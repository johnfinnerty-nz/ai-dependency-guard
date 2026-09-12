"""Offline regression coverage for version-aware Go registry lookups."""

import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from ai_dependency_guard.cli import main
from ai_dependency_guard.extractors import extract_references
from ai_dependency_guard.outputs import render_json, render_sarif, render_text
from ai_dependency_guard.registry import PublicRegistryClient
from ai_dependency_guard.scanner import scan_path

PROXY = "https://proxy.golang.org/"
PSEUDO = "v0.0.0-20260102030405-abcdef123456"


class Response:
    def __init__(self, status=200, payload=b"{}"):
        self.status = status
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def info(version):
    return Response(200, json.dumps({"Version": version}).encode())


class GoVersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "repo"
        self.root.mkdir()
        self.cache_file = Path(self.directory.name) / "cache.json"
        self.calls = []
        self.routes = {}
        self.client = PublicRegistryClient(opener=self.open, max_retries=0)

    def open(self, request, timeout):
        self.assertGreater(timeout, 0)
        path = request.full_url.removeprefix(PROXY)
        self.assertTrue(request.full_url.startswith(PROXY))
        self.calls.append(path)
        # An existing module can have a list, while a requested version is absent.
        result = self.routes.get(path)
        if result is None:
            result = (
                Response(200, b"v1.0.0\n")
                if path.endswith("/@v/list")
                else Response(404)
            )
        if isinstance(result, Exception):
            raise result
        return result

    def require(self, version="v1.0.0", name="example.com/lib", subdir="", extra=""):
        directory = self.root / subdir
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "go.mod").write_text(
            f"module example.com/app\nrequire {name} {version}\n{extra}",
            encoding="utf-8",
        )

    def test_missing_exact_version_is_not_hidden_by_a_module_list(self):
        self.require("v1.2.99")
        result = scan_path(self.root, registry=self.client)
        self.assertEqual(
            [(f.version, f.status) for f in result.findings], [("v1.2.99", "not_found")]
        )
        self.assertEqual(self.calls, ["example.com/lib/@v/v1.2.99.info"])
        self.assertIn("example.com/lib@v1.2.99", result.findings[0].reason)

    def test_pseudo_version_is_checked_directly_without_a_version_list(self):
        self.require(PSEUDO)
        self.routes["example.com/lib/@v/list"] = Response(200, b"")
        self.routes[f"example.com/lib/@v/{PSEUDO}.info"] = info(PSEUDO)
        result = scan_path(self.root, registry=self.client)
        self.assertEqual(result.findings, [])
        self.assertEqual(self.calls, [f"example.com/lib/@v/{PSEUDO}.info"])

    def test_missing_pseudo_version_is_not_reported_clean(self):
        self.require(PSEUDO)
        self.routes["example.com/lib/@v/list"] = Response(200, b"")
        result = scan_path(self.root, registry=self.client)
        self.assertEqual(
            [(f.version, f.status) for f in result.findings], [(PSEUDO, "not_found")]
        )

    def test_two_versions_are_separate_but_repeated_versions_reuse_a_lookup(self):
        self.require("v1.0.0", subdir="a")
        self.require("v1.1.0", subdir="b")
        self.require("v1.0.0", subdir="c")
        self.routes["example.com/lib/@v/v1.0.0.info"] = info("v1.0.0")
        result = scan_path(self.root, registry=self.client)
        self.assertEqual(
            [(f.path, f.version) for f in result.findings], [("b/go.mod", "v1.1.0")]
        )
        self.assertCountEqual(
            self.calls,
            ["example.com/lib/@v/v1.0.0.info", "example.com/lib/@v/v1.1.0.info"],
        )

    def test_disk_cache_separates_versions_and_reuses_each_result(self):
        self.require("v1.0.0")
        self.routes["example.com/lib/@v/v1.0.0.info"] = info("v1.0.0")
        first = scan_path(self.root, registry=self.client, cache_file=self.cache_file)
        self.require("v1.1.0")
        second = scan_path(self.root, registry=self.client, cache_file=self.cache_file)
        third = scan_path(self.root, registry=self.client, cache_file=self.cache_file)
        self.assertEqual(first.findings, [])
        self.assertEqual(
            [(f.version, f.status) for f in second.findings], [("v1.1.0", "not_found")]
        )
        self.assertEqual(second.findings, third.findings)
        self.assertEqual(
            self.calls,
            ["example.com/lib/@v/v1.0.0.info", "example.com/lib/@v/v1.1.0.info"],
        )
        payload = json.loads(self.cache_file.read_text())
        self.assertEqual(payload["version"], 4)
        self.assertEqual(
            payload["entries"]["go:example.com/lib@v1.0.0"]["status"], "found"
        )
        self.assertEqual(
            payload["entries"]["go:example.com/lib@v1.1.0"]["status"], "not_found"
        )

    def test_old_go_caches_are_invalidated_without_discarding_npm_or_pypi(self):
        self.require("v1.2.99")
        (self.root / "package.json").write_text('{"dependencies":{"express":"1.0.0"}}')
        (self.root / "requirements.txt").write_text("requests\n")
        for schema in (1, 2, 3):
            with self.subTest(schema=schema):
                self.calls.clear()
                self.cache_file.write_text(
                    json.dumps(
                        {
                            "version": schema,
                            "entries": {
                                key: {
                                    "status": "found",
                                    "message": "Old result.",
                                    "checked_at": time.time(),
                                }
                                for key in (
                                    "go:example.com/lib",
                                    "npm:express",
                                    "pypi:requests",
                                )
                            },
                        }
                    )
                )
                result = scan_path(
                    self.root, registry=self.client, cache_file=self.cache_file
                )
                self.assertEqual(
                    [(f.version, f.status) for f in result.findings],
                    [("v1.2.99", "not_found")],
                )
                self.assertEqual(self.calls, ["example.com/lib/@v/v1.2.99.info"])
                entries = json.loads(self.cache_file.read_text())["entries"]
                self.assertNotIn("go:example.com/lib", entries)
                self.assertIn("npm:express", entries)
                self.assertIn("pypi:requests", entries)

    def test_module_and_version_uppercase_and_plus_are_escaped(self):
        self.require("v1.0.0-RC1+incompatible", "github.com/BurntSushi/toml")
        expected = "github.com/!burnt!sushi/toml/@v/v1.0.0-!r!c1%2Bincompatible.info"
        self.routes[expected] = info("v1.0.0-RC1+incompatible")
        self.assertEqual(scan_path(self.root, registry=self.client).findings, [])
        self.assertEqual(self.calls, [expected])

    def test_prerelease_version_case_remains_distinct_in_cache(self):
        self.require("v1.0.0-RC1", subdir="a")
        self.require("v1.0.0-rc1", subdir="b")
        self.routes["example.com/lib/@v/v1.0.0-!r!c1.info"] = info("v1.0.0-RC1")
        result = scan_path(self.root, registry=self.client, cache_file=self.cache_file)
        self.assertEqual(
            [(f.version, f.status) for f in result.findings],
            [("v1.0.0-rc1", "not_found")],
        )
        entries = json.loads(self.cache_file.read_text())["entries"]
        self.assertIn("go:example.com/lib@v1.0.0-RC1", entries)
        self.assertIn("go:example.com/lib@v1.0.0-rc1", entries)

    def test_go_404_and_410_are_missing_for_response_and_exception_paths(self):
        self.require()
        path = "example.com/lib/@v/v1.0.0.info"
        for status in (404, 410):
            for raise_error in (False, True):
                with self.subTest(status=status, raise_error=raise_error):
                    self.calls.clear()
                    self.routes[path] = (
                        HTTPError(PROXY + path, status, "Unavailable", {}, None)
                        if raise_error
                        else Response(status)
                    )
                    result = scan_path(self.root, registry=self.client)
                    self.assertEqual([f.status for f in result.findings], ["not_found"])
                    self.assertIn("public Go proxy", result.findings[0].reason)
                    self.assertEqual(self.calls, [path])

    def test_npm_and_pypi_keep_the_two_argument_interface_and_endpoints(self):
        for ecosystem, name, expected in (
            ("npm", "@scope/package", "https://registry.npmjs.org/@scope/package"),
            ("pypi", "requests", "https://pypi.org/pypi/requests/json"),
        ):
            with self.subTest(ecosystem=ecosystem):
                calls = []

                def opener(request, timeout, calls=calls):
                    calls.append(request.full_url)
                    return Response()

                result = PublicRegistryClient(opener=opener).check(ecosystem, name)
                self.assertEqual(result.status, "found")
                self.assertEqual(calls, [expected])

    def test_non_go_410_remains_an_error(self):
        client = PublicRegistryClient(
            opener=lambda *args, **kwargs: Response(410), max_retries=0
        )
        self.assertEqual(client.check("npm", "express").status, "error")
        self.assertEqual(client.check("pypi", "requests").status, "error")

    def test_rate_limits_and_server_errors_retry_the_same_exact_version(self):
        self.require()
        for status in (429, 503):
            with self.subTest(status=status):
                calls, sleeps = [], []

                def opener(request, timeout, calls=calls, status=status):
                    calls.append(request.full_url)
                    if len(calls) == 1:
                        raise HTTPError(request.full_url, status, "Retry", {}, None)
                    return info("v1.0.0")

                client = PublicRegistryClient(
                    opener=opener, max_retries=1, sleep_fn=sleeps.append
                )
                self.assertEqual(scan_path(self.root, registry=client).findings, [])
                self.assertEqual(calls, [PROXY + "example.com/lib/@v/v1.0.0.info"] * 2)
                self.assertEqual(sleeps, [1.0])

    def test_transient_errors_do_not_become_clean_or_persistent_results(self):
        self.require()
        for failure in (Response(503), URLError("offline"), TimeoutError("timed out")):
            with self.subTest(failure=type(failure).__name__):
                self.calls.clear()
                self.routes["example.com/lib/@v/v1.0.0.info"] = failure
                self.routes["example.com/lib/@v/list"] = failure
                first = scan_path(
                    self.root, registry=self.client, cache_file=self.cache_file
                )
                second = scan_path(
                    self.root, registry=self.client, cache_file=self.cache_file
                )
                self.assertEqual([f.status for f in first.findings], ["error"])
                self.assertEqual([f.severity for f in second.findings], ["warning"])
                self.assertEqual(len(self.calls), 2)
                self.assertEqual(json.loads(self.cache_file.read_text())["entries"], {})

    def test_offline_does_not_lookup_execute_or_rewrite_cache(self):
        self.require(PSEUDO)
        cached = json.dumps(
            {
                "version": 3,
                "entries": {
                    f"go:example.com/lib@{PSEUDO}": {
                        "status": "found",
                        "message": "Cached.",
                        "checked_at": time.time(),
                    },
                },
            }
        )
        self.cache_file.write_text(cached)
        with (
            patch(
                "socket.create_connection",
                side_effect=AssertionError("network prohibited"),
            ),
            patch(
                "subprocess.Popen", side_effect=AssertionError("execution prohibited")
            ),
        ):
            first = scan_path(
                self.root,
                registry=self.client,
                offline=True,
                cache_file=self.cache_file,
            )
            second = scan_path(
                self.root,
                registry=self.client,
                offline=True,
                cache_file=self.cache_file,
            )
        self.assertEqual(first, second)
        self.assertEqual(
            [(f.version, f.status) for f in first.findings], [(PSEUDO, "unknown")]
        )
        self.assertEqual(self.calls, [])
        self.assertEqual(self.cache_file.read_text(), cached)

    def test_local_replacement_is_not_looked_up(self):
        self.require(extra="replace example.com/lib => ../local\n")
        result = scan_path(self.root, registry=self.client)
        self.assertEqual(result.findings, [])
        self.assertEqual(result.references_scanned, 0)
        self.assertEqual(self.calls, [])

    def test_remote_replacement_checks_the_effective_module(self):
        self.require(
            "v0.0.0", extra="replace example.com/lib => example.com/fork v1.2.3\n"
        )
        self.routes["example.com/lib/@v/list"] = Response(404)
        self.routes["example.com/fork/@v/v1.2.3.info"] = info("v1.2.3")
        result = scan_path(self.root, registry=self.client)
        self.assertEqual(result.findings, [])
        self.assertEqual(self.calls, ["example.com/fork/@v/v1.2.3.info"])

    def test_remote_missing_version_reports_replacement_location(self):
        self.require(extra="replace example.com/lib => example.com/fork v1.2.99\n")
        result = scan_path(self.root, registry=self.client)
        self.assertEqual(
            [
                (f.package_name, f.version, f.path, f.line, f.status)
                for f in result.findings
            ],
            [("example.com/fork", "v1.2.99", "go.mod", 3, "not_found")],
        )
        references = extract_references("go.mod", (self.root / "go.mod").read_text())
        self.assertEqual(references[0].source, "replace")

    def test_same_module_replacement_changes_the_checked_version(self):
        self.require(
            "v1.2.99", extra="replace example.com/lib => example.com/lib v1.0.0\n"
        )
        self.routes["example.com/lib/@v/v1.0.0.info"] = info("v1.0.0")
        self.assertEqual(scan_path(self.root, registry=self.client).findings, [])
        self.assertEqual(self.calls, ["example.com/lib/@v/v1.0.0.info"])

    def test_unused_replacements_do_not_create_dependencies(self):
        (self.root / "go.mod").write_text(
            "module example.com/app\n"
            "replace example.com/lib => example.com/fork v1.0.0\n"
        )
        result = scan_path(self.root, registry=self.client)
        self.assertEqual(result.references_scanned, 0)
        self.assertEqual(self.calls, [])

    def test_specific_replacement_takes_precedence_regardless_of_order(self):
        local = "replace example.com/lib => ../local\n"
        remote = "replace example.com/lib v1.0.0 => example.com/fork v1.2.3\n"
        self.routes["example.com/fork/@v/v1.2.3.info"] = info("v1.2.3")
        for extra in (local + remote, remote + local):
            with self.subTest(extra=extra):
                self.calls.clear()
                self.require(extra=extra)
                result = scan_path(self.root, registry=self.client)
                self.assertEqual(result.references_scanned, 1)
                self.assertEqual(result.findings, [])
                self.assertEqual(self.calls, ["example.com/fork/@v/v1.2.3.info"])

    def test_specific_local_replacement_beats_remote_wildcard(self):
        self.require(
            extra=(
                "replace (\n"
                " example.com/lib => example.com/fork v1.0.0\n"
                " example.com/lib v1.0.0 => ../local\n)\n"
            )
        )
        self.assertEqual(
            scan_path(self.root, registry=self.client).references_scanned, 0
        )
        self.assertEqual(self.calls, [])

    def test_replacements_are_not_chained(self):
        self.require(
            extra=(
                "replace example.com/lib => example.com/fork v1.0.0\n"
                "replace example.com/fork => example.com/other v1.1.0\n"
            )
        )
        self.routes["example.com/fork/@v/v1.0.0.info"] = info("v1.0.0")
        self.assertEqual(scan_path(self.root, registry=self.client).findings, [])
        self.assertEqual(self.calls, ["example.com/fork/@v/v1.0.0.info"])

    def test_actual_missing_version_reaches_all_output_formats(self):
        self.require("v1.2.99")
        findings = scan_path(self.root, registry=self.client).findings
        self.assertIn("go.mod:2 go:example.com/lib@v1.2.99", render_text(findings))
        self.assertEqual(
            json.loads(render_json(findings))["findings"][0]["version"], "v1.2.99"
        )
        result = json.loads(render_sarif(findings))["runs"][0]["results"][0]
        self.assertIn("example.com/lib@v1.2.99", result["message"]["text"])
        self.assertEqual(
            result["locations"][0]["physicalLocation"]["region"]["startLine"], 2
        )

    def test_cli_fails_for_missing_version_and_succeeds_for_present_version(self):
        self.routes["example.com/lib/@v/v1.0.0.info"] = info("v1.0.0")
        for version, expected_code in (("v1.0.0", 0), ("v1.2.99", 1)):
            with self.subTest(version=version):
                self.require(version)
                output = io.StringIO()
                with (
                    patch(
                        "ai_dependency_guard.cli.PublicRegistryClient",
                        return_value=self.client,
                    ),
                    redirect_stdout(output),
                ):
                    code = main(["scan", str(self.root), "--json"])
                self.assertEqual(code, expected_code)
                self.assertEqual(
                    json.loads(output.getvalue())["summary"]["not_found"], expected_code
                )

    def test_unversioned_go_lookup_remains_backwards_compatible(self):
        self.routes["example.com/lib/@v/list"] = Response(200, b"")
        self.assertEqual(self.client.check("go", "example.com/lib").status, "found")
        self.assertEqual(self.calls, ["example.com/lib/@v/list"])


if __name__ == "__main__":
    unittest.main()
