"""Public npm and PyPI registry lookups."""

from __future__ import annotations

import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import RegistryResult


class PublicRegistryClient:
    """Look up package existence without requiring credentials."""

    _BASE_URLS = {
        "npm": "https://registry.npmjs.org/",
        "pypi": "https://pypi.org/pypi/",
        "go": "https://proxy.golang.org/",
    }

    def __init__(
        self,
        opener: Callable[..., object] = urlopen,
        timeout: float = 5.0,
        max_retries: int = 2,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.opener = opener
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.sleep_fn = sleep_fn

    def _url(self, ecosystem: str, name: str) -> str | None:
        base = self._BASE_URLS.get(ecosystem)
        if base is None:
            return None
        safe = "@/" if ecosystem in {"npm", "go"} else ""
        encoded_name = quote(name, safe=safe)
        suffix = "/json" if ecosystem == "pypi" else "/@v/list" if ecosystem == "go" else ""
        return f"{base}{encoded_name}{suffix}"

    def check(self, ecosystem: str, name: str) -> RegistryResult:
        url = self._url(ecosystem, name)
        if url is None:
            return RegistryResult("error", f"Unsupported package ecosystem: {ecosystem}.")

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "ai-dependency-guard/0.1",
            },
        )
        for attempt in range(self.max_retries + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", None)
                    if status is None and hasattr(response, "getcode"):
                        status = response.getcode()
                    if status is not None and int(status) >= 400:
                        if int(status) == 404:
                            return RegistryResult(
                                "not_found",
                                f"Package was not found in the {ecosystem} registry.",
                            )
                        if int(status) == 429 or int(status) >= 500:
                            if attempt < self.max_retries:
                                self.sleep_fn(float(2**attempt))
                                continue
                            return RegistryResult(
                                "error",
                                f"The {ecosystem} registry returned HTTP {status} after retries.",
                            )
                        return RegistryResult(
                            "error", f"The {ecosystem} registry returned HTTP {status}."
                        )
                    response.read()
                    return RegistryResult("found", "Package exists in the public registry.")
            except HTTPError as exc:
                if exc.code == 404:
                    return RegistryResult(
                        "not_found", f"Package was not found in the {ecosystem} registry."
                    )
                if exc.code == 429 or exc.code >= 500:
                    if attempt < self.max_retries:
                        self.sleep_fn(float(2**attempt))
                        continue
                    return RegistryResult(
                        "error",
                        f"The {ecosystem} registry returned HTTP {exc.code} after retries.",
                    )
                return RegistryResult("error", f"The {ecosystem} registry returned HTTP {exc.code}.")
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    self.sleep_fn(float(2**attempt))
                    continue
                return RegistryResult("error", f"Registry lookup failed: {exc}.")
            except Exception as exc:  # pragma: no cover - defensive boundary for custom openers
                return RegistryResult("error", f"Registry lookup failed: {exc}.")

        return RegistryResult("error", "Registry lookup failed after retries.")
