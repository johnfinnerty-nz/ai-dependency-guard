"""Human and machine-readable output formats."""

from __future__ import annotations

import json
from dataclasses import asdict

from .models import Finding


def render_text(findings: list[Finding]) -> str:
    if not findings:
        return "No dependency findings."

    lines = [f"Found {len(findings)} dependency finding(s):"]
    for finding in findings:
        package = finding.package_name
        if finding.version:
            package = f"{package}@{finding.version}"
        lines.append(
            f"[{finding.severity.upper()}] {finding.path}:{finding.line} "
            f"{finding.ecosystem}:{package}"
        )
        lines.append(f"  Reason: {finding.reason}")
        lines.append(f"  Suggestion: {finding.suggestion}")
    return "\n".join(lines)


def _summary(findings: list[Finding]) -> dict[str, int]:
    summary = {"total": len(findings), "not_found": 0, "unknown": 0, "error": 0}
    for finding in findings:
        if finding.status in summary:
            summary[finding.status] += 1
    return summary


def render_json(findings: list[Finding]) -> str:
    payload = {"summary": _summary(findings), "findings": [asdict(item) for item in findings]}
    return json.dumps(payload, indent=2, sort_keys=True)


def render_sarif(findings: list[Finding]) -> str:
    results = []
    for finding in findings:
        results.append(
            {
                "ruleId": f"{finding.ecosystem}/{finding.status}",
                "level": finding.severity,
                "message": {
                    "text": (
                        f"{finding.ecosystem}:{finding.package_name}"
                        f"{('@' + finding.version) if finding.version else ''}: "
                        f"{finding.reason} {finding.suggestion}"
                    )
                },
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": finding.path},
                            "region": {"startLine": finding.line},
                        }
                    }
                ],
            }
        )
    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "ai-dependency-guard", "version": "0.1.0"}},
                "results": results,
            }
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_github_annotations(findings: list[Finding]) -> str:
    lines = []
    for finding in findings:
        title = f"{finding.ecosystem}:{finding.package_name}"
        message = f"{finding.reason} {finding.suggestion}".replace("\n", " ")
        command = "error" if finding.severity == "error" else "warning"
        lines.append(f"::{command} file={finding.path},line={finding.line},title={title}::{message}")
    return "\n".join(lines)
