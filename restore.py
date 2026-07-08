"""
restore.py  —  WinSnap restorer
Run this on your TARGET (new) machine.

Usage:
    python restore.py my_snapshot.winsnap
    python restore.py my_snapshot.winsnap --skip apps
    python restore.py my_snapshot.winsnap --only wallpaper taskbar
    python restore.py my_snapshot.winsnap --dry-run
    python restore.py my_snapshot.winsnap --verify
    python restore.py my_snapshot.winsnap --verify --report-json report.json

What it does:
1. Safely extracts the .winsnap archive to a temp folder (refusing any
   member whose path would escape the extraction directory)
2. Locates and reads snapshot.json
3. Runs each module's restore() in the order defined by modules/manifest.py,
   collecting a structured per-category report instead of trusting "no
   exception" as success
4. Restarts Explorer exactly once, if any module's changes require it
5. Optionally re-reads live machine state and compares it against the
   snapshot per category (--verify)
6. Prints a per-category summary and exits non-zero if any category failed
7. Cleans up the temp folder
"""

import argparse
import importlib
import json
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Force UTF-8 stdout/stderr so unicode in our messages doesn't crash on
# Windows consoles that default to cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent))

from modules import manifest, report, winutil, taskbar


# Maximum snapshot format MAJOR version this restorer understands.
# We accept anything in the same MAJOR series; refuse a newer MAJOR.
SUPPORTED_MAJOR = 0


# ---------------------------------------------------------------------------
# Archive hygiene (Req 13.1, 13.2)
# ---------------------------------------------------------------------------

class ZipSlipError(Exception):
    """
    Raised when a .winsnap archive contains one or more members whose
    resolved extraction path would escape the destination directory
    (zip-slip). Carries the offending member names so the caller can report
    exactly which entries were rejected.
    """

    def __init__(self, members: list):
        self.members = list(members)
        super().__init__(
            f"archive contains {len(self.members)} unsafe member path(s): "
            f"{', '.join(self.members)}"
        )


class SnapshotLayoutError(Exception):
    """
    Raised when no directory in an extracted .winsnap archive (neither the
    archive root nor any of its immediate subdirectories) contains a
    snapshot.json.
    """


def safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """
    Extract every member of `zf` into `dest`, refusing the entire archive if
    any member's resolved path would land outside `dest` (zip-slip
    protection, Req 13.1).

    Policy: fail the whole restore rather than skip-and-continue -- an
    archive containing a traversal member is treated as hostile and nothing
    in it is trusted. Well-formed archives take the identical `extractall`
    path, so there is no behavior change for legitimate snapshots (Req
    13.4).
    """
    dest_resolved = dest.resolve()
    bad_members = []
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        try:
            escapes = not target.is_relative_to(dest_resolved)
        except ValueError:
            # Different drives on Windows raise ValueError from
            # is_relative_to rather than returning False -- treat as escape.
            escapes = True
        if escapes:
            bad_members.append(member.filename)

    if bad_members:
        raise ZipSlipError(bad_members)

    zf.extractall(dest)


def find_snapshot_dir(tmp_dir: Path) -> Path:
    """
    Locate the directory containing snapshot.json inside an extracted
    archive (Req 13.2): `tmp_dir` itself first (flat archives), then each
    immediate subdirectory, in name order. Raises SnapshotLayoutError if no
    candidate qualifies, instead of blindly picking the first extracted
    directory.
    """
    if (tmp_dir / "snapshot.json").exists():
        return tmp_dir

    for child in sorted(tmp_dir.iterdir()):
        if child.is_dir() and (child / "snapshot.json").exists():
            return child

    raise SnapshotLayoutError(
        "no snapshot.json found in archive (checked the archive root and "
        "every immediate subdirectory)"
    )


# Ordered list of (key, module), derived from the single source of truth in
# modules/manifest.py (Req 2.1, 2.5) instead of a second hand-maintained
# list. The public name `ALL_MODULES` and the (key, module) tuple shape are
# preserved unchanged -- gui.py:1470 consumes this as a {key: mod} lookup.
ALL_MODULES = [
    (name, importlib.import_module(f"modules.{name}"))
    for name in manifest.MODULE_NAMES
]


