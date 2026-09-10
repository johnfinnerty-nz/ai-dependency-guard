import unittest
from urllib.error import HTTPError, URLError

from ai_dependency_guard.registry import PublicRegistryClient


class FakeResponse:
    def __init__(self, status=200, payload=b"{}"):
        self.status = status
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class RegistryTests(unittest.TestCase):
    def test_reports_existing_npm_package(self):
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            return FakeResponse(200, b'{"name":"express"}')

        client = PublicRegistryClient(opener=opener)

        result = client.check("npm", "express")

        self.assertEqual(result.status, "found")
        self.assertEqual(calls[0][0], "https://registry.npmjs.org/express")
        self.assertGreater(calls[0][1], 0)

    def test_reports_missing_pypi_package(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 404, "Not Found", {}, None)

        client = PublicRegistryClient(opener=opener)

        result = client.check("pypi", "definitely-not-a-real-python-package-12345")

        self.assertEqual(result.status, "not_found")
        self.assertIn("not found", result.message.lower())

    def test_uses_pypi_json_endpoint_for_existence_checks(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(200, b'{"info":{"name":"requests"}}')

        client = PublicRegistryClient(opener=opener)

        result = client.check("pypi", "requests")

        self.assertEqual(result.status, "found")
        self.assertEqual(calls, ["https://pypi.org/pypi/requests/json"])

    def test_uses_go_proxy_uppercase_escaping(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(200, b"v1.4.0\n")

        client = PublicRegistryClient(opener=opener)

        result = client.check("go", "github.com/BurntSushi/toml")

        self.assertEqual(result.status, "found")
        self.assertEqual(
            calls,
            ["https://proxy.golang.org/github.com/!burnt!sushi/toml/@v/list"],
        )

    def test_retries_rate_limit_then_succeeds(self):
        attempts = []
        sleeps = []

        def opener(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) == 1:
                raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
            return FakeResponse(200, b'{"info":{"name":"requests"}}')

        client = PublicRegistryClient(opener=opener, max_retries=1, sleep_fn=sleeps.append)

        result = client.check("pypi", "requests")

        self.assertEqual(result.status, "found")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [1.0])

    def test_reports_network_error_without_treating_it_as_safe(self):
        def opener(request, timeout):
            raise URLError("offline")

        client = PublicRegistryClient(opener=opener, max_retries=0)

        result = client.check("npm", "express")

        self.assertEqual(result.status, "error")
        self.assertIn("offline", result.message.lower())

    def test_reports_timeout_without_treating_it_as_safe(self):
        def opener(request, timeout):
            raise TimeoutError("timed out")

        client = PublicRegistryClient(opener=opener, max_retries=0)

        result = client.check("npm", "express")

        self.assertEqual(result.status, "error")
        self.assertIn("timed out", result.message.lower())


if __name__ == "__main__":
    unittest.main()
