import tempfile
import unittest
from pathlib import Path

from ai_dependency_guard.models import RegistryResult
from ai_dependency_guard.scanner import scan_path


class FakeRegistry:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def check(self, ecosystem, name):
        self.calls.append((ecosystem, name))
        return self.results.get(
            (ecosystem, name),
            RegistryResult("found", "Package exists in the public registry."),
        )


class ScannerTests(unittest.TestCase):
    def test_finds_missing_dependency_and_keeps_valid_dependency_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "npm install express definitely-not-a-real-package-12345\n",
                encoding="utf-8",
            )
            registry = FakeRegistry(
                {
                    (
                        "npm",
                        "definitely-not-a-real-package-12345",
                    ): RegistryResult(
                        "not_found", "Package was not found in the npm registry."
                    )
                }
            )

            result = scan_path(root, registry=registry)

            self.assertEqual(len(result.findings), 1)
            finding = result.findings[0]
            self.assertEqual(finding.package_name, "definitely-not-a-real-package-12345")
            self.assertEqual(finding.path, "README.md")
            self.assertEqual(finding.line, 1)
            self.assertIn("not found", finding.reason.lower())
            self.assertIn(("npm", "express"), registry.calls)

    def test_offline_scan_reports_unknown_without_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requirements.txt").write_text("requests\n", encoding="utf-8")

            result = scan_path(root, offline=True)

            self.assertEqual(len(result.findings), 1)
            self.assertEqual(result.findings[0].status, "unknown")
            self.assertIn("offline", result.findings[0].reason.lower())

    def test_ignores_git_and_node_modules_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ignored = root / "node_modules" / "bad" / "README.md"
            ignored.parent.mkdir(parents=True)
            ignored.write_text("npm install definitely-not-a-real-package-12345\n", encoding="utf-8")
            (root / "README.md").write_text("# clean\n", encoding="utf-8")

            result = scan_path(root, offline=True)

            self.assertEqual(result.findings, [])

    def test_reports_malformed_supported_structured_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text('{"dependencies": [\n', encoding="utf-8")
            (root / "pyproject.toml").write_text("[project\n", encoding="utf-8")

            result = scan_path(root, offline=True)

            self.assertEqual(len(result.findings), 2)
            self.assertTrue(all(item.status == "error" for item in result.findings))

    def test_scan_does_not_execute_shell_or_project_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "should-not-exist.txt"
            (root / "script.sh").write_text(
                f"touch {marker.name}\nnpm install definitely-not-a-real-package-12345\n",
                encoding="utf-8",
            )

            scan_path(root, offline=True)

            self.assertFalse(marker.exists())

    def test_handles_malformed_yaml_as_untrusted_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "broken.yml"
            workflow.write_text(
                "jobs:\n  scan:\n    run: npm install definitely-not-a-real-package-12345\n    with: [\n",
                encoding="utf-8",
            )

            result = scan_path(root, offline=True)

            self.assertEqual(len(result.findings), 1)
            self.assertEqual(result.findings[0].package_name, "definitely-not-a-real-package-12345")

    def test_reuses_explicit_cache_on_a_repeated_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_file = root / "cache.json"
            (root / "README.md").write_text(
                "npm install definitely-not-a-real-package-12345\n", encoding="utf-8"
            )
            first_registry = FakeRegistry(
                {
                    (
                        "npm",
                        "definitely-not-a-real-package-12345",
                    ): RegistryResult(
                        "not_found", "Package was not found in the npm registry."
                    )
                }
            )
            second_registry = FakeRegistry({})

            first = scan_path(root, registry=first_registry, cache_file=cache_file)
            second = scan_path(root, registry=second_registry, cache_file=cache_file)

            self.assertEqual(len(first.findings), 1)
            self.assertEqual(len(second.findings), 1)
            self.assertEqual(first.findings[0].reason, second.findings[0].reason)
            self.assertEqual(first_registry.calls, [("npm", "definitely-not-a-real-package-12345")])
            self.assertEqual(second_registry.calls, [])

    def test_ignores_configured_glob_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ignored = root / "fixtures" / "demo.md"
            ignored.parent.mkdir(parents=True)
            ignored.write_text("npm install definitely-not-a-real-package-12345\n", encoding="utf-8")
            (root / "README.md").write_text("# clean\n", encoding="utf-8")

            result = scan_path(root, offline=True, ignore_paths={"fixtures/**"})

            self.assertEqual(result.findings, [])

    def test_does_not_skip_files_when_scan_root_has_a_generated_directory_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "build"
            root.mkdir()
            (root / "README.md").write_text(
                "npm install definitely-not-a-real-package-12345\n", encoding="utf-8"
            )

            result = scan_path(root, offline=True)

            self.assertEqual(len(result.findings), 1)

    def test_empty_repository_is_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            result = scan_path(Path(directory), offline=True)

            self.assertEqual(result.findings, [])
            self.assertEqual(result.files_scanned, 0)
            self.assertEqual(result.references_scanned, 0)

    def test_keeps_duplicate_locations_but_reuses_registry_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "npm install definitely-not-a-real-package-12345\n"
                "npm install definitely-not-a-real-package-12345\n",
                encoding="utf-8",
            )
            registry = FakeRegistry(
                {
                    (
                        "npm",
                        "definitely-not-a-real-package-12345",
                    ): RegistryResult("not_found", "Package was not found in the npm registry.")
                }
            )

            result = scan_path(root, registry=registry)

            self.assertEqual([finding.line for finding in result.findings], [1, 2])
            self.assertEqual(
                registry.calls,
                [("npm", "definitely-not-a-real-package-12345")],
            )

    def test_scans_monorepo_like_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frontend = root / "packages" / "frontend"
            worker = root / "services" / "worker"
            frontend.mkdir(parents=True)
            worker.mkdir(parents=True)
            (frontend / "package.json").write_text(
                '{"dependencies":{"definitely-not-a-real-package-12345":"1.0.0"}}\n',
                encoding="utf-8",
            )
            (worker / "requirements.txt").write_text(
                "definitely-not-a-real-python-package-12345\n", encoding="utf-8"
            )

            result = scan_path(root, offline=True)

            self.assertEqual(
                {(finding.ecosystem, finding.package_name) for finding in result.findings},
                {
                    ("npm", "definitely-not-a-real-package-12345"),
                    ("pypi", "definitely-not-a-real-python-package-12345"),
                },
            )

    def test_partial_registry_failure_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "npm install express definitely-not-a-real-package-12345\n", encoding="utf-8"
            )
            registry = FakeRegistry(
                {
                    (
                        "npm",
                        "definitely-not-a-real-package-12345",
                    ): RegistryResult("error", "The npm registry returned HTTP 503 after retries."),
                }
            )

            result = scan_path(root, registry=registry)

            self.assertEqual(len(result.findings), 1)
            self.assertEqual(result.findings[0].status, "error")


if __name__ == "__main__":
    unittest.main()
