"""Offline regressions for replacements, exclusions, and proxy metadata."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from ai_dependency_guard.extractors import ExtractionError, extract_references
from ai_dependency_guard.registry import PublicRegistryClient
from ai_dependency_guard.scanner import scan_path


class Response:
    def __init__(self, payload=b'{"Version":"v1.0.1"}', status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class GoReviewRegressions(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "repo"
        self.root.mkdir()
        self.cache = Path(directory.name) / "cache.json"
        self.calls = []
        self.response = Response()
        self.client = PublicRegistryClient(opener=self.open, max_retries=0)

    def open(self, request, timeout):
        self.calls.append(request.full_url)
        return self.response

    def manifest(self, extra="", version="v1.0.0", name="example.com/lib"):
        (self.root / "go.mod").write_text(
            f"module example.com/app\nrequire {name} {version}\n{extra}",
            encoding="utf-8",
        )

    def test_ignore_original_and_effective_names_before_lookup(self):
        for selector in ("", " v1.0.0"):
            for ignored in ("corp.example/private", "corp.example/private-fork"):
                with self.subTest(selector=selector, ignored=ignored):
                    self.calls.clear()
                    self.manifest(
                        f"replace corp.example/private{selector} => "
                        "corp.example/private-fork v1.0.1\n",
                        name="corp.example/private",
                    )
                    result = scan_path(
                        self.root, registry=self.client, ignore_packages={ignored}
                    )
                    self.assertEqual(self.calls, [])
                    self.assertEqual(result.findings, [])

    def test_ignored_dependency_cannot_create_persistent_cache_entry(self):
        self.manifest("replace example.com/lib => example.com/fork v1.0.1\n")
        scan_path(
            self.root,
            registry=self.client,
            ignore_packages={"example.com/lib"},
            cache_file=self.cache,
        )
        self.assertEqual(self.calls, [])
        self.assertEqual(json.loads(self.cache.read_text())["entries"], {})

    def test_ignore_is_still_case_insensitive_for_both_names(self):
        self.manifest(
            "replace example.com/Lib => example.com/Fork v1.0.1\n",
            name="example.com/Lib",
        )
        for name in (" EXAMPLE.COM/LIB ", "EXAMPLE.COM/FORK"):
            scan_path(self.root, registry=self.client, ignore_packages={name})
        self.assertEqual(self.calls, [])

    def test_ignoring_one_alias_does_not_ignore_another_requirement(self):
        (self.root / "go.mod").write_text(
            "module example.com/app\nrequire (\n"
            "example.com/a v1.0.0\nexample.com/b v1.0.0\n)\n"
            "replace example.com/a => example.com/fork v1.0.1\n"
            "replace example.com/b => example.com/fork v1.0.1\n",
            encoding="utf-8",
        )
        result = scan_path(
            self.root, registry=self.client, ignore_packages={"example.com/a"}
        )
        self.assertEqual(result.findings, [])
        self.assertEqual(
            self.calls, ["https://proxy.golang.org/example.com/fork/@v/v1.0.1.info"]
        )

    def test_effective_reference_retains_declared_identity(self):
        self.manifest("replace example.com/lib => example.com/fork v1.0.1\n")
        reference = extract_references("go.mod", (self.root / "go.mod").read_text())[0]
        self.assertEqual(
            (reference.name, reference.version), ("example.com/fork", "v1.0.1")
        )
        self.assertEqual(
            (reference.declared_name, reference.declared_version),
            ("example.com/lib", "v1.0.0"),
        )
        self.assertEqual((reference.source, reference.line), ("replace", 3))

    def test_unreplaced_and_local_requirements_keep_exclusions(self):
        for extra in (
            "",
            "replace example.com/lib => ../local\n",
            "replace example.com/lib v1.0.0 => ../local\n",
        ):
            with self.subTest(extra=extra):
                self.manifest(extra)
                result = scan_path(
                    self.root, registry=self.client, ignore_packages={"example.com/lib"}
                )
                self.assertEqual(result.findings, [])
                self.assertEqual(self.calls, [])

    def test_excluded_file_never_looks_up_replacements(self):
        self.manifest("replace example.com/lib => example.com/fork v1.0.1\n")
        result = scan_path(self.root, registry=self.client, ignore_paths={"go.mod"})
        self.assertEqual(result.findings, [])
        self.assertEqual(self.calls, [])

    def test_invalid_info_responses_are_errors_and_never_cached(self):
        self.manifest()
        cases = [
            (b"", 200),
            (b"<html>maintenance</html>", 200),
            (b"{}", 200),
            (b'{"Version":"v9.9.9"}', 200),
            (b"", 204),
            (b"[]", 200),
            (b'{"Version":null}', 200),
            (b'{"Version":1}', 200),
            (b'{"Version":"v1.0"}', 200),
            (b'{"Version":"v01.0.0"}', 200),
            (b'{"Version":"v1.0.0+meta"}', 200),
            (b"\xff", 200),
            (b'{"Version":"v1.0.0"}', 201),
            (b'{"Version":"v1.0.0"}', None),
        ]
        for payload, status in cases:
            with self.subTest(payload=payload, status=status):
                self.calls.clear()
                self.response = Response(payload, status)
                for _ in range(2):
                    result = scan_path(
                        self.root, registry=self.client, cache_file=self.cache
                    )
                    self.assertEqual([f.status for f in result.findings], ["error"])
                    self.assertEqual([f.severity for f in result.findings], ["warning"])
                    self.assertEqual(json.loads(self.cache.read_text())["entries"], {})
                self.assertEqual(len(self.calls), 2)

    def test_non_json_numeric_constants_are_errors_and_never_cached(self):
        self.manifest()
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                self.response = Response(
                    ('{"Version":"v1.0.0","Extra":' + constant + "}").encode()
                )
                result = scan_path(
                    self.root, registry=self.client, cache_file=self.cache
                )
                self.assertEqual([f.status for f in result.findings], ["error"])
                self.assertEqual(json.loads(self.cache.read_text())["entries"], {})

    def test_valid_info_metadata_accepts_optional_time_and_future_fields(self):
        self.manifest()
        for extra in (
            {},
            {"Time": "2026-01-02T03:04:05Z"},
            {"Time": "2026-01-02T03:04:05.123456789+12:00", "Origin": {"Hash": "abc"}},
        ):
            with self.subTest(extra=extra):
                self.response = Response(
                    json.dumps({"Version": "v1.0.0", **extra}).encode()
                )
                self.assertEqual(
                    scan_path(self.root, registry=self.client).findings, []
                )

    def test_invalid_optional_time_is_not_clean(self):
        self.manifest()
        for value in (
            None,
            1,
            "yesterday",
            "2026-99-02T03:04:05Z",
            "2026-01-02T03:04:05",
            "2026-01-02T03:04:05+12:99",
        ):
            with self.subTest(value=value):
                self.response = Response(
                    json.dumps({"Version": "v1.0.0", "Time": value}).encode()
                )
                self.assertEqual(
                    [
                        f.status
                        for f in scan_path(self.root, registry=self.client).findings
                    ],
                    ["error"],
                )

    def test_noncanonical_requests_are_reported_without_network(self):
        for version in ("main", "v1", "v1.2", "v1.0.0+build", "v1.0.0-01", "v01.0.0"):
            with self.subTest(version=version):
                result = self.client.check("go", "example.com/lib", version=version)
                self.assertEqual(result.status, "error")
                self.assertIn("canonical", result.message)
        self.assertEqual(self.calls, [])

    def test_canonical_prereleases_pseudo_and_incompatible_versions(self):
        for version in (
            "v0.0.0",
            "v1.0.0-0",
            "v1.0.0-alpha.2",
            "v1.0.0-0beta",
            "v0.0.0-20260102030405-abcdef123456",
            "v2.0.0+incompatible",
        ):
            with self.subTest(version=version):
                self.response = Response(json.dumps({"Version": version}).encode())
                self.assertEqual(
                    self.client.check("go", "example.com/lib", version=version).status,
                    "found",
                )

    def test_schema_three_go_results_are_rechecked_but_other_caches_survive(self):
        self.manifest()
        (self.root / "requirements.txt").write_text("requests\n", encoding="utf-8")
        (self.root / "package.json").write_text(
            '{"dependencies":{"express":"1.0.0"}}', encoding="utf-8"
        )
        self.cache.write_text(
            json.dumps(
                {
                    "version": 3,
                    "entries": {
                        key: {
                            "status": "found",
                            "message": "Old unvalidated result.",
                            "checked_at": time.time(),
                        }
                        for key in (
                            "go:example.com/lib@v1.0.0",
                            "pypi:requests",
                            "npm:express",
                        )
                    },
                }
            ),
            encoding="utf-8",
        )
        self.response = Response(b"{}")
        result = scan_path(self.root, registry=self.client, cache_file=self.cache)
        self.assertEqual([f.status for f in result.findings], ["error"])
        self.assertEqual(
            self.calls, ["https://proxy.golang.org/example.com/lib/@v/v1.0.0.info"]
        )
        payload = json.loads(self.cache.read_text())
        self.assertEqual(payload["version"], 4)
        self.assertEqual(set(payload["entries"]), {"npm:express", "pypi:requests"})

    def test_conflicting_replacement_targets_are_parse_errors(self):
        for selector in ("", " v1.0.0"):
            for first, second in (
                ("example.com/one v1.0.1", "example.com/two v1.0.1"),
                ("example.com/one v1.0.1", "example.com/one v1.0.2"),
                ("../one", "../two"),
                ("../one", "example.com/two v1.0.1"),
                ("example.com/one v1.0.1", "../two"),
            ):
                with self.subTest(selector=selector, first=first, second=second):
                    text = (
                        "module example.com/app\nrequire example.com/lib v1.0.0\n"
                        f"replace example.com/lib{selector} => {first}\n"
                        f"replace example.com/lib{selector} => {second}\n"
                    )
                    with self.assertRaisesRegex(
                        ExtractionError, "[Cc]onflicting replace"
                    ):
                        extract_references("go.mod", text)

    def test_conflicts_are_visible_findings_without_network(self):
        self.manifest(
            "replace example.com/lib => ../one\nreplace example.com/lib => ../two\n"
        )
        result = scan_path(self.root, registry=self.client)
        self.assertEqual(
            [f.package_name for f in result.findings], ["<file-parse-error>"]
        )
        self.assertEqual([f.severity for f in result.findings], ["error"])
        self.assertEqual(self.calls, [])

    def test_identical_replacements_keep_the_first_location(self):
        self.manifest(
            "replace example.com/lib => example.com/fork v1.0.1\n"
            'replace "example.com/lib" => "example.com/fork" "v1.0.1"\n'
        )
        references = extract_references("go.mod", (self.root / "go.mod").read_text())
        self.assertEqual(
            [(r.name, r.version, r.line) for r in references],
            [("example.com/fork", "v1.0.1", 3)],
        )

    def test_identical_local_replacements_are_allowed(self):
        self.manifest(
            'replace example.com/lib => ../local\nreplace example.com/lib => "../local"\n'
        )
        self.assertEqual(
            extract_references("go.mod", (self.root / "go.mod").read_text()), []
        )


if __name__ == "__main__":
    unittest.main()
