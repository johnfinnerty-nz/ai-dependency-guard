# ai-dependency-guard

Detect package dependencies hallucinated by AI coding tools before they reach your machine or CI.

AI assistants can suggest package names that sound plausible but do not exist. A package may later be registered under that name by an attacker. `ai-dependency-guard` checks references against public npm and PyPI registries; it does not install or execute packages.

> A package existing in a registry does not mean that it is safe.

## Quick start

Run from a checkout without installing runtime dependencies:

```bash
PYTHONPATH=src python -m ai_dependency_guard scan fixtures/broken-repo --offline
```

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m ai_dependency_guard scan fixtures/broken-repo --offline
```

Example output:

```text
Found 2 dependency finding(s):
[WARNING] README.md:4 npm:definitely-not-a-real-package-12345
[WARNING] README.md:5 pypi:definitely-not-a-real-python-package-12345
```

For an installed CLI after publishing:

```bash
pipx install ai-dependency-guard
ai-dependency-guard scan .
```

## 15-second demo

From this checkout, run the scanner against the included broken fixture:

```bash
PYTHONPATH=src python -m ai_dependency_guard scan fixtures/broken-repo --offline
```

It immediately reports both fake package names with their exact file and line.

## GitHub Action

Add this workflow to a repository:

```yaml
name: AI dependency guard

on: [push, pull_request]

permissions:
  contents: read

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./
        with:
          path: .
          sarif-file: ai-dependency-guard.sarif
```

The local form above is copy-pasteable when `action.yml` is in the repository being scanned. After publishing, replace `./` with `OWNER/ai-dependency-guard@v1`. The Action adds annotations to affected files and fails when findings exist. It needs no API key. The optional `sarif-file` input writes a SARIF 2.1.0 report; upload it with GitHub's Code Scanning workflow if desired. The `cache-file` input can point to a workspace or runner cache; it is never written into the checkout unless explicitly configured.

### Action configuration

| Input | Default | Purpose |
| --- | --- | --- |
| `path` | `.` | Repository directory to scan. |
| `offline` | `false` | Skip registry requests and report unknown status. |
| `fail-on` | `any` | Use `none` to report findings without failing the step. |
| `ignore-paths` | empty | Comma-separated relative paths or globs. |
| `ignore-packages` | empty | Comma-separated package names. |
| `cache-file` | runner temp file | Explicit registry cache path. |
| `sarif-file` | empty | Optional SARIF output path. |

## CLI

```text
ai-dependency-guard scan [path]
ai-dependency-guard scan [path] --json
ai-dependency-guard scan [path] --sarif
ai-dependency-guard scan [path] --sarif-file results.sarif
ai-dependency-guard scan [path] --offline
ai-dependency-guard scan [path] --ignore-package PACKAGE
ai-dependency-guard scan [path] --ignore-path 'fixtures/**'
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | No findings, or `--fail-on none` |
| `1` | Findings detected |
| `2` | Invalid input or internal error |

The scanner skips `.git`, `node_modules`, virtual environments, build output, and other generated directories. It does not run package managers, shell scripts, project code, or registry install hooks.

## What it checks

- npm dependency sections in `package.json`.
- Python dependencies in `requirements.txt` and `pyproject.toml`.
- Install commands in Markdown, agent instruction files, shell scripts, PowerShell, YAML, and JSON configuration.
- Public npm and PyPI package existence.
- Optional JSON and SARIF reports for CI tooling.

Registry lookup failures are reported as unknown; they are never treated as a clean result. Offline mode deliberately reports unknown status for each package reference.

## Threat model

The tool is designed to catch a package name that an AI assistant invented or mistyped before a developer installs it. It treats a missing registry entry as a high-signal finding and keeps registry failures visible as unknown findings.

It does not determine whether an existing package is malicious, compromised, typosquatted, or vulnerable. It is not a malware scanner and does not replace lockfile review, dependency auditing, or package provenance checks.

## Privacy

- No LLM, paid service, credential, or API key is required.
- Only public npm and PyPI package endpoints are contacted during online scans.
- Package contents are not downloaded or executed.
- Secret values are not included in findings or cache entries.
- The default scan is read-only. An explicit `--cache-file` may be used to persist registry status outside the scanned repository.

## Limitations

- v1 covers npm and PyPI only.
- A package referenced through an unusual command wrapper or generated dynamically may not be detected.
- Registry availability, rate limits, and package renames can affect results; unknown is never treated as clean.
- A package existing in a registry does not mean that it is safe.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m ai_dependency_guard --help
```

The test suite uses mocked registry responses and does not require network access.

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
