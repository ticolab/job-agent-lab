# CVE Mitigation Log

## 2026-11-01 — pyasn1 mitigation and blocked filelock upgrade

PR #8 merged the pyasn1 update from 0.6.4 to 0.6.5 into `main`. This addresses CVE-2026-119194, CVE-2026-119195, CVE-2026-119196, and CVE-2026-119197, covering excessive CPU use in ASN.1 decoding. Existing checkouts and environments must update their lockfile and synchronize dependencies before they receive the mitigation; merging the PR does not update an installed environment automatically.

PR #6 proposes filelock 3.29.4 → 4.2.1, but the upgrade is blocked and is not a mitigation yet. Clean-wheel installation fails on both Linux and macOS because LiteLLM 1.106.2 requires `filelock>=3.16.1,<4.0`, which conflicts with 4.2.1. Unit, snapshot, and static checks passed, but do not establish installability.

The filelock advisories remain unresolved: CVE-2026-112907, CVE-2026-112909, CVE-2026-112910, CVE-2026-112913, CVE-2026-112915, CVE-2026-112935, CVE-2026-112936, CVE-2026-112939, and CVE-2026-112940. The audit recommends 4.2.1 to address the full set. No compatible patched 3.x release or newer compatible LiteLLM release has been verified here.

Keep PR #6 unmerged until a supported dependency graph is available. Prefer a LiteLLM release that officially accepts the patched filelock version, or verify an upstream security backport within the supported 3.x range. Regenerate the lockfile, rerun the vulnerability audit, lint, type checks, full tests, and clean-wheel CI on Linux and macOS. Do not override the dependency constraint or weaken CI to force installation. The browser-use-pinned findings documented below remain separate unresolved work.

## 2026-10-23 — LiteLLM 1.106.2

The project previously resolved LiteLLM 1.83.0. It now requires and locks 1.106.2, the current compatible PyPI release audited on 2026-10-23.

CVE-2026-37004 is a critical server-side template injection in the proxy `/prompts/test` endpoint affecting versions before 1.83.7. A remote unauthenticated caller could execute operating-system commands through unsandboxed template input. LiteLLM 1.106.2 contains the fix.

CVE-2026-59823 is an authenticated server-side request forgery through nested `user_config.api_base` input affecting versions through 1.83.8. A proxy caller could reach internal or external hosts. LiteLLM 1.106.2 contains the fix.

CVE-2026-84377 is an authenticated server-side request forgery and provider-credential exfiltration issue in request routing parameters. Multiple release lines through 1.96.1 were affected; 1.96.2 is the first patched 1.96 release. LiteLLM 1.106.2 is outside every published affected range.

The application uses LiteLLM as an in-process SDK rather than exposing its proxy endpoints, which reduced exposure to these proxy vulnerabilities but did not justify retaining a vulnerable dependency. The lockfile upgrade is the mitigation.

An audit of the exact exported `uv.lock` graph found no LiteLLM advisory at 1.106.2. Compatible lock updates moved certifi to 2026.10.23, cryptography to 50.0.2, multidict to 6.9.1, oauthlib to 4.0.0, pyasn1 to 0.6.4, PyJWT to 2.15.1, soupsieve to 3.0.4, and virtualenv to 21.14.13.

The remaining findings are pinned exactly by browser-use 0.13.9: aiohttp 3.14.3, anyio 4.12.1, mcp 1.28.1, Pillow 12.3.0, and pypdf 6.15.0. Their patched releases are incompatible with browser-use's exact dependency declarations, so overriding them would create an unsupported runtime graph. Representative identifiers include CVE-2026-111064 through CVE-2026-111066 and CVE-2026-111748 through CVE-2026-111750 for aiohttp, CVE-2026-59951 for mcp, CVE-2026-96636 through CVE-2026-96644 for Pillow, and CVE-2026-113786 plus CVE-2026-113789 through CVE-2026-113795 for pypdf. Upgrade browser-use when it publishes a release that accepts the patched transitive versions, regenerate the lock, rerun the complete browser regression suite, and update this log.
