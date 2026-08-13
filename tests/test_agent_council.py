import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills" / "cli-agents" / "agent_council.py"
SPEC = importlib.util.spec_from_file_location("agent_council", MODULE_PATH)
agent_council = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(agent_council)


class CouncilTests(unittest.TestCase):
    def test_empty_agents_is_rejected_cleanly(self):
        with mock.patch.object(
            sys,
            "argv",
            [
                "agent_council.py",
                "--mode",
                "panel",
                "--agents",
                ",",
                "--topic",
                "test",
            ],
        ):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaisesRegex(SystemExit, "2"),
            ):
                agent_council.main()

    def test_debate_uses_fresh_calls_and_propagates_limits(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                agent_council, "call_model", return_value="A concrete contribution"
            ) as call,
        ):
            output = Path(tmp) / "debate.md"
            agent_council.run_debate(
                "topic",
                ["codex", "claude-opus"],
                output,
                rounds=1,
                min_len=10,
                cwd=tmp,
                hard_timeout=600,
                idle_timeout=90,
                access="read-only",
            )

        self.assertEqual(call.call_count, 2)
        for invocation in call.call_args_list:
            self.assertNotIn("session", invocation.kwargs)
            self.assertEqual(invocation.kwargs["timeout"], 600)
            self.assertEqual(invocation.kwargs["idle_timeout"], 90)
            self.assertEqual(invocation.kwargs["access"], "read-only")

    def test_panel_propagates_access_to_panelists_and_synthesizer(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                agent_council, "call_model", return_value="answer"
            ) as call,
        ):
            output = Path(tmp) / "panel.md"
            agent_council.run_panel(
                "topic",
                ["codex", "claude-opus"],
                output,
                synth_agent="claude-opus",
                cwd=tmp,
                hard_timeout=700,
                idle_timeout=100,
                access="workspace-write",
            )

        self.assertEqual(call.call_count, 3)
        for invocation in call.call_args_list:
            self.assertEqual(invocation.kwargs["timeout"], 700)
            self.assertEqual(invocation.kwargs["idle_timeout"], 100)
            self.assertEqual(invocation.kwargs["access"], "workspace-write")


if __name__ == "__main__":
    unittest.main()