@dataclass(frozen=True)
class VersionEvaluation:
    """Pure result of evaluating a snapshot's format version (Req 7.1):
    verdict is one of "compatible" | "incompatible" | "unparseable"; raw is
    the version value after the fallback chain, kept in its ORIGINAL type
    (not stringified) so callers that need repr-parity with the pre-refactor
    diagnostic message can get it; major is the parsed MAJOR component, or
    None when unparseable."""
    verdict: str
    raw: object
    major: int | None


def evaluate_snapshot_version(snapshot: dict) -> VersionEvaluation:
    """Pure, print-free version-acceptance decision -- single source of
    truth for restore.py's own _check_format_version (Task 1.2) and for the
    GUI (Req 7.1, 7.2, 7.3). Fallback chain and MAJOR-parsing logic are
    identical to the pre-refactor _check_format_version: try
    snapshot_format_version, then winsnap_version, then "0.1.0"; a MAJOR
    greater than SUPPORTED_MAJOR is incompatible; anything that cannot be
    parsed as int(str(raw).split(".")[0]) is unparseable."""
    raw = (snapshot.get("snapshot_format_version")
           or snapshot.get("winsnap_version")
           or "0.1.0")
    try:
        major = int(str(raw).split(".")[0])
    except (ValueError, IndexError):
        return VersionEvaluation("unparseable", raw, None)

    if major > SUPPORTED_MAJOR:
        return VersionEvaluation("incompatible", raw, major)
    return VersionEvaluation("compatible", raw, major)


def _check_format_version(snapshot: dict) -> bool:
    """Return True if we can safely restore this snapshot.

    Thin wrapper over evaluate_snapshot_version (Req 7.3, 11.1): the
    fallback chain and MAJOR-parsing logic now live in one place
    (evaluate_snapshot_version), and this function only reproduces the two
    diagnostic print lines and boolean return the CLI has always printed --
    byte-identical to the pre-refactor inline implementation, including the
    {raw!r} warning line for non-string raw values (see
    VersionEvaluation.raw's docstring for why raw is kept unstringified)."""
    ev = evaluate_snapshot_version(snapshot)
    if ev.verdict == "unparseable":
        print(f"  WARNING: unrecognized version format {ev.raw!r}, "
              f"attempting restore anyway.")
        return True
    if ev.verdict == "incompatible":
        print(f"  ERROR: snapshot format v{ev.raw} is newer than this restorer "
              f"supports (v{SUPPORTED_MAJOR}.x). Update WinSnap and try again.")
        return False
    return True


def partition_modules(modules_to_run: list, modules_data: dict) -> tuple:
    """
    Pure classification of `modules_to_run` into (attemptable, skipped),
    where `skipped` maps key -> reason code ("not_found_in_snapshot" |
    "export_error") (Req 2.2, 2.6).

    This mirrors the two-line membership check inline in run_modules's loop
    (`key not in modules_data` / `"error" in data`) exactly, but is
    deliberately NOT a shared call -- run_modules is already hardened and is
    left untouched (see restore.py's module docstring / design notes). The
    GUI (and run_dry_run) get their skip-row data from this function
    instead of re-deriving the membership check themselves. A property-based
    parity test guards this copy against ever drifting from run_modules's
    inline check.
    """
    attemptable: list = []
    skipped: dict = {}
    for key, mod in modules_to_run:
        if key not in modules_data:
            skipped[key] = "not_found_in_snapshot"
            continue
        data = modules_data[key]
        if isinstance(data, dict) and "error" in data:
            skipped[key] = "export_error"
            continue
        attemptable.append((key, mod))
    return attemptable, skipped


