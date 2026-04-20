#!/usr/bin/env python3
"""
Agent Council — multi-model discussion orchestrator.

Two modes:

  debate  : Sequential A→B→C rounds. A shared markdown file is the source of
            truth; each agent reads it and appends its turn. Each agent also
            keeps its own `--session last` so its own thread stays cheap.

  panel   : Parallel one-shot. Every agent answers the topic independently,
            then a synthesizer model reads all answers and produces a
            consensus/diff summary.

Stop signals (debate): an agent sends "CONCLUDED" OR its turn gets shorter
than --min-len twice in a row, OR the round cap is reached.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import sys
from pathlib import Path

# Reuse the single-call wrapper.
sys.path.insert(0, str(Path(__file__).parent))
from cli_caller import call_model, MODEL_COMMANDS  # noqa: E402

STOP_TOKEN = "CONCLUDED"
DEFAULT_MIN_LEN = 40  # chars; below this we treat the turn as "nothing to add"

DEBATE_PREAMBLE = """You are participating in a multi-agent technical debate.
Ground rules:
  1. Read the full discussion below. Do NOT repeat points already made.
  2. Add exactly ONE new contribution per turn: a concrete argument, counter-point,
     proof request, refinement, or concrete proposal. Cite file:line if relevant.
  3. If you genuinely have nothing new to add — reply with a single line:
     {stop}
  4. Be terse. Prefer 3-10 sentences over walls of text.
  5. Do not impersonate other participants.

Your role in this debate: **{role}**
"""

PANEL_PREAMBLE = """You are one of several expert advisors answering the same question
independently. Give your best concrete answer. Be terse (3-10 sentences).
Cite file:line if relevant. Do not hedge between options — take a position.
"""

SYNTH_PREAMBLE = """You are a synthesizer. Below are {n} independent expert answers to
the same question. Produce a consensus report with three sections:

  ## Consensus   — points where all/most agree
  ## Divergence  — where they disagree, and why
  ## Recommendation — your own call, given the above

