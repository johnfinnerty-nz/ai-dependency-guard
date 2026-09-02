import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectContractTests(unittest.TestCase):
    def test_action_metadata_declares_composite_action_and_required_inputs(self):
        action = (ROOT / "action.yml").read_text(encoding="utf-8")

        self.assertIn("using: composite", action)
        self.assertIn("path:", action)
        self.assertIn("fail-on:", action)
        self.assertIn("ignore-paths:", action)
        self.assertIn("ignore-packages:", action)
        self.assertIn("sarif-file:", action)
        self.assertIn("github-annotations", action)

    def test_workflows_trigger_push_and_pull_request_with_read_permissions(self):
        for workflow_name in ("ci.yml", "ai-dependency-guard.yml"):
            workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
            self.assertIn("push:", workflow)
            self.assertIn("pull_request:", workflow)
            self.assertIn("contents: read", workflow)

    def test_readme_contains_the_safety_limitation(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("A package existing in a registry does not mean that it is safe.", readme)
        self.assertIn("does not install or execute", readme)


if __name__ == "__main__":
    unittest.main()