def _summarize(key: str, data) -> str:
    """One-line summary of what restoring `key` would do, for --dry-run."""
    if data is None:
        return "no data"
    if isinstance(data, dict):
        if "error" in data:
            return f"export had error: {data['error']}"
        if key == "apps":
            wg = len(data.get("winget", []) or [])
            mn = len(data.get("manual", []) or [])
            return f"would install {wg} winget app(s), report {mn} manual app(s)"
        if key == "fonts":
            return f"would install {len(data.get('fonts') or [])} font file(s)"
        if key == "startup":
            reg = data.get("registry", {}) or {}
            reg_count = sum(len(v) for v in reg.values())
            sc = len(data.get("shortcuts") or [])
            return f"would restore {reg_count} registry entry(ies), {sc} shortcut(s)"
        if key == "env_vars":
            # 0.3.0 wraps the vars map as {"source_profile", "vars"}; 0.2.0
            # snapshots are the bare vars map itself (Req 14.2).
            variables = data.get("vars", data)
            return f"would restore {len(variables)} environment variable(s)"
        if key == "region_lang":
            intl    = len(data.get("international") or {})
            layouts = len(data.get("keyboard_layouts") or {})
            return f"would restore {intl} format(s), {layouts} keyboard layout(s)"
        if key == "desktop_icons":
            visible = sum(1 for v in data.values() if v == 0)
            return f"would set {visible} icon(s) visible, {len(data)-visible} hidden"
        if key == "explorer":
            return f"would restore {len(data)} Explorer preference(s)"
        if key == "sound_scheme":
            evs = len(data.get("event_sounds") or {})
            return f"would set scheme {data.get('scheme')!r} with {evs} event sound(s)"
        if key == "cursors":
            return f"would set cursor scheme {data.get('scheme')!r}"
        return f"would restore {len(data)} field(s)"
    if isinstance(data, list):
        return f"would restore {len(data)} item(s)"
    return f"would restore: {data!r}"


# Wording matches, verbatim, the skip messages main()'s pre-refactor
# --dry-run loop printed inline for each partition_modules reason code (Req
# 8.8, 11.1) -- see tests/test_run_dry_run_golden.py for the captured
# baseline this must keep matching.
_DRY_RUN_SKIP_MESSAGES = {
    "not_found_in_snapshot": "Not found in snapshot. Skipping.",
    "export_error": "Was not captured (export error). Skipping.",
}


def run_dry_run(modules_to_run: list, modules_data: dict) -> dict:
    """
    Extracted verbatim from main()'s --dry-run loop (Req 2.6, 8.8): prints
    the identical lines, in the identical order, reusing partition_modules
    for the skip classification and the existing _summarize for the
    per-module summary text. Additionally returns
    {key: {"would_restore": bool, "summary": str | None,
    "skip_reason": str | None}} so a structured caller (the GUI) does not
    have to scrape stdout to know what would have happened (Req 2.6).
    """
    _, skipped = partition_modules(modules_to_run, modules_data)
    result: dict = {}
    for key, mod in modules_to_run:
        if key in skipped:
            print(f"[{key}] {_DRY_RUN_SKIP_MESSAGES[skipped[key]]}")
            result[key] = {
                "would_restore": False,
                "summary": None,
                "skip_reason": skipped[key],
            }
            continue
        summary = _summarize(key, modules_data[key])
        print(f"[{key}] {summary}")
        result[key] = {
            "would_restore": True,
            "summary": summary,
            "skip_reason": None,
        }
    return result


# ---------------------------------------------------------------------------
# Orchestration (Req 1.3, 2.1, 2.2, 7.2, 7.3, 7.4, 7.5, 7.6)
# ---------------------------------------------------------------------------

