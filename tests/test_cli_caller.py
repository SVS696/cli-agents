import contextlib
import importlib.util
import io
import sys
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills" / "cli-agents" / "cli_caller.py"
SPEC = importlib.util.spec_from_file_location("cli_caller", MODULE_PATH)
cli_caller = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(cli_caller)


class CommandBuilderTests(unittest.TestCase):
    def test_core_and_optional_profiles_are_explicit(self):
        self.assertEqual(
            set(cli_caller.MODEL_COMMANDS),
            {
                "gemini",
                "gemini-json",
                "codex",
                "codex-review",
                "codex-review-uncommitted",
                "codex-json",
                "claude",
                "claude-sonnet",
                "claude-opus",
                "claude-haiku",
            },
        )

    def test_codex_is_read_only_by_default(self):
        cmd = cli_caller.build_command("codex")
        rendered = " ".join(cmd)
        self.assertIn('sandbox_mode="read-only"', rendered)
        self.assertIn('approval_policy="never"', rendered)
        self.assertNotIn("dangerously-bypass", rendered)

    def test_claude_uses_stable_alias_and_plan_mode(self):
        cmd = cli_caller.build_command("claude-opus")
        self.assertEqual(cmd[:4], ["claude", "--print", "--model", "opus"])
        self.assertIn("--permission-mode", cmd)
        self.assertIn("plan", cmd)
        self.assertIn("--safe-mode", cmd)
        self.assertIn("--disallowedTools=Edit,Write,NotebookEdit", cmd)

    def test_workspace_write_is_explicit(self):
        claude_cmd = cli_caller.build_command("claude", access="workspace-write")
        codex_cmd = cli_caller.build_command("codex", access="workspace-write")
        self.assertEqual(claude_cmd[-1], "acceptEdits")
        self.assertIn('sandbox_mode="workspace-write"', codex_cmd)

    def test_inherit_adds_no_access_flags(self):
        self.assertEqual(
            cli_caller.build_command("claude-opus", access="inherit"),
            ["claude", "--print", "--model", "opus"],
        )

    def test_provider_model_replaces_stable_alias(self):
        cmd = cli_caller.build_command(
            "claude-opus", access="inherit", provider_model="claude-example"
        )
        self.assertEqual(cmd, ["claude", "--print", "--model", "claude-example"])

    def test_claude_stream_uses_partial_stream_json(self):
        cmd = cli_caller.build_command("claude-opus", stream=True)
        self.assertIn("stream-json", cmd)
        self.assertIn("--include-partial-messages", cmd)
        self.assertIn("--verbose", cmd)

    def test_codex_stream_uses_jsonl(self):
        cmd = cli_caller.build_command("codex", stream=True)
        self.assertIn("--json", cmd)

    def test_native_codex_review_stream_is_raw(self):
        cmd = cli_caller.build_command("codex-review", stream=True)
        self.assertNotIn("--json", cmd)
        self.assertFalse(
            cli_caller._profile_uses_structured_stream("codex-review", True)
        )

    def test_gemini_avoids_yolo_and_deprecated_prompt_flag(self):
        cmd = cli_caller.build_command("gemini")
        self.assertNotIn("--yolo", cmd)
        self.assertNotIn("-p", cmd)
        self.assertEqual(cmd[-2:], ["--approval-mode", "default"])

    def test_codex_resume_drops_fresh_exec_only_flags(self):
        cmd = cli_caller.build_command("codex", session="last", access="inherit")
        self.assertEqual(cmd, ["codex", "exec", "resume", "--last"])

    def test_codex_json_resume_preserves_json(self):
        cmd = cli_caller.build_command(
            "codex-json", session="thread-id", access="inherit"
        )
        self.assertEqual(cmd, ["codex", "exec", "resume", "thread-id", "--json"])


