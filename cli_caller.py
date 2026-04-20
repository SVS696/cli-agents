#!/usr/bin/env python3
"""
AI Models Direct CLI Caller
Calls AI models directly via CLI without MCP overhead
Supports: Gemini (1M), Codex (400k), Qwen (256k+), Claude (200k-1M)

Uses bare command names so the shell PATH picks the latest installed versions
(fnm / homebrew / ~/.local/bin) instead of a pinned absolute path that goes
stale after upgrades.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Model CLI commands configuration.
# Commands use bare names resolved via PATH so upgrades (fnm, homebrew, ~/.local/bin)
# are picked up automatically. Non-interactive flags:
#   gemini   -> -p/--prompt (headless); positional query triggers interactive mode
#   codex    -> `exec` subcommand
#   qwen     -> positional query (defaults to one-shot since 0.3.0)
#   claude   -> --print
MODEL_COMMANDS = {
    # Gemini auto — default model (currently gemini-3-pro-preview → gemini-2.5-pro fallback)
    "gemini": {
        "cmd": ["gemini", "--yolo", "-p"],
        "timeout": 120,
        "context_window": "1M tokens",
    },
    "gemini-3-pro": {
        "cmd": ["gemini", "--yolo", "-m", "gemini-3-pro-preview", "-p"],
        "timeout": 120,
        "context_window": "1M tokens",
    },
    "gemini-2.5-pro": {
        "cmd": ["gemini", "--yolo", "-m", "gemini-2.5-pro", "-p"],
        "timeout": 120,
        "context_window": "1M tokens",
    },
    "gemini-2.5-flash": {
        "cmd": ["gemini", "--yolo", "-m", "gemini-2.5-flash", "-p"],
        "timeout": 60,
        "context_window": "1M tokens",
    },
    "gemini-2.5-flash-lite": {
        "cmd": ["gemini", "--yolo", "-m", "gemini-2.5-flash-lite", "-p"],
        "timeout": 60,
        "context_window": "1M tokens",
    },
    # Codex 0.121+ defaults to gpt-5.4. Other available: gpt-5-codex, gpt-5.1-codex.
    "codex": {
        "cmd": ["codex", "exec", "--skip-git-repo-check"],
        "timeout": 120,
        "context_window": "400k tokens",
    },
    "codex-gpt-5-codex": {
        "cmd": ["codex", "exec", "--skip-git-repo-check", "-m", "gpt-5-codex"],
        "timeout": 120,
        "context_window": "400k tokens",
    },
    "codex-gpt-5.1-codex": {
        "cmd": ["codex", "exec", "--skip-git-repo-check", "-m", "gpt-5.1-codex"],
        "timeout": 120,
        "context_window": "400k tokens",
    },
    # Native `codex review` — custom prompt treated as review instructions.
    # Requires cwd to be a trusted git repo (`codex trust-dir <path>` first time).
    "codex-review": {
        "cmd": ["codex", "review"],
        "timeout": 180,
        "context_window": "400k tokens",
    },
    # `codex review --uncommitted` — review staged/unstaged/untracked changes in cwd.
    "codex-review-uncommitted": {
        "cmd": ["codex", "review", "--uncommitted"],
        "timeout": 180,
        "context_window": "400k tokens",
    },
    # `codex exec --json` — structured JSONL events (one event per line) for parsing.
    "codex-json": {
        "cmd": ["codex", "exec", "--skip-git-repo-check", "--json"],
        "timeout": 180,
        "context_window": "400k tokens",
    },
    # Gemini with JSON output — for structured parsing.
    "gemini-json": {
        "cmd": ["gemini", "--yolo", "-o", "json", "-p"],
        "timeout": 120,
        "context_window": "1M tokens",
    },
    "qwen": {
        "cmd": ["qwen", "--yolo"],
        "timeout": 60,
        "context_window": "256k tokens",
    },
    "claude": {
        "cmd": ["claude", "--print"],
        "timeout": 120,
        "context_window": "200k tokens",
    },
    "claude-sonnet": {
        "cmd": ["claude", "--print", "--model", "claude-sonnet-4-6"],
        "timeout": 120,
        "context_window": "200k tokens",
    },
    "claude-opus": {
        "cmd": ["claude", "--print", "--model", "claude-opus-4-7"],
        "timeout": 180,
        "context_window": "200k tokens (1M beta tier)",
    },
    "claude-haiku": {
        "cmd": ["claude", "--print", "--model", "claude-haiku-4-5-20251001"],
        "timeout": 90,
        "context_window": "200k tokens",
    },
}

def load_systemprompt(prompt_name):
    """Load system prompt from systemprompts directory"""
    if not prompt_name:
        return None

    script_dir = Path(__file__).parent
    prompt_file = script_dir / "systemprompts" / f"{prompt_name}.txt"

    if not prompt_file.exists():
        print(f"Warning: System prompt '{prompt_name}' not found at {prompt_file}", file=sys.stderr)
        return None

    return prompt_file.read_text()

def _apply_session(cmd, model_name, session):
    """
    Inject resume-session flags into `cmd` for the given model family.

    session values:
      - None / "new"        → no-op (fresh session)
      - "last" / "latest"   → continue most recent session
      - "<id>"              → resume specific session by id/index

    Supported only for gemini*, codex* (exec variants), claude*. For review
    variants resume is not supported — the native `codex review` always
    starts a fresh review.
    """
    if not session or session == "new":
        return cmd

    fam = model_name.split("-")[0]
    # codex-review* don't accept resume — treat as unsupported.
    if model_name.startswith("codex-review"):
        print(
            f"Warning: session resume not supported for {model_name}; ignoring.",
            file=sys.stderr,
        )
        return cmd

    if fam == "gemini":
        token = "latest" if session in ("last", "latest") else session
        # gemini already has "-p" at end of base cmd; insert -r before it.
        if "-p" in cmd:
            i = cmd.index("-p")
            cmd[i:i] = ["-r", token]
        else:
            cmd.extend(["-r", token])
    elif fam == "codex":
        # `codex exec resume [--last | <id>]` — rewrite cmd to insert the resume
        # subcommand right after `exec`.
        try:
            i = cmd.index("exec")
        except ValueError:
            return cmd
        insert = ["resume"]
        if session in ("last", "latest"):
            insert.append("--last")
        else:
            insert.append(session)
        cmd[i + 1 : i + 1] = insert
    elif fam == "claude":
        if session in ("last", "latest"):
            cmd.append("--continue")
        else:
            cmd.extend(["--resume", session])
    return cmd


def call_model(
    model_name, prompt, systemprompt=None, timeout=None, cwd=None, session=None
):
    """Call AI model via CLI. Pass session to resume a previous conversation."""
    if model_name not in MODEL_COMMANDS:
        print(f"Error: Unknown model '{model_name}'", file=sys.stderr)
        print(f"Available models: {', '.join(MODEL_COMMANDS.keys())}", file=sys.stderr)
        return None

    config = MODEL_COMMANDS[model_name]
    cmd = config["cmd"].copy()

    # Resolve binary via PATH; fail early with a clear message if missing.
    resolved = shutil.which(cmd[0])
    if not resolved:
        print(
            f"Error: {cmd[0]} CLI not found in PATH. "
            f"Install it or ensure the shell PATH is inherited.",
            file=sys.stderr,
        )
        return None
    cmd[0] = resolved

    cmd = _apply_session(cmd, model_name, session)

    # Combine system prompt with user prompt if provided
    full_prompt = prompt
    if systemprompt:
        systemprompt_text = load_systemprompt(systemprompt)
        if systemprompt_text:
            full_prompt = f"{systemprompt_text}\n\n---\n\nUser Request:\n{prompt}"

    # Add prompt to command. `codex review --uncommitted` rejects a positional PROMPT,
    # so skip appending when model is that variant and the prompt is empty.
    if not (model_name == "codex-review-uncommitted" and not full_prompt.strip()):
        cmd.append(full_prompt)

    # Use provided timeout or default
    timeout_value = timeout if timeout else config["timeout"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_value,
            cwd=cwd
        )

        if result.returncode != 0:
            print(f"Error calling {model_name}:", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            return None

        return result.stdout

    except subprocess.TimeoutExpired:
        print(f"Error: {model_name} call timed out after {timeout_value}s", file=sys.stderr)
        return None
    except FileNotFoundError:
        print(f"Error: {model_name} CLI not found. Install it first.", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error calling {model_name}: {e}", file=sys.stderr)
        return None

def main():
    parser = argparse.ArgumentParser(
        description="Call AI models directly via CLI without MCP overhead"
    )
    parser.add_argument(
        "--model",
        required=True,
        choices=list(MODEL_COMMANDS.keys()),
        help=(
            "AI model to use. Gemini: gemini (auto), gemini-3-pro, gemini-2.5-pro, "
            "gemini-2.5-flash, gemini-2.5-flash-lite. Codex: codex (gpt-5.4 default), "
            "codex-gpt-5-codex, codex-gpt-5.1-codex. Qwen: qwen. "
            "Claude: claude, claude-sonnet (4.6), claude-opus (4.7), claude-haiku (4.5)."
        )
    )
    parser.add_argument(
        "--prompt",
        help="Prompt to send to the model (required unless using --info)"
    )
    parser.add_argument(
        "--systemprompt",
        help="System prompt to prepend (default, planner, codereviewer, codex_codereviewer)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="Timeout in seconds (default: 30)"
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Show model information"
    )
    parser.add_argument(
        "--session",
        help=(
            "Resume a previous session for multi-turn discussion. "
            "Values: 'new' (default — fresh), 'last'/'latest' (most recent), "
            "or a session id (UUID for codex, index for gemini, session id for claude). "
            "Not supported for codex-review variants."
        ),
    )
    parser.add_argument(
        "--cwd",
        help="Working directory for model execution (enables file access in that directory)"
    )

    args = parser.parse_args()

    # Validate arguments. `codex review` variants accept empty prompt (instructions optional).
    review_models = {"codex-review", "codex-review-uncommitted"}
    if not args.info and args.prompt is None and args.model not in review_models:
        parser.error("--prompt is required unless using --info or a codex-review variant")
    if args.prompt is None:
        args.prompt = ""

    # Show model info if requested
    if args.info:
        print(f"\nModel: {args.model}")
        print(f"Command: {' '.join(MODEL_COMMANDS[args.model]['cmd'])}")
        print(f"Context Window: {MODEL_COMMANDS[args.model]['context_window']}")
        print(f"Default Timeout: {MODEL_COMMANDS[args.model]['timeout']}s")
        return 0

    # Call the model
    result = call_model(
        args.model,
        args.prompt,
        args.systemprompt,
        args.timeout,
        args.cwd,
        args.session,
    )

    if result:
        print(result)
        return 0
    else:
        return 1

if __name__ == "__main__":
    sys.exit(main())