Be terse. Do not quote the experts verbatim; attribute by name in parentheses
when useful (e.g. "prefer pg_partman (gemini, codex)").
"""


def _ts() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def run_debate(
    topic: str,
    agents: list[str],
    output: Path,
    rounds: int,
    min_len: int,
    cwd: str | None,
    timeout: int,
) -> Path:
    output.write_text(
        f"# Debate: {topic}\n\n"
        f"_Started {_dt.datetime.now().isoformat(timespec='seconds')}_\n"
        f"_Participants: {', '.join(agents)}_\n\n",
        encoding="utf-8",
    )
    # Use each agent's OWN session chain so it remembers its own turns cheaply;
    # shared context comes from reading the file each turn.
    sessions = {a: None for a in agents}  # None → fresh, later "last"
    short_streak = {a: 0 for a in agents}
    concluded = set()

    for r in range(1, rounds + 1):
        print(f"[{_ts()}] --- Round {r}/{rounds} ---", file=sys.stderr)
        _append(output, f"\n## Round {r}\n")
        made_progress = False

        for agent in agents:
            if agent in concluded:
                continue

            role = f"{agent} (round {r})"
            prompt = (
                DEBATE_PREAMBLE.format(stop=STOP_TOKEN, role=role)
                + "\n---\nCurrent discussion (full file below):\n---\n"
                + output.read_text(encoding="utf-8")
                + "\n---\nWrite ONLY your new contribution. Do not restate the thread."
            )

            print(f"[{_ts()}] → {agent}", file=sys.stderr)
            reply = call_model(
                agent,
                prompt,
                systemprompt=None,
                timeout=timeout,
                cwd=cwd,
                session=sessions[agent],
            )
            sessions[agent] = "last"  # next turn resumes its own chain

            if reply is None:
                _append(output, f"\n### {agent}\n_(error — skipped)_\n")
                continue

            reply = reply.strip()
            is_stop = STOP_TOKEN in reply.upper() and len(reply) < 200
            is_short = len(reply) < min_len

            if is_short:
                short_streak[agent] += 1
            else:
                short_streak[agent] = 0
                made_progress = True

            _append(output, f"\n### {agent}\n{reply}\n")

            if is_stop or short_streak[agent] >= 2:
                concluded.add(agent)
                _append(output, f"_{agent} concluded._\n")
                print(f"[{_ts()}] {agent} concluded", file=sys.stderr)

        if len(concluded) == len(agents):
            print(f"[{_ts()}] All agents concluded — stopping early.", file=sys.stderr)
            break
        if not made_progress:
            print(f"[{_ts()}] No progress this round — stopping.", file=sys.stderr)
            break

    _append(output, f"\n---\n_Debate ended {_dt.datetime.now().isoformat(timespec='seconds')}_\n")
    return output


def run_panel(
    topic: str,
    agents: list[str],
    output: Path,
    synth_agent: str,
    cwd: str | None,
    timeout: int,
) -> Path:
    output.write_text(
        f"# Panel: {topic}\n\n"
        f"_Started {_dt.datetime.now().isoformat(timespec='seconds')}_\n"
        f"_Panelists: {', '.join(agents)} — synthesizer: {synth_agent}_\n\n",
        encoding="utf-8",
    )

    def one(agent: str) -> tuple[str, str | None]:
        prompt = f"{PANEL_PREAMBLE}\n---\nQuestion:\n{topic}"
        print(f"[{_ts()}] → {agent}", file=sys.stderr)
        return agent, call_model(agent, prompt, timeout=timeout, cwd=cwd)

    # Fan out in parallel — agents are independent here.
    answers: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as ex:
        for agent, reply in ex.map(one, agents):
            answers[agent] = (reply or "_(error)_").strip()

    _append(output, "## Individual Answers\n")
    for agent, reply in answers.items():
        _append(output, f"\n### {agent}\n{reply}\n")

    synth_prompt = (
        SYNTH_PREAMBLE.format(n=len(answers))
        + "\n---\nQuestion:\n"
        + topic
        + "\n---\nAnswers:\n"
        + "\n".join(f"\n### {a}\n{r}" for a, r in answers.items())
    )
    print(f"[{_ts()}] → synthesizer {synth_agent}", file=sys.stderr)
    synth = call_model(synth_agent, synth_prompt, timeout=timeout, cwd=cwd) or "_(synth error)_"

    _append(output, "\n## Synthesis\n")
    _append(output, synth.strip() + "\n")
    _append(output, f"\n---\n_Panel ended {_dt.datetime.now().isoformat(timespec='seconds')}_\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-agent council: debate or panel.")
    parser.add_argument("--mode", required=True, choices=["debate", "panel"])
    parser.add_argument(
        "--agents",
        required=True,
        help=f"Comma-separated model names. Valid: {','.join(MODEL_COMMANDS.keys())}",
    )
    parser.add_argument("--topic", help="Question/topic for the council")
    parser.add_argument("--topic-file", help="Read topic from this file")
    parser.add_argument(
        "--output",
        default="discussion.md",
        help="Markdown file to write/append the transcript to (default: discussion.md)",
    )
    parser.add_argument("--rounds", type=int, default=5, help="[debate] max rounds (default 5)")
    parser.add_argument(
        "--min-len",
        type=int,
        default=DEFAULT_MIN_LEN,
        help=f"[debate] turns shorter than this count as 'nothing to add' (default {DEFAULT_MIN_LEN})",
    )
    parser.add_argument(
        "--synthesize-with",
        default="claude-opus",
        help="[panel] model used to synthesize (default claude-opus)",
    )
    parser.add_argument("--cwd", help="Working directory for all agents")
    parser.add_argument("--timeout", type=int, default=180, help="Per-call timeout in seconds")

    args = parser.parse_args()

    if not args.topic and not args.topic_file:
        parser.error("Provide --topic or --topic-file")
    topic = args.topic or Path(args.topic_file).read_text(encoding="utf-8").strip()

    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    unknown = [a for a in agents if a not in MODEL_COMMANDS]
    if unknown:
        parser.error(f"Unknown agents: {unknown}")

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.mode == "debate":
        run_debate(topic, agents, output, args.rounds, args.min_len, args.cwd, args.timeout)
    else:
        if args.synthesize_with not in MODEL_COMMANDS:
            parser.error(f"Unknown synthesizer: {args.synthesize_with}")
        run_panel(topic, agents, output, args.synthesize_with, args.cwd, args.timeout)

    print(str(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
