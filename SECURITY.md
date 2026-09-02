# Security policy

## Scope

Report vulnerabilities in `ai-dependency-guard`, its registry lookup behavior, or its GitHub Action.

## Reporting

Please use a private GitHub security advisory for sensitive reports when the repository is hosted. Do not include live credentials, private source code, or exploit payloads in public issues.

## Security guarantees and limits

The scanner is read-only with respect to the target repository. It does not install packages or execute project code. Registry existence is not a safety verdict; an existing package may still be malicious, compromised, or vulnerable.
