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
        self.assertEqual(cmd[-2:], ["--permission-mode", "plan"])

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


if __name__ == "__main__":
    unittest.main()