def run_modules(modules_to_run: list, modules_data: dict, snapshot_dir: Path,
                 *, dry_run: bool) -> dict:
    """
    Runs restore() for each (key, mod) in `modules_to_run` whose data is
    present in `modules_data`, returning {name: restore_report}.

    While the loop runs, `taskbar.INLINE_EXPLORER_RESTART` is forced False
    (restored in a finally) so no module restarts Explorer inline; instead
    every module whose changes need a reload sets
    `explorer_restart_required` on its report, and this function performs a
    single `winutil.restart_explorer()` after all modules have run and
    before verification (D2).

    A module raising an exception does not abort the remaining modules --
    the exception is caught and synthesized into a failed report. A module
    that returns None (a contract violation) is recorded as skipped, never
    as a silent success (Req 7.4).
    """
    if dry_run:
        return {}

    reports: dict = {}
    previous_flag = taskbar.INLINE_EXPLORER_RESTART
    taskbar.INLINE_EXPLORER_RESTART = False
    try:
        for key, mod in modules_to_run:
            if key not in modules_data:
                print(f"[{key}] Not found in snapshot. Skipping.")
                continue

            data = modules_data[key]
            if isinstance(data, dict) and "error" in data:
                print(f"[{key}] Was not captured (export error). Skipping.")
                continue

            print(f"\n[{key}] Restoring...")
            try:
                result = mod.restore(data, snapshot_dir)
            except Exception as e:
                print(f"[{key}] ERROR during restore: {e}")
                result = {"status": "failed", "items": [], "reason": str(e)}

            if result is None:
                result = {
                    "status": "skipped",
                    "items": [],
                    "reason": "module returned no report",
                }

            reports[key] = result
    finally:
        taskbar.INLINE_EXPLORER_RESTART = previous_flag

    if any(r.get("explorer_restart_required") for r in reports.values()):
        winutil.restart_explorer()

    return reports


def run_verify(modules_to_run: list, modules_data: dict, snapshot_dir: Path) -> dict:
    """
    Runs verify() for each (key, mod) in `modules_to_run` whose data is
    present in `modules_data`, returning {name: verify_report}. Modules
    that do not implement verify() are reported skipped -- never defaulted
    to matched (Req 7.6). Only called when --verify is passed; --dry-run
    bypasses both restore and verify.
    """
    reports: dict = {}
    for key, mod in modules_to_run:
        if key not in modules_data:
            continue

        data = modules_data[key]
        if isinstance(data, dict) and "error" in data:
            continue

        verify_fn = getattr(mod, "verify", None)
        if verify_fn is None:
            reports[key] = {
                "status": "skipped",
                "reason": "verification not implemented",
                "items": [],
            }
            continue

        print(f"[{key}] Verifying...")
        try:
            reports[key] = verify_fn(data, snapshot_dir)
        except Exception as e:
            print(f"[{key}] ERROR during verify: {e}")
            reports[key] = {"status": "failed", "items": [], "reason": str(e)}

    return reports


def print_summary(restore_reports: dict, verify_reports: dict) -> None:
    """
    Print a per-category summary table (restore status, verify status,
    item counts) followed by per-item detail for every category whose
    restore or verify status is partial or failed. Replaces the old
    unconditional "Restore completed successfully!" banner (Req 7.3, 7.4).
    """
    print(f"\n{'='*55}")
    print("  Restore summary")
    print(f"{'='*55}")

    ordered_keys = [name for name, _ in ALL_MODULES
                    if name in restore_reports or name in verify_reports]

    if not ordered_keys:
        print("  No modules were run.")

    for key in ordered_keys:
        r = restore_reports.get(key)
        v = verify_reports.get(key)
        r_status = r["status"] if r else "not run"
        r_items = len(r.get("items", [])) if r else 0
        line = f"  [{key}] restore={r_status} ({r_items} item(s))"
        if verify_reports:
            v_status = v["status"] if v else "not run"
            v_items = len(v.get("items", [])) if v else 0
            line += f"  verify={v_status} ({v_items} item(s))"
        print(line)

        for phase_name, phase_report in (("restore", r), ("verify", v)):
            if phase_report and phase_report.get("status") in ("partial", "failed"):
                for item in phase_report.get("items", []):
                    detail = f" -- {item['detail']}" if item.get("detail") else ""
                    print(f"      [{phase_name}] {item['name']}: "
                          f"{item['status']}{detail}")

    print(f"{'='*55}")


