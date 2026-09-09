import json
import unittest

from ai_dependency_guard.models import Finding
from ai_dependency_guard.outputs import render_json, render_sarif, render_text


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.finding = Finding(
            package_name="definitely-not-a-real-package-12345",
            ecosystem="npm",
            path="README.md",
            line=3,
            status="not_found",
            reason="Package was not found in the npm registry.",
            suggestion="Verify the package name before installing it.",
            version="v1.2.3",
        )

    def test_text_output_contains_location_and_reason(self):
        output = render_text([self.finding])

        self.assertIn("README.md:3", output)
        self.assertIn("definitely-not-a-real-package-12345", output)
        self.assertIn("@v1.2.3", output)
        self.assertIn("not found", output.lower())

    def test_json_output_is_machine_readable(self):
        payload = json.loads(render_json([self.finding]))

        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["findings"][0]["ecosystem"], "npm")
        self.assertEqual(payload["findings"][0]["version"], "v1.2.3")

    def test_sarif_output_has_valid_core_shape(self):
        payload = json.loads(render_sarif([self.finding]))

        self.assertEqual(payload["version"], "2.1.0")
        self.assertEqual(payload["runs"][0]["tool"]["driver"]["name"], "ai-dependency-guard")
        self.assertEqual(payload["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"], 3)
        self.assertIn(
            "definitely-not-a-real-package-12345@v1.2.3",
            payload["runs"][0]["results"][0]["message"]["text"],
        )


if __name__ == "__main__":
    unittest.main()
