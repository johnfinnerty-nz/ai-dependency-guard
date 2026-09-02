"""Data structures shared by the scanner, registry clients, and renderers."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PackageReference:
    ecosystem: str
    name: str
    path: str
    line: int
    source: str = ""


@dataclass(frozen=True)
class RegistryResult:
    status: str
    message: str


@dataclass(frozen=True)
class Finding:
    package_name: str
    ecosystem: str
    path: str
    line: int
    status: str
    reason: str
    suggestion: str
    severity: str = "error"


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    references_scanned: int = 0
