import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from ai_dependency_guard.cli import build_parser, main


class CliTests(unittest.TestCase):
    def test_json_scan_returns_findings_and_exit_code_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "npm install definitely-not-a-real-package-12345\n", encoding="utf-8"
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(["scan", str(root), "--offline", "--json"])

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertEqual(payload["summary"]["total"], 1)

    def test_sarif_file_and_github_annotations_are_emitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sarif_path = root / "results.sarif"
            (root / "README.md").write_text(
                "npm install definitely-not-a-real-package-12345\n", encoding="utf-8"
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "scan",
                        str(root),
                        "--offline",
                        "--github-annotations",
                        "--sarif-file",
                        str(sarif_path),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("::warning", output.getvalue())
            sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
            self.assertEqual(sarif["version"], "2.1.0")

    def test_fail_on_none_reports_findings_without_failing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "npm install definitely-not-a-real-package-12345\n", encoding="utf-8"
            )

            with redirect_stdout(io.StringIO()):
                exit_code = main(["scan", str(root), "--offline", "--fail-on", "none"])

            self.assertEqual(exit_code, 0)

    def test_missing_path_returns_usage_error(self):
        errors = io.StringIO()

        with redirect_stderr(errors):
            exit_code = main(["scan", "does-not-exist"])

        self.assertEqual(exit_code, 2)
        self.assertIn("does not exist", errors.getvalue())

    def test_parser_accepts_cache_and_ignore_path_options(self):
        args = build_parser().parse_args(
            [
                "scan",
                ".",
                "--cache-file",
                "cache.json",
                "--ignore-path",
                "fixtures/**",
            ]
        )

        self.assertEqual(args.cache_file, "cache.json")
        self.assertEqual(args.ignore_path, ["fixtures/**"])


if __name__ == "__main__":
    unittest.main()