class RuntimeTests(unittest.TestCase):
    class FakeStdout(io.StringIO):
        def __init__(self, is_tty):
            super().__init__()
            self._is_tty = is_tty

        def isatty(self):
            return self._is_tty

    def test_claude_stream_result_extraction(self):
        events = "\n".join(
            [
                '{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"partial"}}}',
                '{"type":"result","result":"final answer"}',
            ]
        )
        self.assertEqual(
            cli_caller._extract_stream_result("claude", events), "final answer"
        )

    def test_codex_stream_result_extraction(self):
        events = "\n".join(
            [
                '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
                '{"type":"item.completed","item":{"type":"agent_message","text":"final"}}',
            ]
        )
        self.assertEqual(cli_caller._extract_stream_result("codex", events), "final")

    def test_stream_result_extraction_ignores_scalar_json(self):
        claude = "\n".join(
            [
                '"use strict"',
                '["not", "an", "event"]',
                '{"type":"result","result":"claude final"}',
            ]
        )
        codex = "\n".join(
            [
                "null",
                "42",
                '{"type":"item.completed","item":{"type":"agent_message","text":"codex final"}}',
            ]
        )
        self.assertEqual(
            cli_caller._extract_stream_result("claude", claude), "claude final"
        )
        self.assertEqual(
            cli_caller._extract_stream_result("codex", codex), "codex final"
        )

    def test_stream_parsers_tolerate_malformed_nested_fields(self):
        events = [
            '{"type":"stream_event","event":"unexpected"}\n',
            '{"type":"stream_event","event":{"delta":{"type":"text_delta","text":123}}}\n',
            '{"type":"assistant","message":{"content":[null,"text"]}}\n',
            '{"type":"result","result":42}\n',
            '{"type":"item.completed","item":null}\n',
        ]
        state = {
            "emitted_text": False,
            "last_char": "",
            "live_text_to_stdout": True,
        }
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            for event in events[:4]:
                cli_caller._emit_live_line("claude", event, "stdout", state)
            cli_caller._emit_live_line("codex", events[4], "stdout", state)
        joined = "".join(events)
        self.assertEqual(cli_caller._extract_stream_result("claude", joined), "")
        self.assertEqual(cli_caller._extract_stream_result("codex", joined), "")
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_call_model_falls_back_to_raw_unrecognized_stream(self):
        raw = '{"type":"future.schema","payload":{"answer":"ok"}}\n'
        with (
            mock.patch.object(cli_caller.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(
                cli_caller,
                "_run_with_idle_timeout",
                return_value=(0, raw, "", "ok"),
            ),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            result = cli_caller.call_model("claude", "test", stream=True)
        self.assertEqual(result, raw)
        self.assertIn("no recognized final stream event", stderr.getvalue())

    def test_empty_recognized_final_does_not_fall_back_to_raw_json(self):
        cases = {
            "claude": '{"type":"result","result":""}\n',
            "codex": (
                '{"type":"item.completed","item":'
                '{"type":"agent_message","text":""}}\n'
            ),
        }
        for model, raw in cases.items():
            with self.subTest(model=model):
                with (
                    mock.patch.object(
                        cli_caller.shutil, "which", return_value=f"/usr/bin/{model}"
                    ),
                    mock.patch.object(
                        cli_caller,
                        "_run_with_idle_timeout",
                        return_value=(0, raw, "", "ok"),
                    ),
                    contextlib.redirect_stderr(io.StringIO()) as stderr,
                ):
                    result = cli_caller.call_model(model, "test", stream=True)
                self.assertEqual(result, "")
                self.assertNotIn(
                    "no recognized final stream event", stderr.getvalue()
                )

    def test_claude_live_renderer_emits_text_not_raw_json(self):
        event = (
            '{"type":"stream_event","event":{"delta":'
            '{"type":"text_delta","text":"hello"}}}\n'
        )
        state = {
            "emitted_text": False,
            "last_char": "",
            "live_text_to_stdout": True,
        }
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli_caller._emit_live_line("claude", event, "stdout", state)
        self.assertEqual(stdout.getvalue(), "hello")
        self.assertTrue(state["emitted_text"])

    def test_scalar_json_line_does_not_crash_renderer(self):
        state = {
            "emitted_text": False,
            "last_char": "",
            "live_text_to_stdout": True,
        }
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli_caller._emit_live_line("codex", '"use strict"\n', "stdout", state)
        self.assertEqual(stdout.getvalue(), '"use strict"\n')

    def test_streaming_runner_separates_claude_text_across_tool_call(self):
        lines = [
            {
                "type": "stream_event",
                "event": {"delta": {"type": "text_delta", "text": "before"}},
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Read"}]},
            },
            {
                "type": "stream_event",
                "event": {"delta": {"type": "text_delta", "text": "after"}},
            },
            {"type": "result", "result": "after"},
        ]
        program = (
            "import json; events="
            + repr(lines)
            + "; [print(json.dumps(event), flush=True) for event in events]"
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc, out, err, reason = cli_caller._run_with_idle_timeout(
                [sys.executable, "-c", program],
                cwd=None,
                idle_timeout=5,
                hard_timeout=10,
                stream=True,
                family="claude",
                live_text_to_stdout=True,
            )
        self.assertEqual((rc, err, reason), (0, "", "ok"))
        self.assertIn('"type": "result"', out)
        self.assertEqual(stdout.getvalue(), "before\nafter\n")
        self.assertEqual(stderr.getvalue(), "[claude] tool: Read\n")

    def test_streaming_runner_routes_codex_answer_to_stderr_under_redirect(self):
        lines = [
            {"type": "thread.started", "thread_id": "test-thread"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "codex final"},
            },
        ]
        program = (
            "import json; events="
            + repr(lines)
            + "; [print(json.dumps(event), flush=True) for event in events]"
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc, out, err, reason = cli_caller._run_with_idle_timeout(
                [sys.executable, "-c", program],
                cwd=None,
                idle_timeout=5,
                hard_timeout=10,
                stream=True,
                family="codex",
                live_text_to_stdout=False,
            )
        self.assertEqual((rc, err, reason), (0, "", "ok"))
        self.assertEqual(
            cli_caller._extract_stream_result("codex", out), "codex final"
        )
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "[codex] session: test-thread\ncodex final\n",
        )

    def test_missing_system_prompt_is_a_hard_error(self):
        with self.assertRaisesRegex(ValueError, "Available:"):
            cli_caller.load_systemprompt("does-not-exist")

    def test_system_prompt_rejects_path_traversal(self):
        with self.assertRaisesRegex(ValueError, "may contain only"):
            cli_caller.load_systemprompt("../../../tmp/anything")

    def test_all_shipped_system_prompts_load(self):
        prompt_dir = MODULE_PATH.parent / "systemprompts"
        for path in prompt_dir.glob("*.txt"):
            with self.subTest(path=path.name):
                self.assertTrue(cli_caller.load_systemprompt(path.stem).strip())

    def test_invalid_working_directory_fails_before_spawn(self):
        result = cli_caller.call_model(
            "codex", "test", cwd="/definitely/not/a/real/cli-agents-directory"
        )
        self.assertIsNone(result)

    def test_hard_timeout_returns_promptly(self):
        started = time.monotonic()
        rc, out, err, reason = cli_caller._run_with_idle_timeout(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=None,
            idle_timeout=5,
            hard_timeout=0.1,
        )
        self.assertEqual(reason, "hard")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(out, "")
        self.assertEqual(err, "")
        self.assertIsNotNone(rc)

    def test_empty_successful_output_is_not_an_error(self):
        with (
            mock.patch.object(cli_caller, "call_model", return_value=""),
            mock.patch.object(
                sys,
                "argv",
                ["cli_caller.py", "--model", "claude", "--prompt", "test"],
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli_caller.main(), 0)

    def test_streaming_main_prints_clean_final_when_stdout_is_redirected(self):
        stdout = self.FakeStdout(False)
        with (
            mock.patch.object(cli_caller, "call_model", return_value="done"),
            mock.patch.object(
                sys,
                "argv",
                [
                    "cli_caller.py",
                    "--model",
                    "claude",
                    "--prompt",
                    "test",
                    "--stream",
                ],
            ),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(cli_caller.main(), 0)
        self.assertEqual(stdout.getvalue(), "done\n")

    def test_streaming_main_does_not_repeat_final_in_interactive_terminal(self):
        stdout = self.FakeStdout(True)
        with (
            mock.patch.object(cli_caller, "call_model", return_value="done"),
            mock.patch.object(
                sys,
                "argv",
                [
                    "cli_caller.py",
                    "--model",
                    "claude",
                    "--prompt",
                    "test",
                    "--stream",
                ],
            ),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(cli_caller.main(), 0)
        self.assertEqual(stdout.getvalue(), "")

    def test_info_does_not_call_provider(self):
        with (
            mock.patch.object(cli_caller, "call_model") as call,
            mock.patch.object(
                sys,
                "argv",
                ["cli_caller.py", "--model", "codex", "--info"],
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli_caller.main(), 0)
            call.assert_not_called()

    def test_uncommitted_review_accepts_empty_prompt(self):
        with (
            mock.patch.object(
                cli_caller.shutil, "which", return_value="/usr/bin/codex"
            ),
            mock.patch.object(
                cli_caller,
                "_run_with_idle_timeout",
                return_value=(0, "done", "", "ok"),
            ) as run,
        ):
            self.assertEqual(
                cli_caller.call_model("codex-review-uncommitted", ""), "done"
            )
        command = run.call_args.args[0]
        self.assertEqual(command[-1], 'approval_policy="never"')

    def test_nonempty_prompt_declares_actual_execution_context(self):
        with (
            mock.patch.object(
                cli_caller.shutil, "which", return_value="/usr/bin/claude"
            ),
            mock.patch.object(
                cli_caller,
                "_run_with_idle_timeout",
                return_value=(0, "done", "", "ok"),
            ) as run,
        ):
            self.assertEqual(
                cli_caller.call_model(
                    "claude",
                    "Review this",
                    cwd=str(ROOT),
                    access="read-only",
                ),
                "done",
            )

        prompt = run.call_args.args[0][-1]
        self.assertIn("Execution context supplied by the CLI wrapper:", prompt)
        self.assertIn("- Access: read-only", prompt)
        self.assertIn(f"- Working directory: {ROOT.resolve()}", prompt)
        self.assertIn("This is already the external provider turn", prompt)
        self.assertIn("User Request:\nReview this", prompt)


if __name__ == "__main__":
    unittest.main()
