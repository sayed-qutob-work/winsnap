"""
power.py
Captures and restores the active Windows power plan.

Export: uses `powercfg /export` to dump the active plan to a .pow file,
        and saves the plan's GUID and name. Requires Administrator rights
        (powercfg /export of the active scheme needs elevation), so export
        skips outright when not elevated.

Restore (Req 6): `powercfg /import` is the one WinSnap operation that is not
  HKCU-only and always requires Administrator rights, so restore() checks
  elevation first and refuses to touch powercfg at all when not admin.

  The import flow handles three distinct outcomes instead of assuming a
  clean import:
    1. `powercfg /import <file> <original_guid>` succeeds -> activate the
       original GUID.
    2. It fails, but a plan with the original GUID is already present on the
       target (`powercfg /list`) -> not a fatal error, just activate the
       existing plan (Req 6.3).
    3. It fails and the GUID isn't present -> retry `powercfg /import <file>`
       *without* a destination GUID, letting Windows assign one; the GUID is
       parsed only from that retry's *successful* stdout. If the retry also
       fails, the whole category is reported failed with both commands'
       stdout/stderr captured (Req 6.4) -- there used to be a dead branch
       here that parsed a "new GUID" out of the *failed* import's output;
       that branch has been removed, since a failed `powercfg /import` prints
       an error message, not a GUID, and activating whatever it happened to
       match was never correct.

  Whichever GUID is settled on, `powercfg /setactive` finishes the job; a
  nonzero exit there is recorded as a failed item too.

Verify (Req 6.5): read-only. Non-elevated -> skipped (restore itself would
  have been skipped for the same reason, so there is nothing meaningful to
  compare). Elevated -> `powercfg /getactivescheme` is compared against the
  snapshot's intended plan GUID/name.
"""

import re
import subprocess
from pathlib import Path

from modules.report import Report
from modules import winutil

# powercfg exit codes/messages don't carry a GUID on failure -- see the
# module docstring for why the old "parse GUID from failed import" branch
# was removed rather than kept as a fallback.
_GUID_LINE_RE = re.compile(r"GUID:\s+([\w\-]+)\s+\((.+?)\)")
_GUID_ONLY_RE = re.compile(r"GUID:\s+([\w\-]+)")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(snapshot_dir: Path) -> dict:
    if not winutil.is_admin():
        print("[power] Skipped — requires Administrator rights.")
        print("[power] Tip: re-run export.py as Administrator to capture your power plan.")
        return {"enabled": False, "skip_reason": "not_admin"}

    active_guid, active_name = _get_active_plan()

    if not active_guid:
        print("[power] Could not determine active power plan. Skipping.")
        return {"enabled": False}

    pow_file = snapshot_dir / "power_plan.pow"
    result = subprocess.run(
        ["powercfg", "/export", str(pow_file), active_guid],
        capture_output=True, text=True
    )

    if result.returncode != 0 or not pow_file.exists():
        print(f"[power] Export failed: {result.stderr.strip()}")
        return {"enabled": False}

    print(f"[power] Captured power plan: {active_name} ({active_guid})")
    return {
        "enabled": True,
        "guid": active_guid,
        "name": active_name,
        "filename": "power_plan.pow",
    }


