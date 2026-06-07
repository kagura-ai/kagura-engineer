# Security Policy

## Supported versions

This project is pre-1.0 and under active development. Only the latest `main`
receives security fixes.

| Version | Supported |
|---------|-----------|
| `main` (latest) | ✅ |
| older tags | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Use GitHub's private vulnerability reporting:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability** (Private Vulnerability Reporting).
3. Describe the issue, affected versions, and reproduction steps.

> Maintainers: enable this under **Settings → Code security → Private
> vulnerability reporting** if it is not already on.

We aim to acknowledge a report within **3 business days** and to provide a
remediation timeline after triage. Please give us a reasonable window to fix
the issue before any public disclosure.

## Scope

`setup` runs install commands and `doctor` probes local services and the
configured `memory_cloud_url`. Reports involving credential handling, command
injection in the setup steps, or unsafe subprocess invocation are especially
welcome.
