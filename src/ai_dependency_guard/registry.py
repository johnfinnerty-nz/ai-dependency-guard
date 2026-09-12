"""Public npm, PyPI, and Go registry lookups."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import RegistryResult

# Canonical Go versions have three numeric components and only +incompatible
# build metadata. Noncanonical revisions need Go's resolver, not a guessed match.
_GO_CANONICAL_VERSION = re.compile(
    r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+incompatible)?"
)
_GO_INFO_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


def _is_canonical_go_version(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = _GO_CANONICAL_VERSION.fullmatch(value)
    if match is None:
        return False
    prerelease = match.group("prerelease")
    return prerelease is None or all(
        not (part.isdigit() and len(part) > 1 and part.startswith("0"))
        for part in prerelease.split(".")
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant: {value}")


def _validate_go_info(payload: bytes | str, requested_version: str) -> str | None:
    """Return an error for invalid exact-version metadata, otherwise None."""

    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        info = json.loads(text, parse_constant=_reject_json_constant)
    except (UnicodeError, ValueError, TypeError):
        return "response is not a UTF-8 JSON object"
    if not isinstance(info, dict) or not _is_canonical_go_version(info.get("Version")):
        return "response has no canonical Version string"
    if info["Version"] != requested_version:
        return "response Version does not match the requested canonical version"
    if "Time" in info:
        timestamp = info["Time"]
        if not isinstance(timestamp, str) or _GO_INFO_TIME.fullmatch(timestamp) is None:
            return "response Time is not an RFC 3339 timestamp"
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return "response Time is not an RFC 3339 timestamp"
    return None


def _escape_go_proxy_path(path: str) -> str:
    """Apply the Go proxy's case-sensitive module path or version escaping."""

    return "".join(
        f"!{character.lower()}" if "A" <= character <= "Z" else character
        for character in path
    )


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

    def _url(self, ecosystem: str, name: str, version: str = "") -> str | None:
        base = self._BASE_URLS.get(ecosystem)
        if base is None:
            return None
        if ecosystem == "go":
            encoded_name = quote(_escape_go_proxy_path(name), safe="/!")
            if version:
                encoded_version = quote(_escape_go_proxy_path(version), safe="!")
                suffix = f"/@v/{encoded_version}.info"
            else:
                suffix = "/@v/list"
        else:
            safe = "@/" if ecosystem == "npm" else ""
            encoded_name = quote(name, safe=safe)
            suffix = "/json" if ecosystem == "pypi" else ""
        return f"{base}{encoded_name}{suffix}"

    def check(self, ecosystem: str, name: str, version: str = "") -> RegistryResult:
        """Check existence, including the requested version for Go when supplied."""

        if ecosystem == "go" and version and not _is_canonical_go_version(version):
            return RegistryResult(
                "error",
                "Go version is not canonical; resolve it with Go tooling before scanning. "
                "This scanner does not resolve revision names or version shorthand.",
            )
        url = self._url(ecosystem, name, version)
        if url is None:
            return RegistryResult("error", f"Unsupported package ecosystem: {ecosystem}.")

        not_found_message = f"Package was not found in the {ecosystem} registry."
        found_message = "Package exists in the public registry."
        if ecosystem == "go":
            module = f"{name}@{version}" if version else name
            not_found_message = f"Module {module} was not found in the public Go proxy."
            found_message = f"Module {module} exists in the public Go proxy."

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
                        if int(status) == 404 or (ecosystem == "go" and int(status) == 410):
                            return RegistryResult(
                                "not_found",
                                not_found_message,
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
                    if ecosystem == "go" and version and status != 200:
                        return RegistryResult(
                            "error", "The Go proxy metadata endpoint did not return HTTP 200."
                        )
                    payload = response.read()
                    if ecosystem == "go" and version:
                        error = _validate_go_info(payload, version)
                        if error is not None:
                            return RegistryResult("error", f"Invalid Go proxy metadata: {error}.")
                    return RegistryResult("found", found_message)
            except HTTPError as exc:
                if exc.code == 404 or (ecosystem == "go" and exc.code == 410):
                    return RegistryResult("not_found", not_found_message)
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
