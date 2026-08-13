import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "cli-agents"


class PackageContractTests(unittest.TestCase):
    @staticmethod
    def model_profiles():
        tree = ast.parse((SKILL_DIR / "cli_caller.py").read_text())
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "MODEL_COMMANDS"
                for target in node.targets
            ):
                continue
            return {key.value for key in node.value.keys}
        raise AssertionError("MODEL_COMMANDS not found")

    def test_manifest_version_and_skill_frontmatter(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        skill = (SKILL_DIR / "SKILL.md").read_text()
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        frontmatter = skill.split("---", 2)[1]
        keys = set(re.findall(r"^([a-z][a-z0-9_-]*):", frontmatter, re.MULTILINE))
        self.assertEqual(keys, {"name", "description"})

    def test_every_system_prompt_is_documented(self):
        skill = (SKILL_DIR / "SKILL.md").read_text()
        for path in (SKILL_DIR / "systemprompts").glob("*.txt"):
            with self.subTest(path=path.name):
                self.assertIn(f"`{path.stem}`", skill)

    def test_every_model_profile_is_documented(self):
        documents = {
            "SKILL.md": (SKILL_DIR / "SKILL.md").read_text(),
            "README.md": (ROOT / "README.md").read_text(),
            "QUICKREF.md": (ROOT / "QUICKREF.md").read_text(),
        }
        for profile in self.model_profiles():
            for name, content in documents.items():
                with self.subTest(profile=profile, document=name):
                    self.assertIn(f"`{profile}`", content)

    def test_skill_links_to_supplementary_docs(self):
        skill = (SKILL_DIR / "SKILL.md").read_text()
        for name in ("QUICKREF.md", "examples.md"):
            with self.subTest(document=name):
                self.assertTrue((ROOT / name).is_file())
                self.assertIn(f"../../{name}", skill)

    def test_legacy_server_layer_is_removed(self):
        for relative in (
            "agent_server.py",
            "ollama_compat_server.py",
            "server.sh",
            "skill.json",
        ):
            with self.subTest(path=relative):
                self.assertFalse((SKILL_DIR / relative).exists())
        self.assertFalse((ROOT / "n8n_basic_chat.json").exists())
        self.assertFalse((ROOT / "n8n_examples.json").exists())

    def test_runtime_commands_use_defaults_or_stable_aliases(self):
        tree = ast.parse((SKILL_DIR / "cli_caller.py").read_text())
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "MODEL_COMMANDS"
                for target in node.targets
            )
        )
        commands = ast.literal_eval(assignment.value)
        for profile, config in commands.items():
            cmd = config["cmd"]
            if config["family"] in {"codex", "gemini"}:
                with self.subTest(profile=profile):
                    self.assertNotIn("-m", cmd)
                    self.assertNotIn("--model", cmd)
            if config["family"] == "claude" and "--model" in cmd:
                model = cmd[cmd.index("--model") + 1]
                with self.subTest(profile=profile):
                    self.assertIn(model, {"opus", "sonnet", "haiku"})


class PromptCookbookRegressionTests(unittest.TestCase):
    def test_prompt_cookbook_fixtures(self):
        fixture_dir = ROOT / "evals" / "prompt-cookbook"
        fixtures = sorted(fixture_dir.glob("*.json"))
        self.assertTrue(fixtures, "Prompt Cookbook fixtures are missing")

        for fixture_path in fixtures:
            fixture = json.loads(fixture_path.read_text())
            targets = []
            for pattern in fixture["targets"]:
                targets.extend(ROOT.glob(pattern))
            self.assertTrue(targets, f"{fixture_path.name}: no targets matched")

            for target in targets:
                content = target.read_text()
                for required in fixture.get("contains", []):
                    with self.subTest(
                        fixture=fixture["id"], target=str(target), required=required
                    ):
                        self.assertIn(required, content)
                for forbidden in fixture.get("forbidden", []):
                    with self.subTest(
                        fixture=fixture["id"], target=str(target), forbidden=forbidden
                    ):
                        self.assertNotIn(forbidden, content)


if __name__ == "__main__":
    unittest.main()
