#!/usr/bin/env python3
"""
Agent Council — multi-model discussion orchestrator.

Two modes:

  debate  : Sequential A→B→C rounds. A shared markdown file is the source of
            truth; each turn is fresh and receives the complete transcript.
            This avoids `--session last` cross-talk between provider aliases.

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
from cli_caller import DEFAULT_HARD_TIMEOUT, MODEL_COMMANDS, call_model  # noqa: E402

STOP_TOKEN = "CONCLUDED"
DEFAULT_MIN_LEN = 40  # chars; below this we treat the turn as "nothing to add"

DEBATE_PREAMBLE = """Role: Independent participant in a multi-agent technical debate.

Goal: Advance the shared question with one material contribution that is not already
present in the transcript.

Contract:
- Read the full discussion before answering.
- Add one argument, counterexample, proof request, refinement, or concrete proposal.
- Ground repository claims in available file:line evidence. Label inference and missing
  evidence instead of inventing facts.
- Do not modify files or impersonate another participant.
- If no material contribution remains, reply with the single line {stop}

Output: Only the new contribution, with no recap of the thread.
Your participant label: **{role}**
"""

PANEL_PREAMBLE = """Role: Independent expert advisor in a multi-model panel.

Goal: Give a concrete answer to the question without seeing or anticipating the other
panelists' answers.

Contract:
- Use available evidence and cite file:line when repository facts matter.
- State material assumptions, conflicts, or missing evidence.
- Make a recommendation when evidence supports one; otherwise identify the smallest
  fact needed to choose safely.
- Do not modify files.

Output: Recommendation first, then the decisive evidence and caveats. Omit generic
background and do not discuss the panel process.
"""

SYNTH_PREAMBLE = """Role: Synthesizer of {n} independent expert answers.

Goal: Resolve the original question while preserving material agreement, disagreement,
evidence gaps, and uncertainty from the source answers.

Output exactly these Markdown sections:
## Consensus
## Divergence
## Recommendation

Attribute positions by participant label when useful. Do not invent facts or treat a
majority vote as evidence. If the recommendation depends on missing evidence, name the
smallest deciding check. Do not quote answers at length.
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
    hard_timeout: int,
    idle_timeout: int | None,
    access: str,
) -> Path:
    output.write_text(
        f"# Debate: {topic}\n\n"
        f"_Started {_dt.datetime.now().isoformat(timespec='seconds')}_\n"
        f"_Participants: {', '.join(agents)}_\n\n",
        encoding="utf-8",
    )
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
                timeout=hard_timeout,
                idle_timeout=idle_timeout,
                cwd=cwd,
                access=access,
            )

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

    _append(
        output,
        f"\n---\n_Debate ended {_dt.datetime.now().isoformat(timespec='seconds')}_\n",
    )
    return output


def run_panel(
    topic: str,
    agents: list[str],
    output: Path,
    synth_agent: str,
    cwd: str | None,
    hard_timeout: int,
    idle_timeout: int | None,
    access: str,
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
        return agent, call_model(
            agent,
            prompt,
            timeout=hard_timeout,
            idle_timeout=idle_timeout,
            cwd=cwd,
            access=access,
        )

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
    synth = (
        call_model(
            synth_agent,
            synth_prompt,
            timeout=hard_timeout,
            idle_timeout=idle_timeout,
            cwd=cwd,
            access=access,
        )
        or "_(synth error)_"
    )

    _append(output, "\n## Synthesis\n")
    _append(output, synth.strip() + "\n")
    _append(
        output,
        f"\n---\n_Panel ended {_dt.datetime.now().isoformat(timespec='seconds')}_\n",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Multi-agent council: debate or panel."
    )
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
    parser.add_argument(
        "--rounds", type=int, default=5, help="[debate] max rounds (default 5)"
    )
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
    parser.add_argument(
        "--hard-timeout",
        "--timeout",
        dest="hard_timeout",
        type=int,
        default=DEFAULT_HARD_TIMEOUT,
        help=(
            "Per-call hard wall-clock cap in seconds "
            f"(default {DEFAULT_HARD_TIMEOUT}; --timeout is a compatibility alias)"
        ),
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        help="Per-call output-idle timeout; omit to use each model profile default",
    )
    parser.add_argument(
        "--access",
        choices=["read-only", "workspace-write", "inherit"],
        default="read-only",
        help="Provider tool-access profile (default read-only)",
    )

    args = parser.parse_args()

    if not args.topic and not args.topic_file:
        parser.error("Provide --topic or --topic-file")
    topic = args.topic or Path(args.topic_file).read_text(encoding="utf-8").strip()

    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    if not agents:
        parser.error("Provide at least one agent in --agents")
    unknown = [a for a in agents if a not in MODEL_COMMANDS]
    if unknown:
        parser.error(f"Unknown agents: {unknown}")

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.mode == "debate":
        run_debate(
            topic,
            agents,
            output,
            args.rounds,
            args.min_len,
            args.cwd,
            args.hard_timeout,
            args.idle_timeout,
            args.access,
        )
    else:
        if args.synthesize_with not in MODEL_COMMANDS:
            parser.error(f"Unknown synthesizer: {args.synthesize_with}")
        run_panel(
            topic,
            agents,
            output,
            args.synthesize_with,
            args.cwd,
            args.hard_timeout,
            args.idle_timeout,
            args.access,
        )

    print(str(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
