#!/usr/bin/env python3
"""
cli-agents CLI Caller
Calls external Claude, Codex, and optional Gemini providers via their CLIs.

Uses bare command names so the shell PATH picks the latest installed versions
(fnm / homebrew / ~/.local/bin) instead of a pinned absolute path that goes
stale after upgrades.
"""

import argparse
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# Model CLI commands configuration. Provider defaults and stable aliases are
# intentional: hard-coded dated model ids make this wrapper stale every few
# months. Use --provider-model when a specific provider model is required.
MODEL_COMMANDS = {
    # Gemini remains an optional compatibility provider. The positional query
    # replaces deprecated -p/--prompt, and --yolo is deliberately not used.
    "gemini": {
        "cmd": ["gemini"],
        "family": "gemini",
        "timeout": 120,
    },
    # Codex uses the current CLI/config default. This preserves local profiles
    # such as gpt-5.6-sol without baking them into the plugin.
    "codex": {
        "cmd": ["codex", "exec", "--skip-git-repo-check"],
        "family": "codex",
        "timeout": 300,
    },
    # Native `codex review` — custom prompt treated as review instructions.
    # Requires cwd to be a trusted git repo (`codex trust-dir <path>` first time).
    "codex-review": {
        "cmd": ["codex", "review"],
        "family": "codex",
        "timeout": 360,
    },
    # `codex review --uncommitted` — review staged/unstaged/untracked changes in cwd.
    "codex-review-uncommitted": {
        "cmd": ["codex", "review", "--uncommitted"],
        "family": "codex",
        "timeout": 360,
    },
    # `codex exec --json` — structured JSONL events (one event per line) for parsing.
    "codex-json": {
        "cmd": ["codex", "exec", "--skip-git-repo-check", "--json"],
        "family": "codex",
        "timeout": 300,
    },
    # Gemini with JSON output — for structured parsing.
    "gemini-json": {
        "cmd": ["gemini", "--output-format", "json"],
        "family": "gemini",
        "timeout": 120,
    },
    "claude": {
        "cmd": ["claude", "--print"],
        "family": "claude",
        "timeout": 120,
    },
    "claude-sonnet": {
        "cmd": ["claude", "--print", "--model", "sonnet"],
        "family": "claude",
        "timeout": 120,
    },
    "claude-opus": {
        "cmd": ["claude", "--print", "--model", "opus"],
        "family": "claude",
        "timeout": 180,
    },
    "claude-haiku": {
        "cmd": ["claude", "--print", "--model", "haiku"],
        "family": "claude",
        "timeout": 90,
    },
}

ACCESS_MODES = ("read-only", "workspace-write", "inherit")
DEFAULT_HARD_TIMEOUT = 1800
IDLE_TIMEOUT_RANGE = (
    min(config["timeout"] for config in MODEL_COMMANDS.values()),
    max(config["timeout"] for config in MODEL_COMMANDS.values()),
)


def load_systemprompt(prompt_name):
    """Load system prompt from systemprompts directory"""
    if not prompt_name:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]+", prompt_name):
        raise ValueError(
            "System prompt name may contain only letters, digits, '_' and '-'"
        )

    script_dir = Path(__file__).parent
    prompt_file = script_dir / "systemprompts" / f"{prompt_name}.txt"

    if not prompt_file.exists():
        available = ", ".join(sorted(p.stem for p in prompt_file.parent.glob("*.txt")))
        raise ValueError(
            f"System prompt '{prompt_name}' not found. Available: {available or '(none)'}"
        )

    return prompt_file.read_text(encoding="utf-8")


def _set_provider_model(cmd, family, provider_model):
    """Override a provider model without exposing dated aliases in MODEL_COMMANDS."""
    if not provider_model:
        return cmd

    flags = ("-m", "--model")
    for flag in flags:
        while flag in cmd:
            i = cmd.index(flag)
            del cmd[i : i + 2]

    flag = "-m" if family == "codex" else "--model"
    cmd.extend([flag, provider_model])
    return cmd


