# Security Policy

WinSnap touches Windows registry keys, environment variables, and the file
system. While it tries to stay in safe territory (HKCU only, no HKLM writes,
no network calls), bugs can still cause real damage on a user's machine.
Security reports are taken seriously.

---

## Supported versions

The latest tagged release is the only version that receives security fixes.
Older snapshot formats are read-only — no patches will be backported.

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |
| < 0.1   | ❌        |

---

## Reporting a vulnerability

**Do not open a public GitHub issue for security problems.**

Instead, email the maintainer directly at the address listed in the GitHub
profile, or use GitHub's private vulnerability reporting feature on this
repository.

Include:

- A clear description of the issue and potential impact
- Steps to reproduce, or a minimal proof-of-concept
- The version of WinSnap (snapshot format + commit hash if from source)
- Your Windows version (10 / 11, build number)

You'll get an acknowledgement within 7 days. Fixes for confirmed
vulnerabilities aim to ship within 30 days, with public disclosure coordinated
with you after a patch is available.

---

## Threat model and design choices

WinSnap is designed around the assumption that **the snapshot file is trusted**.
If an attacker can swap your `.winsnap` file with a malicious one, they could:

- Set malicious environment variables (e.g. point `PATH` at a hostile binary)
- Install winget packages of their choosing (`winget import` runs unattended)
- Place `.lnk` shortcuts in your Startup folder
- Register fonts (these are largely benign but font parsers have had bugs)

To reduce this risk:

- **HKCU only.** WinSnap never writes to `HKEY_LOCAL_MACHINE`. Even with a
  malicious snapshot, the blast radius is limited to your user account.
- **PATH merge, not replace.** A hostile snapshot can't strip system tools
  off your PATH; it can only append.
- **Startup binary validation.** Run-key entries pointing at non-existent
  binaries are dropped on restore, so we don't pollute the Startup keys.
- **No network calls** other than `winget`'s own connections to Microsoft's
  package source. WinSnap doesn't phone home.

Future hardening on the roadmap:

- Snapshot signing / checksum verification so altered snapshots fail loudly
- Code signing of the `.exe` build (removes SmartScreen warning, also
  proves the binary hasn't been tampered with)

---

## What is **not** in scope

- Reports about the **SmartScreen** warning when running the unsigned `.exe`
  — that's expected behavior for any unsigned Windows binary.
- General complaints about `winget` itself or the packages it installs.
- Privacy reports about Windows-side telemetry — WinSnap does not collect
  or transmit any data beyond `winget`'s own behavior.

For functionality bugs, please use the regular issue templates instead.
