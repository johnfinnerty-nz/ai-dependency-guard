# Contributing

Thanks for helping improve `ai-dependency-guard`.

## Development setup

The project targets Python 3.11 or newer and has no runtime dependencies:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Pull requests

- Keep changes focused on dependency hallucination detection.
- Add a test before changing behavior.
- Do not add live-network dependencies to tests.
- Do not execute scanned project code in the scanner.
- Document false-positive or registry-behavior changes.

## Adding an ecosystem

Add an extractor and registry adapter behind the existing interfaces. Include fixtures for valid, missing, malformed, offline, and rate-limited inputs.