def write_report_json(path: Path, snapshot_format: str, restore_reports: dict,
                       verify_reports: dict, exit_code: int) -> None:
    """
    Writes the combined restore+verify report as JSON to `path`, so a
    scripted round trip can assert against structured data instead of
    scraping the console banner (Req 7.7, D10).
    """
    payload = {
        "snapshot_format": snapshot_format,
        "restore": restore_reports,
        "verify": verify_reports,
        "exit_code": exit_code,
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(
        description="WinSnap — restore your Windows settings from a snapshot."
    )
    parser.add_argument(
        "snapshot",
        type=Path,
        help="Path to the .winsnap file"
    )
    parser.add_argument(
        "--skip", nargs="+", metavar="MODULE", default=[],
        help="Modules to skip (e.g. --skip apps power)"
    )
    parser.add_argument(
        "--only", nargs="+", metavar="MODULE", default=[],
        help="Run only these modules (e.g. --only wallpaper)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be restored without making any changes."
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="After restoring, re-read live machine state and compare it "
             "against the snapshot for each restored category."
    )
    parser.add_argument(
        "--report-json", type=Path, metavar="FILE", default=None,
        help="Write the combined restore+verify report as JSON to FILE."
    )
    args = parser.parse_args()

    if not args.snapshot.exists():
        print(f"Error: file not found: {args.snapshot}")
        sys.exit(1)

    print("=" * 55)
    print("  WinSnap — Windows Settings Restorer")
    if args.dry_run:
        print("  DRY-RUN: no changes will be made")
    print("=" * 55)

    # --- Determine which modules to run ---
    skip = set(args.skip)
    only = set(args.only)
    modules_to_run = [
        (key, mod) for key, mod in ALL_MODULES
        if (not only or key in only) and key not in skip
    ]

    # --- Extract snapshot (zip-slip safe) ---
    tmp_dir = Path(tempfile.mkdtemp(prefix="winsnap_restore_"))
    print(f"\nExtracting snapshot to: {tmp_dir}")
    try:
        with zipfile.ZipFile(args.snapshot, "r") as zf:
            safe_extract(zf, tmp_dir)
    except ZipSlipError as e:
        print("Error: archive refused -- unsafe path(s) detected "
              "(zip-slip protection). Rejected member(s):")
        for member in e.members:
            print(f"    {member}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        sys.exit(1)
    except zipfile.BadZipFile as e:
        print(f"Error: not a valid .winsnap archive: {e}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        sys.exit(1)

    # --- Locate the snapshot content directory ---
    try:
        snapshot_dir = find_snapshot_dir(tmp_dir)
    except SnapshotLayoutError as e:
        print(f"Error: {e}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        sys.exit(1)

    # --- Load snapshot.json ---
    snapshot = json.loads((snapshot_dir / "snapshot.json").read_text(encoding="utf-8"))
    print(f"\nSnapshot from: {snapshot.get('exported_at', 'unknown date')}")
    print(f"WinSnap version: {snapshot.get('winsnap_version', '?')}")
    fmt_ver = snapshot.get("snapshot_format_version", "?")
    print(f"Snapshot format: {fmt_ver}\n")

    if not _check_format_version(snapshot):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        sys.exit(2)

    modules_data = snapshot.get("modules", {})

    # --- Dry-run: summarize only, bypassing both restore and verify ---
    if args.dry_run:
        run_dry_run(modules_to_run, modules_data)

        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"\n{'='*55}")
        print("  Dry-run complete. Nothing was changed.")
        print(f"{'='*55}")
        sys.exit(0)

    # --- Run restore modules ---
    restore_reports = run_modules(modules_to_run, modules_data, snapshot_dir,
                                   dry_run=False)

    # --- Optionally verify, after the single Explorer restart ---
    verify_reports = {}
    if args.verify:
        verify_reports = run_verify(modules_to_run, modules_data, snapshot_dir)

    # Exit code is 0 only if no category failed in either phase (Req 7.5).
    exit_code = max(
        report.worst_exit_code(restore_reports),
        report.worst_exit_code(verify_reports),
    )

    print_summary(restore_reports, verify_reports)

    if args.report_json:
        write_report_json(args.report_json, fmt_ver, restore_reports,
                           verify_reports, exit_code)

    # --- Cleanup ---
    shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\nNote: Some changes (theme, env vars) may require a "
          "logout/restart to fully apply.")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
