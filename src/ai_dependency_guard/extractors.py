"""Extract dependency references without executing project code."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .models import PackageReference


class ExtractionError(ValueError):
    """Raised when a supported structured file cannot be parsed."""


_DEPENDENCY_SECTIONS = {
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
}
_NPM_COMMAND = re.compile(
    r"\b(?P<command>npm\s+(?:install|i)|pnpm\s+(?:add|dlx)|"
    r"yarn\s+(?:add|dlx)|bun\s+(?:add|x)|npx)\b(?P<args>[^\r\n]*)",
    re.IGNORECASE,
)
_NPM_OPTIONS_WITH_VALUE = {
    "--prefix",
    "--registry",
    "--userconfig",
    "--globalconfig",
    "--cache",
    "--workspace",
    "-w",
    "--filter",
}
_PIP_COMMAND = re.compile(r"\bpip(?:3)?\s+install\b(?P<args>[^\r\n]*)", re.IGNORECASE)
_PIP_OPTIONS_WITH_VALUE = {
    "-r",
    "--requirement",
    "-c",
    "--constraint",
    "-e",
    "--editable",
    "--index-url",
    "--extra-index-url",
    "--trusted-host",
    "--proxy",
    "--timeout",
    "--retries",
    "--target",
    "--platform",
    "--python-version",
    "--implementation",
    "--abi",
    "--root",
    "--prefix",
    "--src",
    "--log",
    "--cert",
    "--client-cert",
    "--config-settings",
    "--progress-bar",
    "--exists-action",
}
_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*|@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"(?:\[[^\]]+\])?\s*(?:===|==|~=|!=|>=|<=|>|<|;|$)"
)
_JSON_DEPENDENCY_KEY = re.compile(r'^\s*"(?P<name>@?[^"\\]+)"\s*:')
_POETRY_DEPENDENCY_KEY = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_.-]+)\s*=\s*"
)


def _strip_npm_version(token: str) -> str:
    token = token.strip().strip("\"'`;,)")
    if token.startswith("@"):
        slash = token.find("/")
        if slash >= 0:
            version_at = token.find("@", slash)
            if version_at >= 0:
                return token[:version_at]
        return token
    if "@" in token:
        return token.split("@", 1)[0]
    return token


def _is_non_registry_target(token: str) -> bool:
    lowered = token.lower().strip()
    return (
        not lowered
        or lowered.startswith("-")
        or lowered in {"&&", "||", ";", "|"}
        or lowered.startswith("./")
        or lowered.startswith("../")
        or lowered.startswith(".")
        or lowered.startswith("/")
        or lowered.startswith("~")
        or lowered.startswith("file:")
        or lowered.startswith("git+")
        or lowered.startswith("git://")
        or lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.endswith(".tgz")
        or lowered.endswith(".whl")
    )


def _tokens_after_command(arguments: str) -> list[str]:
    tokens: list[str] = []
    for token in arguments.replace("\t", " ").split():
        if token in {"&&", "||", ";", "|"}:
            break
        tokens.append(token)
    return tokens


def _pip_install_targets(arguments: str) -> list[str]:
    """Return package targets while consuming pip options that take a value."""

    targets: list[str] = []
    skip_next = False
    for token in _tokens_after_command(arguments):
        lowered = token.lower()
        if skip_next:
            skip_next = False
            continue
        if lowered in _PIP_OPTIONS_WITH_VALUE:
            skip_next = True
            continue
        if any(
            lowered.startswith(f"{option}=")
            for option in _PIP_OPTIONS_WITH_VALUE
            if option.startswith("--")
        ):
            continue
        if (lowered.startswith("-r") or lowered.startswith("-c") or lowered.startswith("-e")) and len(
            token
        ) > 2:
            continue
        if _is_non_registry_target(token):
            continue
        targets.append(token)
    return targets


def _single_npm_target(arguments: str) -> list[str]:
    """Return the executable package for npx/pnpm dlx-style commands."""

    skip_next = False
    for token in _tokens_after_command(arguments):
        lowered = token.lower()
        if skip_next:
            skip_next = False
            continue
        if lowered in _NPM_OPTIONS_WITH_VALUE:
            skip_next = True
            continue
        if any(
            lowered.startswith(f"{option}=")
            for option in _NPM_OPTIONS_WITH_VALUE
            if option.startswith("--")
        ):
            continue
        if _is_non_registry_target(token):
            continue
        return [token]
    return []


def _extract_command_references(path: str, content: str) -> list[PackageReference]:
    references: list[PackageReference] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        for match in _NPM_COMMAND.finditer(line):
            command = match.group("command").lower().strip()
            tokens = _tokens_after_command(match.group("args"))
            if command == "npx" or command.endswith("dlx") or command == "bun x":
                tokens = _single_npm_target(match.group("args"))
            for token in tokens:
                if _is_non_registry_target(token):
                    continue
                package_name = _strip_npm_version(token)
                if package_name and not _is_non_registry_target(package_name):
                    references.append(
                        PackageReference("npm", package_name, path, line_number, command)
                    )

        for match in _PIP_COMMAND.finditer(line):
            for token in _pip_install_targets(match.group("args")):
                requirement = _REQUIREMENT.match(token)
                if requirement:
                    references.append(
                        PackageReference(
                            "pypi", requirement.group("name"), path, line_number, "pip install"
                        )
                    )
    return references


def _extract_package_json(path: str, content: str) -> list[PackageReference]:
    try:
        document = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Invalid JSON: {exc.msg} at line {exc.lineno}.") from exc

    if not isinstance(document, dict):
        return []

    references: list[PackageReference] = []
    active_section: str | None = None
    section_depth = 0
    for line_number, line in enumerate(content.splitlines(), start=1):
        header = re.search(r'"(?P<section>[A-Za-z]+)"\s*:\s*\{', line)
        if header and header.group("section") in _DEPENDENCY_SECTIONS:
            inline_body = re.search(r'\{(?P<body>[^{}]*)\}', line[header.end() - 1 :])
            if inline_body:
                for key_match in re.finditer(r'"(?P<name>@?[^"\\]+)"\s*:', inline_body.group("body")):
                    references.append(
                        PackageReference(
                            "npm",
                            key_match.group("name"),
                            path,
                            line_number,
                            header.group("section"),
                        )
                    )
                continue
            active_section = header.group("section")
            section_depth = line.count("{") - line.count("}")
            continue

        if active_section:
            key_match = _JSON_DEPENDENCY_KEY.match(line)
            if key_match:
                references.append(
                    PackageReference(
                        "npm", key_match.group("name"), path, line_number, active_section
                    )
                )
            section_depth += line.count("{") - line.count("}")
            if section_depth <= 0:
                active_section = None
    return references


def _requirement_from_value(value: str) -> str | None:
    value = value.strip().strip("\"'")
    match = _REQUIREMENT.match(value)
    return match.group("name") if match else None


def _extract_requirements(path: str, content: str) -> list[PackageReference]:
    references: list[PackageReference] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("-", "--")) or _is_non_registry_target(stripped):
            continue
        match = _REQUIREMENT.match(stripped)
        if match:
            references.append(
                PackageReference("pypi", match.group("name"), path, line_number, "requirements")
            )
    return references


def _extract_pyproject(path: str, content: str) -> list[PackageReference]:
    try:
        document = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ExtractionError(f"Invalid TOML: {exc}") from exc

    references: list[PackageReference] = []
    section: str | None = None
    in_project_dependencies = False
    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        section_match = re.match(r"\[(?P<section>[^\]]+)\]", stripped)
        if section_match:
            section = section_match.group("section")
            in_project_dependencies = False
            continue

        if section == "project" and stripped.startswith("dependencies") and "[" in stripped:
            in_project_dependencies = True
        if in_project_dependencies:
            for value in re.findall(r"[\"']([^\"']+)[\"']", line):
                name = _requirement_from_value(value)
                if name:
                    references.append(PackageReference("pypi", name, path, line_number, "project"))
            if "]" in stripped:
                in_project_dependencies = False

        if section == "tool.poetry.dependencies":
            match = _POETRY_DEPENDENCY_KEY.match(line)
            if match and match.group("name").lower() != "python":
                references.append(
                    PackageReference(
                        "pypi", match.group("name"), path, line_number, "poetry"
                    )
                )
    return references


def extract_references(path: str | Path, content: str) -> list[PackageReference]:
    """Extract registry package references from one file."""

    display_path = str(path).replace("\\", "/")
    name = Path(display_path).name.lower()
    if name == "package.json":
        references = _extract_package_json(display_path, content)
    elif name == "requirements.txt" or name.startswith("requirements-") and name.endswith(".txt"):
        references = _extract_requirements(display_path, content)
    elif name == "pyproject.toml":
        references = _extract_pyproject(display_path, content)
    else:
        references = []

    return references + _extract_command_references(display_path, content)