def _apply_access(cmd, family, access):
    """Apply the safe execution profile understood by each provider CLI."""
    if access not in ACCESS_MODES:
        raise ValueError(
            f"Unknown access mode '{access}'. Valid: {', '.join(ACCESS_MODES)}"
        )
    if access == "inherit":
        return cmd

    if family == "claude":
        mode = "plan" if access == "read-only" else "acceptEdits"
        cmd.extend(["--permission-mode", mode])
    elif family == "gemini":
        mode = "default" if access == "read-only" else "auto_edit"
        cmd.extend(["--approval-mode", mode])
    elif family == "codex":
        # `codex exec resume` and `codex review` do not expose the short
        # sandbox/approval flags. Config overrides work across all variants.
        cmd.extend(
            [
                "-c",
                f'sandbox_mode="{access}"',
                "-c",
                'approval_policy="never"',
            ]
        )
    return cmd


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

    fam = MODEL_COMMANDS[model_name]["family"]
    # codex-review* don't accept resume — treat as unsupported.
    if model_name.startswith("codex-review"):
        print(
            f"Warning: session resume not supported for {model_name}; ignoring.",
            file=sys.stderr,
        )
        return cmd

    if fam == "gemini":
        token = "latest" if session in ("last", "latest") else session
        cmd.extend(["--resume", token])
    elif fam == "codex":
        # Resume has a different option surface than fresh `exec`; rebuild the
        # command instead of leaking unsupported --skip-git-repo-check flags.
        if "exec" not in cmd:
            return cmd
        keep_json = "--json" in cmd
        cmd = [cmd[0], "exec", "resume"]
        if session in ("last", "latest"):
            cmd.append("--last")
        else:
            cmd.append(session)
        if keep_json:
            cmd.append("--json")
    elif fam == "claude":
        if session in ("last", "latest"):
            cmd.append("--continue")
        else:
            cmd.extend(["--resume", session])
    return cmd


def build_command(model_name, session=None, access="read-only", provider_model=None):
    """Build a provider command without the final user prompt."""
    if model_name not in MODEL_COMMANDS:
        raise ValueError(f"Unknown model '{model_name}'")
    config = MODEL_COMMANDS[model_name]
    cmd = _apply_session(config["cmd"].copy(), model_name, session)
    cmd = _set_provider_model(cmd, config["family"], provider_model)
    return _apply_access(cmd, config["family"], access)


