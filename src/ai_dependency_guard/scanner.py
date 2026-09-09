"""Repository discovery and dependency verification."""

from __future__ import annotations

import fnmatch
import json
import time
from pathlib import Path

from .extractors import ExtractionError, extract_references
from .models import Finding, RegistryResult, ScanResult
from .registry import PublicRegistryClient


_SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    "__pycache__",
    ".tox",
}
_SUPPORTED_NAMES = {
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "readme.md",
    "agents.md",
    "claude.md",
    ".cursorrules",
}
_SUPPORTED_SUFFIXES = {".md", ".mdc", ".json", ".yml", ".yaml", ".sh", ".ps1", ".txt", ".toml"}


def _is_supported_file(path: Path) -> bool:
    name = path.name.lower()
    return name in _SUPPORTED_NAMES or path.suffix.lower() in _SUPPORTED_SUFFIXES


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in _SKIP_DIRECTORIES for part in relative_parts):
            continue
        if _is_supported_file(path):
            yield path


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_ignored_path(relative: str, patterns: set[str]) -> bool:
    for raw_pattern in patterns:
        pattern = raw_pattern.strip().replace("\\", "/")
        if not pattern:
            continue
        if fnmatch.fnmatch(relative, pattern):
            return True
        directory = pattern.rstrip("/")
        if relative.startswith(f"{directory}/"):
            return True
    return False


def _finding_for_reference(reference, result: RegistryResult) -> Finding:
    if result.status == "not_found":
        suggestion = "Verify the exact package name before installing it."
        severity = "error"
    elif result.status == "unknown":
        suggestion = "Run the scan again with network access before treating this as clean."
        severity = "warning"
    else:
        suggestion = "Retry the scan and inspect the registry status before installing it."
        severity = "warning"
    return Finding(
        package_name=reference.name,
        ecosystem=reference.ecosystem,
        path=reference.path,
        line=reference.line,
        status=result.status,
        reason=result.message,
        suggestion=suggestion,
        severity=severity,
    )


def _finding_for_extraction_error(path: str, error: ExtractionError) -> Finding:
    return Finding(
        package_name="<file-parse-error>",
        ecosystem="file",
        path=path,
        line=1,
        status="error",
        reason=str(error),
        suggestion="Fix the file syntax and run the scan again.",
        severity="error",
    )


def _load_registry_cache(cache_file: Path | None, cache_ttl: float) -> dict:
    if cache_file is None or not cache_file.exists():
        return {}
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    now = time.time()
    entries = payload.get("entries", {})
    if not isinstance(entries, dict):
        return {}
    valid = {}
    for key, value in entries.items():
        if not isinstance(value, dict):
            continue
        checked_at = value.get("checked_at")
        if not isinstance(checked_at, (int, float)) or now - checked_at > cache_ttl:
            continue
        if value.get("status") not in {"found", "not_found"}:
            continue
        if not isinstance(value.get("message"), str):
            continue
        valid[key] = value
    return valid


def _save_registry_cache(cache_file: Path | None, entries: dict) -> None:
    if cache_file is None:
        return
    payload = {"version": 1, "entries": entries}
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_file.with_name(f"{cache_file.name}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(cache_file)
    except OSError:
        # A cache is an optimization; a read-only filesystem must not hide findings.
        return


def scan_path(
    root: str | Path,
    registry: PublicRegistryClient | None = None,
    offline: bool = False,
    ignore_packages: set[str] | None = None,
    ignore_paths: set[str] | None = None,
    cache_file: str | Path | None = None,
    cache_ttl: float = 86400.0,
) -> ScanResult:
    """Scan a repository path without running project commands."""

    root_path = Path(root).resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"Scan path does not exist: {root}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"Scan path is not a directory: {root}")

    registry_client = registry or PublicRegistryClient()
    ignored = {item.strip().lower() for item in (ignore_packages or set()) if item.strip()}
    ignored_paths = {item for item in (ignore_paths or set()) if item.strip()}
    result = ScanResult()
    lookup_cache: dict[tuple[str, str], RegistryResult] = {}
    disk_cache_path = Path(cache_file).resolve() if cache_file is not None else None
    disk_cache = _load_registry_cache(disk_cache_path, cache_ttl)

    for file_path in _iter_files(root_path):
        relative = _relative_path(file_path, root_path)
        if _is_ignored_path(relative, ignored_paths):
            continue
        result.files_scanned += 1
        try:
            content = file_path.read_text(encoding="utf-8")
            references = extract_references(relative, content)
        except (UnicodeDecodeError, OSError) as exc:
            result.findings.append(
                Finding(
                    package_name="<file-read-error>",
                    ecosystem="file",
                    path=relative,
                    line=1,
                    status="error",
                    reason=f"Could not read file: {exc}.",
                    suggestion="Check the file encoding and permissions.",
                    severity="error",
                )
            )
            continue
        except ExtractionError as exc:
            result.findings.append(_finding_for_extraction_error(relative, exc))
            continue

        result.references_scanned += len(references)
        for reference in references:
            if reference.name.lower() in ignored:
                continue
            key = (reference.ecosystem, reference.name.lower())
            if key not in lookup_cache:
                cache_key = f"{reference.ecosystem}:{reference.name.lower()}"
                cached = disk_cache.get(cache_key)
                if offline:
                    lookup_cache[key] = RegistryResult(
                        "unknown", "Offline mode skipped registry lookup."
                    )
                elif cached:
                    lookup_cache[key] = RegistryResult(cached["status"], cached["message"])
                else:
                    lookup_cache[key] = registry_client.check(reference.ecosystem, reference.name)
                    registry_result = lookup_cache[key]
                    if registry_result.status in {"found", "not_found"}:
                        disk_cache[cache_key] = {
                            "status": registry_result.status,
                            "message": registry_result.message,
                            "checked_at": time.time(),
                        }
            registry_result = lookup_cache[key]
            if registry_result.status != "found":
                result.findings.append(_finding_for_reference(reference, registry_result))

    if not offline:
        _save_registry_cache(disk_cache_path, disk_cache)
    return result