def _get_active_plan() -> tuple[str | None, str | None]:
    """Returns (guid, name) of the currently active power plan."""
    try:
        result = subprocess.run(
            ["powercfg", "/getactivescheme"],
            capture_output=True, text=True
        )
        # Output: "Power Scheme GUID: xxxxxxxx-xxxx-...  (Balanced)"
        match = _GUID_LINE_RE.search(result.stdout)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    except FileNotFoundError:
        pass
    return None, None


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    report = Report("power", "restore")

    # Req 6.1: elevation is checked first, before any powercfg call at all.
    if not winutil.is_admin():
        return report.skip_all("requires elevation — run restore.py as Administrator")

    if not snapshot.get("enabled"):
        reason = "no power plan in snapshot"
        if snapshot.get("skip_reason") == "not_admin":
            reason += " (export was not run as Administrator)"
        print(f"[power] Skipped — {reason}.")
        return report.skip_all(reason)

    pow_file = snapshot_dir / snapshot.get("filename", "power_plan.pow")
    if not pow_file.exists():
        report.add_failed("import", detail=f"power plan file missing: {pow_file}")
        return report.finalize()

    original_guid = snapshot.get("guid")
    if not original_guid:
        report.add_failed("import", detail="snapshot missing power plan GUID")
        return report.finalize()

    # Step 1: import under the original destination GUID.
    import_result = subprocess.run(
        ["powercfg", "/import", str(pow_file), original_guid],
        capture_output=True, text=True
    )

    target_guid: str | None = None

    if import_result.returncode == 0:
        target_guid = original_guid
        report.add_matched("import", detail=f"imported plan {original_guid}")
    else:
        # Req 6.3: the import can fail simply because a plan with this GUID
        # already exists on the target -- that is not a fatal error.
        list_result = subprocess.run(
            ["powercfg", "/list"], capture_output=True, text=True
        )
        if original_guid.lower() in list_result.stdout.lower():
            target_guid = original_guid
            report.add_matched(
                "import", detail="plan already present, activating existing")
        else:
            # Retry without a destination GUID; Windows assigns one, and the
            # new GUID is parsed ONLY from a successful retry's stdout (the
            # deleted branch used to parse a "GUID" out of a failed import's
            # output, which is not something powercfg ever prints).
            retry_result = subprocess.run(
                ["powercfg", "/import", str(pow_file)],
                capture_output=True, text=True
            )
            if retry_result.returncode == 0:
                match = _GUID_ONLY_RE.search(retry_result.stdout)
                if match:
                    target_guid = match.group(1).strip()
                    report.add_matched(
                        "import",
                        detail=f"imported under new GUID {target_guid}")
                else:
                    report.add_failed(
                        "import",
                        detail="import succeeded but no GUID could be "
                               f"parsed from output: {retry_result.stdout.strip()!r}")
            else:
                report.add_failed(
                    "import",
                    detail=(
                        "all import attempts failed — "
                        f"import with GUID: rc={import_result.returncode} "
                        f"stdout={import_result.stdout.strip()!r} "
                        f"stderr={import_result.stderr.strip()!r}; "
                        f"import without GUID: rc={retry_result.returncode} "
                        f"stdout={retry_result.stdout.strip()!r} "
                        f"stderr={retry_result.stderr.strip()!r}"
                    ))

    if target_guid is None:
        # All import attempts failed — do NOT call /setactive on anything.
        return report.finalize()

    setactive_result = subprocess.run(
        ["powercfg", "/setactive", target_guid],
        capture_output=True, text=True
    )
    if setactive_result.returncode == 0:
        report.add_matched("activate", detail=f"activated plan {target_guid}")
    else:
        report.add_failed(
            "activate",
            detail=(
                f"powercfg /setactive failed: rc={setactive_result.returncode} "
                f"stdout={setactive_result.stdout.strip()!r} "
                f"stderr={setactive_result.stderr.strip()!r}"
            ))

    return report.finalize()


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def verify(data: dict, snapshot_dir: Path) -> dict:
    """Read-only: compares the live active power scheme against the
    snapshot's intended plan. Non-elevated -> skipped, since restore itself
    would have been skipped for the same reason and there is nothing to
    compare (Req 6.5)."""
    report = Report("power", "verify")

    if not winutil.is_admin():
        return report.skip_all("requires elevation — run restore.py as Administrator")

    if not data.get("enabled"):
        return report.finalize()  # nothing was captured/restored -> skipped

    expected_guid = data.get("guid")
    expected_name = data.get("name")

    result = subprocess.run(
        ["powercfg", "/getactivescheme"], capture_output=True, text=True
    )
    if result.returncode != 0:
        report.add_failed(
            "active_scheme",
            detail=f"powercfg /getactivescheme failed: {result.stderr.strip()!r}")
        return report.finalize()

    match = _GUID_LINE_RE.search(result.stdout)
    if not match:
        report.add_failed(
            "active_scheme",
            detail=f"could not parse active scheme from output: {result.stdout.strip()!r}")
        return report.finalize()

    active_guid, active_name = match.group(1).strip(), match.group(2).strip()

    guid_matches = bool(expected_guid) and active_guid.lower() == expected_guid.lower()
    name_matches = bool(expected_name) and active_name.lower() == expected_name.lower()

    expected_label = expected_guid or expected_name
    if guid_matches or name_matches:
        report.add_matched(
            "active_scheme",
            detail=f"active plan is {active_name} ({active_guid})",
            expected=expected_label, actual=active_guid)
    else:
        report.add_failed(
            "active_scheme",
            detail=f"expected {expected_label}, active is {active_name} ({active_guid})",
            expected=expected_label, actual=active_guid)

    return report.finalize()
