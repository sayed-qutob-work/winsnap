"""
scripts/roundtrip_check.py
Real-machine executable round-trip check (Req 7.5, 7.7, 15.5).

Runs a full export -> restore --verify cycle as real subprocesses against
the machine this script is invoked on (no mocking -- this is the
complement to tests/test_roundtrip_mocked.py, which drives the same
export.main()/restore.main() entry points with every OS boundary faked for
CI). It then parses the structured --report-json output and asserts, via
evaluate_report() (a pure function, unit-tested in
tests/test_roundtrip_mocked.py without touching a real machine), that
every category came back "matched" or explicitly "skipped" with a reason
-- never "failed" -- and that the restore exited 0. That is WinSnap's
definition of done for the round trip (Req 7.7, 15.5): a scripted
same-machine export -> restore -> verify that never reports a false
success.

Usage:
    python scripts/roundtrip_check.py
    python scripts/roundtrip_check.py --full
    python scripts/roundtrip_check.py --skip apps power fonts

By default, `apps` and `power` are skipped (winget installs and powercfg
elevation make a "quick" round trip slow/inconsistent across machines);
pass --full to run every module, or --skip to choose your own list.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Skipped by default for a "quick" round trip: apps (winget installs can be
# slow and depend on network/source availability) and power (requires
# Administrator; powercfg import/activate is exercised by
# tests/test_power_flow.py already). --full overrides this.
DEFAULT_SKIP = ["apps", "power"]


def evaluate_report(report: dict) -> tuple[bool, str]:
    """
    Pure verdict function over a restore.py --report-json payload
    (`{"snapshot_format", "restore": {...}, "verify": {...}, "exit_code"}`).

    Passes only when:
      - report["exit_code"] == 0, and
      - every category in both report["restore"] and report["verify"] has
        status "matched" or "skipped" (never "failed"/"partial"), and every
        "skipped" category carries a non-empty reason.

    Never touches the filesystem, the registry, or subprocess -- this is
    what makes it independently unit-testable (see
    tests/test_roundtrip_mocked.py::TestEvaluateReport) without a real
    machine or a real round trip.

    Returns (passed, message): message is a human-readable PASS/FAIL
    summary suitable for printing directly.
    """
    problems: list[str] = []

    exit_code = report.get("exit_code")
    if exit_code != 0:
        problems.append(f"exit_code={exit_code!r} (expected 0)")

    for phase in ("restore", "verify"):
        phase_reports = report.get(phase) or {}
        for category, cat_report in phase_reports.items():
            status = (cat_report or {}).get("status")
            if status not in ("matched", "skipped"):
                problems.append(
                    f"{phase}.{category}: status={status!r} "
                    f"(expected matched or skipped)"
                )
            elif status == "skipped" and not cat_report.get("reason"):
                problems.append(f"{phase}.{category}: skipped with no reason")

    if problems:
        return False, "FAIL: " + "; ".join(problems)
    return True, "PASS: every category matched or skipped-with-reason; exit_code=0"


def _resolve_skip_modules(args) -> list:
    if args.full:
        return []
    if args.skip is not None:
        return args.skip
    return list(DEFAULT_SKIP)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a real export -> restore --verify round trip on "
                    "this machine and assert every category matched or was "
                    "honestly skipped (WinSnap's round-trip definition of "
                    "done, Req 7.5/7.7/15.5)."
    )
    parser.add_argument(
        "--skip", nargs="+", metavar="MODULE", default=None,
        help=f"Modules to skip on both export and restore "
             f"(default: {' '.join(DEFAULT_SKIP)})."
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Run every module -- overrides --skip and the default skip list."
    )
    args = parser.parse_args(argv)

    skip_modules = _resolve_skip_modules(args)

    with tempfile.TemporaryDirectory(prefix="winsnap_roundtrip_") as tmp:
        tmp_path = Path(tmp)

        export_cmd = [
            sys.executable, str(PROJECT_ROOT / "export.py"),
            "--all-apps", "--output", str(tmp_path),
            "--name", "rt_check", "--force",
        ]
        if skip_modules:
            export_cmd += ["--skip", *skip_modules]

        print(f"[roundtrip_check] Running export: {' '.join(export_cmd)}")
        export_result = subprocess.run(export_cmd, cwd=str(PROJECT_ROOT))
        if export_result.returncode != 0:
            print(f"FAIL: export.py exited {export_result.returncode}")
            return 1

        winsnap_path = tmp_path / "rt_check.winsnap"
        report_path = tmp_path / "rt_report.json"

        restore_cmd = [
            sys.executable, str(PROJECT_ROOT / "restore.py"), str(winsnap_path),
            "--verify", "--report-json", str(report_path),
        ]
        if skip_modules:
            restore_cmd += ["--skip", *skip_modules]

        print(f"[roundtrip_check] Running restore: {' '.join(restore_cmd)}")
        restore_result = subprocess.run(restore_cmd, cwd=str(PROJECT_ROOT))

        if not report_path.exists():
            print(
                f"FAIL: restore.py did not write a report to {report_path} "
                f"(restore.py exit code {restore_result.returncode})"
            )
            return 1

        report = json.loads(report_path.read_text(encoding="utf-8"))

    passed, message = evaluate_report(report)
    print(message)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