def _run_with_idle_timeout(cmd, cwd, idle_timeout, hard_timeout):
    """
    Run `cmd` and kill it only if both output streams go silent for `idle_timeout` seconds
    (or total wall time exceeds `hard_timeout`). Long CLI sessions that stream
    progress stay alive as long as they keep writing output.

    Returns (returncode, stdout, stderr, reason) where reason ∈
    {"ok", "idle", "hard", "error"}.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,  # codex 0.121+ hangs forever reading stdin otherwise
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            text=True,
            bufsize=1,  # line-buffered
            start_new_session=True,
        )
    except OSError as exc:
        return (None, "", f"Failed to start {cmd[0]}: {exc}", "error")

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, "stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, "stderr")

    out_buf, err_buf = [], []
    last_activity = time.monotonic()
    deadline = time.monotonic() + hard_timeout
    reason = "ok"

    try:
        while True:
            now = time.monotonic()
            if now > deadline:
                reason = "hard"
                break
            if now - last_activity > idle_timeout:
                reason = "idle"
                break

            wait = min(idle_timeout - (now - last_activity), deadline - now, 5.0)
            events = sel.select(timeout=max(wait, 0.1))
            got_data = False
            for key, _ in events:
                chunk = key.fileobj.readline()
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                got_data = True
                if key.data == "stdout":
                    out_buf.append(chunk)
                else:
                    err_buf.append(chunk)
            if got_data:
                last_activity = time.monotonic()

            # All streams closed → process is done.
            if not sel.get_map() and proc.poll() is not None:
                break
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        # Drain anything left.
        try:
            tail_out, tail_err = proc.communicate(timeout=2)
            if tail_out:
                out_buf.append(tail_out)
            if tail_err:
                err_buf.append(tail_err)
        except Exception as exc:
            err_buf.append(f"Failed to drain provider output after termination: {exc}")
            reason = "error"

    return (proc.returncode, "".join(out_buf), "".join(err_buf), reason)


def call_model(
    model_name,
    prompt,
    systemprompt=None,
    timeout=None,
    cwd=None,
    session=None,
    idle_timeout=None,
    access="read-only",
    provider_model=None,
):
    """Call AI model via CLI. Pass session to resume a previous conversation.

    timeout: hard wall-clock deadline (default DEFAULT_HARD_TIMEOUT).
    idle_timeout: kill if stdout and stderr are silent this long (per-model default).
    A long review that keeps streaming output stays alive until it finishes.
    """
    if model_name not in MODEL_COMMANDS:
        print(f"Error: Unknown model '{model_name}'", file=sys.stderr)
        print(f"Available models: {', '.join(MODEL_COMMANDS.keys())}", file=sys.stderr)
        return None

    config = MODEL_COMMANDS[model_name]
    try:
        cmd = build_command(model_name, session, access, provider_model)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None

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

    if cwd:
        cwd_path = Path(cwd).expanduser()
        if not cwd_path.is_dir():
            print(
                f"Error: working directory does not exist: {cwd_path}", file=sys.stderr
            )
            return None
        cwd = str(cwd_path.resolve())

    # Combine system prompt with user prompt if provided
    full_prompt = prompt
    if systemprompt:
        try:
            systemprompt_text = load_systemprompt(systemprompt)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return None
        full_prompt = f"{systemprompt_text}\n\n---\n\nUser Request:\n{prompt}"

    # Add prompt to command. `codex review --uncommitted` rejects a positional PROMPT,
    # so skip appending when model is that variant and the prompt is empty.
    if not (model_name == "codex-review-uncommitted" and not full_prompt.strip()):
        cmd.append(full_prompt)

    # hard_timeout: absolute wall-clock cap. Default 30 min — forgiving for long reviews.
    # idle_timeout: kill only if both output streams are silent this long.
    hard = timeout if timeout else DEFAULT_HARD_TIMEOUT
    idle = idle_timeout if idle_timeout else config["timeout"]

    rc, out, err, reason = _run_with_idle_timeout(cmd, cwd, idle, hard)

    if reason == "error":
        print(f"Error calling {model_name}: {err}", file=sys.stderr)
        return None
    if reason == "idle":
        print(
            f"Error: {model_name} silent for {idle}s (output idle timeout). "
            f"Partial output below. Override with --idle-timeout.",
            file=sys.stderr,
        )
        if out:
            return out
        return None
    if reason == "hard":
        print(
            f"Error: {model_name} exceeded hard timeout of {hard}s. "
            f"Override with --timeout.",
            file=sys.stderr,
        )
        if out:
            return out
        return None
    if rc != 0:
        print(f"Error calling {model_name} (exit {rc}):", file=sys.stderr)
        if err:
            print(err, file=sys.stderr)
        return None
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Call AI models directly via CLI without MCP overhead"
    )
    parser.add_argument(
        "--model",
        required=True,
        choices=list(MODEL_COMMANDS.keys()),
        help=(
            "Wrapper profile. Core: codex, codex-review, codex-review-uncommitted, "
            "codex-json, claude, claude-sonnet, claude-opus, claude-haiku. "
            "Optional compatibility: gemini, gemini-json."
        ),
    )
    parser.add_argument(
        "--provider-model",
        help=(
            "Explicit provider model id. Omit to use the provider default or the "
            "stable alias selected by --model."
        ),
    )
    parser.add_argument(
        "--prompt", help="Prompt to send to the model (required unless using --info)"
    )
    parser.add_argument(
        "--systemprompt",
        help=(
            "System prompt file stem from systemprompts/ (for example default, "
            "default_planner, default_codereviewer, codex_codereviewer)."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help=(
            f"Hard wall-clock cap in seconds (default {DEFAULT_HARD_TIMEOUT}). "
            "The process is NOT killed as long as it streams output; this is only "
            "the ceiling. For ordinary sizing use --idle-timeout."
        ),
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        help=(
            "Kill the call if stdout and stderr are silent this long (default per model, "
            f"currently {IDLE_TIMEOUT_RANGE[0]}-{IDLE_TIMEOUT_RANGE[1]}s). "
            "A streaming review that keeps emitting output "
            "stays alive indefinitely (up to --timeout)."
        ),
    )
    parser.add_argument("--info", action="store_true", help="Show model information")
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
        help="Working directory for model execution (enables file access in that directory)",
    )
    parser.add_argument(
        "--access",
        choices=ACCESS_MODES,
        default="read-only",
        help=(
            "Provider tool-access profile (default: read-only). Use workspace-write "
            "only for an explicitly authorized implementation task; inherit keeps "
            "the provider CLI configuration unchanged."
        ),
    )

    args = parser.parse_args()

    # Validate arguments. `codex review` variants accept empty prompt (instructions optional).
    review_models = {"codex-review", "codex-review-uncommitted"}
    if not args.info and args.prompt is None and args.model not in review_models:
        parser.error(
            "--prompt is required unless using --info or a codex-review variant"
        )
    if args.prompt is None:
        args.prompt = ""

    # Show model info if requested
    if args.info:
        cmd = build_command(
            args.model,
            session=args.session,
            access=args.access,
            provider_model=args.provider_model,
        )
        binary = shutil.which(cmd[0])
        print(f"\nProfile: {args.model}")
        print(f"Family: {MODEL_COMMANDS[args.model]['family']}")
        print(f"CLI: {binary or 'not found'}")
        print(f"Command: {' '.join(cmd)}")
        print(f"Default idle timeout: {MODEL_COMMANDS[args.model]['timeout']}s")
        print(f"Default hard timeout: {DEFAULT_HARD_TIMEOUT}s")
        return 0

    # Call the model
    result = call_model(
        args.model,
        args.prompt,
        args.systemprompt,
        args.timeout,
        args.cwd,
        args.session,
        args.idle_timeout,
        args.access,
        args.provider_model,
    )

    if result is not None:
        print(result)
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
