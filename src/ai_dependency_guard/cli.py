"""Command-line interface for AI Dependency Guard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .outputs import render_github_annotations, render_json, render_sarif, render_text
from .registry import PublicRegistryClient
from .scanner import scan_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-dependency-guard",
        description="Detect package dependencies hallucinated by AI coding tools.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="scan a repository path")
    scan.add_argument("path", nargs="?", default=".", help="repository path (default: current directory)")
    formats = scan.add_mutually_exclusive_group()
    formats.add_argument("--json", action="store_true", help="emit JSON")
    formats.add_argument("--sarif", action="store_true", help="emit SARIF 2.1.0")
    scan.add_argument("--sarif-file", help="also write SARIF output to a file")
    scan.add_argument("--github-annotations", action="store_true", help="emit GitHub workflow annotations")
    scan.add_argument("--offline", action="store_true", help="skip registry requests and report unknown status")
    scan.add_argument("--cache-file", help="persist registry results in an explicit cache file")
    scan.add_argument("--cache-ttl", type=float, default=86400.0, help="cache lifetime in seconds")
    scan.add_argument(
        "--fail-on",
        choices=("any", "none"),
        default="any",
        help="exit 1 when findings exist (any) or always exit 0 (none)",
    )
    scan.add_argument(
        "--ignore-package",
        action="append",
        default=[],
        help="package name to ignore; may be repeated",
    )
    scan.add_argument(
        "--ignore-path",
        action="append",
        default=[],
        help="relative path or glob to ignore; may be repeated",
    )
    scan.add_argument("--timeout", type=float, default=5.0, help="registry request timeout in seconds")
    scan.add_argument("--max-retries", type=int, default=2, help="maximum registry retries")
    return parser


def _write_sarif(path: str, payload: str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload + "\n", encoding="utf-8")


def _run_scan(args: argparse.Namespace) -> int:
    try:
        registry = PublicRegistryClient(timeout=args.timeout, max_retries=args.max_retries)
        result = scan_path(
            args.path,
            registry=registry,
            offline=args.offline,
            ignore_packages=set(args.ignore_package),
            ignore_paths=set(args.ignore_path),
            cache_file=args.cache_file,
            cache_ttl=args.cache_ttl,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sarif_payload = render_sarif(result.findings)
    if args.sarif_file:
        try:
            _write_sarif(args.sarif_file, sarif_payload)
        except OSError as exc:
            print(f"error: could not write SARIF file: {exc}", file=sys.stderr)
            return 2

    if args.github_annotations:
        annotations = render_github_annotations(result.findings)
        if annotations:
            print(annotations, file=sys.stderr if args.json or args.sarif else sys.stdout)

    if args.json:
        print(render_json(result.findings))
    elif args.sarif:
        print(sarif_payload)
    else:
        print(render_text(result.findings))

    if args.fail_on == "none":
        return 0
    return 1 if result.findings else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        return _run_scan(args)
    parser.print_help()
    return 0
