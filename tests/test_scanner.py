import json
import tempfile
import time
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

    def test_go_module_version_reaches_scanner_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.mod").write_text(
                "module example.com/demo\n\n"
                "go 1.24\n\n"
                "require example.com/missing-ai v1.2.3\n",
                encoding="utf-8",
            )
            registry = FakeRegistry(
                {
                    ("go", "example.com/missing-ai"): RegistryResult(
                        "not_found", "Module was not found in the Go module proxy."
                    )
                }
            )

            result = scan_path(root, registry=registry)

            self.assertEqual(len(result.findings), 1)
            finding = result.findings[0]
            self.assertEqual(finding.package_name, "example.com/missing-ai")
            self.assertEqual(finding.version, "v1.2.3")
            self.assertEqual(finding.path, "go.mod")
            self.assertEqual(finding.line, 5)

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

    def test_reports_malformed_go_replace_as_a_file_parse_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.mod").write_text(
                "module example.com/app\nreplace example.com/mod =>\n",
                encoding="utf-8",
            )

            result = scan_path(root, offline=True)

            self.assertEqual(len(result.findings), 1)
            self.assertEqual(result.findings[0].package_name, "<file-parse-error>")
            self.assertIn("Malformed replace directive", result.findings[0].reason)

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

    def test_go_cache_keeps_module_path_case_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_file = root / "cache.json"
            (root / "go.mod").write_text(
                "module example.com/app\n\n"
                "require (\n"
                "    example.com/Case v1.0.0\n"
                "    example.com/case v1.0.0\n"
                ")\n",
                encoding="utf-8",
            )
            first_registry = FakeRegistry(
                {
                    ("go", "example.com/Case"): RegistryResult(
                        "found", "Uppercase exists."
                    ),
                    ("go", "example.com/case"): RegistryResult(
                        "not_found", "Lowercase does not exist."
                    ),
                }
            )
            second_registry = FakeRegistry({})

            first = scan_path(root, registry=first_registry, cache_file=cache_file)
            second = scan_path(root, registry=second_registry, cache_file=cache_file)

            self.assertEqual(
                first_registry.calls,
                [("go", "example.com/Case"), ("go", "example.com/case")],
            )
            self.assertEqual(second_registry.calls, [])
            self.assertEqual(
                [(item.package_name, item.reason) for item in first.findings],
                [("example.com/case", "Lowercase does not exist.")],
            )
            self.assertEqual(
                [(item.package_name, item.reason) for item in second.findings],
                [("example.com/case", "Lowercase does not exist.")],
            )
            cache_payload = json.loads(cache_file.read_text(encoding="utf-8"))
            self.assertEqual(cache_payload["version"], 2)
            self.assertIn("go:example.com/Case", cache_payload["entries"])
            self.assertIn("go:example.com/case", cache_payload["entries"])

    def test_invalidates_legacy_go_cache_entries_but_preserves_other_ecosystems(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_file = root / "cache.json"
            checked_at = time.time()
            (root / "go.mod").write_text(
                "module example.com/app\nrequire example.com/case v1.0.0\n",
                encoding="utf-8",
            )
            (root / "package.json").write_text(
                '{"dependencies":{"express":"1.0.0"}}\n', encoding="utf-8"
            )
            (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
            cache_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": {
                            "go:example.com/case": {
                                "status": "found",
                                "message": "Legacy ambiguous Go result.",
                                "checked_at": checked_at,
                            },
                            "npm:express": {
                                "status": "found",
                                "message": "Cached npm result.",
                                "checked_at": checked_at,
                            },
                            "pypi:requests": {
                                "status": "found",
                                "message": "Cached PyPI result.",
                                "checked_at": checked_at,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            registry = FakeRegistry(
                {
                    ("go", "example.com/case"): RegistryResult(
                        "not_found", "Lowercase module does not exist."
                    )
                }
            )

            result = scan_path(root, registry=registry, cache_file=cache_file)

            self.assertEqual(registry.calls, [("go", "example.com/case")])
            self.assertEqual(
                [(item.package_name, item.reason) for item in result.findings],
                [("example.com/case", "Lowercase module does not exist.")],
            )
            cache_payload = json.loads(cache_file.read_text(encoding="utf-8"))
            self.assertEqual(cache_payload["version"], 2)
            self.assertEqual(
                cache_payload["entries"]["go:example.com/case"]["status"],
                "not_found",
            )
            self.assertIn("npm:express", cache_payload["entries"])
            self.assertIn("pypi:requests", cache_payload["entries"])

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
