"""
export.py  —  WinSnap exporter
Run this on your SOURCE machine.

Usage:
    python export.py
    python export.py --output C:\\Users\\You\\Desktop\\my_snapshot
    python export.py --skip fonts startup
    python export.py --only wallpaper taskbar

What it does:
1. Creates a snapshot folder
2. Runs each settings module
3. Writes snapshot.json with all captured metadata
4. Zips everything into <name>.winsnap
"""

import argparse
import ctypes
import importlib
import os
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdout/stderr so unicode in our messages (✓, →, etc.) doesn't
# crash on Windows consoles that default to cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# Make sure we can import our modules whether running from the project root
# or from inside the winsnap/ folder
sys.path.insert(0, str(Path(__file__).parent))

from modules import manifest


# Snapshot format version. Bump the MINOR when adding categories so older
# restore.py tools can refuse newer snapshots gracefully.
#
# 0.3.0: Taskband blob + pins list, accent palette fields, wallpaper
# style/tile/sha256/image_format, bundled cursor/sound files, mouse
# acceleration thresholds, env_vars source_profile/vars wrapper (Req 14.1).
SNAPSHOT_FORMAT_VERSION = "0.3.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_snapshot_dir(base_output: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = base_output / f"winsnap_{timestamp}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def resolve_output_path(output: Path, name: str, force: bool) -> Path:
    """
    Resolve the snapshot directory export will use when --name is given,
    handling a pre-existing destination deterministically (Req 13.3).

    If neither `<output>/<name>` (a leftover unzipped snapshot folder) nor
    `<output>/<name>.winsnap` (a previous export) exists, this simply
    returns `<output>/<name>`.

    If either exists:
      - force=False: raise FileExistsError naming every colliding path,
        BEFORE any export module has run, so the caller can fail fast
        without wasting a partial export.
      - force=True: delete the colliding directory/file(s) and return
        `<output>/<name>` for a fresh export to use.

    This replaces the old bare `snapshot_dir.rename(named)` call, which
    crashed with an unhelpful OSError on Windows whenever the destination
    already existed.
    """
    target_dir = output / name
    target_zip = output / f"{name}.winsnap"
    existing = [p for p in (target_dir, target_zip) if p.exists()]
    if existing:
        if not force:
            colliding = ", ".join(str(p) for p in existing)
            raise FileExistsError(
                f"Snapshot destination already exists: {colliding}. "
                "Use --force to overwrite, or pick a different --name."
            )
        for p in existing:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
    return target_dir


def resolve_snapshot_dir(output: Path, name: str | None, force: bool) -> Path:
    """
    Decide where an export writes to, and prepare that directory.

    Encapsulates main()'s "where does this export write to" branch as a
    single importable decision (gui-backend-alignment Req 9.1, 9.4, 10.1): a
    named export (`name` truthy) delegates to resolve_output_path for
    collision handling -- raising FileExistsError, BEFORE any module has
    run, when the destination collides and force=False, or deleting the
    colliding path(s) and returning a fresh directory when force=True. An
    unnamed export delegates to create_snapshot_dir's timestamp naming,
    which never collides.

    This is the single place either decision is made, so a GUI export and a
    CLI export can never diverge on where a snapshot folder ends up.
    """
    if name:
        target = resolve_output_path(output, name, force)
        output.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        return target
    return create_snapshot_dir(output)


def zip_snapshot(snapshot_dir: Path) -> Path:
    zip_path = snapshot_dir.parent / (snapshot_dir.name + ".winsnap")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in snapshot_dir.rglob("*"):
            zf.write(file, file.relative_to(snapshot_dir.parent))
    return zip_path


def build_snapshot_metadata(modules_attempted: list) -> dict:
    """
    Build the snapshot.json metadata dict — the exact same keys, values,
    and insertion order as main()'s inline `snapshot = {...}` literal, with
    `modules_attempted` supplied by the caller instead of assigned onto the
    dict after the fact.

    Extracted so a GUI export and a CLI export construct snapshot metadata
    through the same code path and can never structurally drift (single
    source of truth, gui-backend-alignment Req 10.1, 10.2). `modules` starts
    empty; callers populate it with per-module export results after running
    modules (matching main()'s existing pattern of running modules before
    writing snapshot.json).
    """
    return {
        "winsnap_version":         SNAPSHOT_FORMAT_VERSION,
        "snapshot_format_version": SNAPSHOT_FORMAT_VERSION,
        "exported_at":             datetime.now().isoformat(),
        "exported_on": {
            "user":    os.environ.get("USERNAME", ""),
            "machine": os.environ.get("COMPUTERNAME", ""),
        },
        "modules": {},
        "modules_attempted": modules_attempted,
    }


def write_snapshot_json(snapshot_dir: Path, snapshot: dict) -> Path:
    """
    Write `snapshot` to `snapshot_dir/snapshot.json` as pretty-printed,
    UTF-8 JSON.

    Extracted from main()'s inline `json_path.write_text(...)` call
    (gui-backend-alignment Req 10.1) so a GUI export and a CLI export write
    snapshot.json through the exact same code path.
    """
    json_path = snapshot_dir / "snapshot.json"
    json_path.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    return json_path


def cleanup_snapshot_dir(snapshot_dir: Path) -> None:
    """
    Remove the unzipped snapshot folder after it has been zipped, retrying
    with the read-only flag cleared on Windows permission errors.

    Extracted from main()'s post-zip cleanup block (gui-backend-alignment
    Req 10.1) so a GUI export and a CLI export clean up temp folders through
    the exact same code path. Never raises -- a cleanup failure is reported
    to stdout and left for the user to delete manually, matching main()'s
    existing behavior.
    """
    def _force_remove(func, path, _):
        """If rmtree hits a permission error, chmod and retry."""
        import stat
        os.chmod(path, stat.S_IWRITE)
        func(path)

    try:
        shutil.rmtree(snapshot_dir, onexc=_force_remove)
    except Exception as e:
        print(f"[export] Note: could not fully clean up temp folder: {e}")
        print(f"[export] You can safely delete it manually: {snapshot_dir}")


def run_export_modules(modules_to_run: list, snapshot_dir: Path) -> dict:
    """
    Run every (name, fn) pair in `modules_to_run` against `snapshot_dir` and
    collect the per-module results.

    Extracted from main()'s module-running loop (gui-backend-alignment Req
    10.1, 10.3) so a GUI export and a CLI export execute modules through the
    exact same code path. A module raising is recorded as
    `{"error": str(e)}` and the loop continues -- a single module's failure
    never aborts the rest of the batch, matching main()'s existing
    try/except-per-module behavior.
    """
    results: dict = {}
    for name, fn in modules_to_run:
        print(f"\n[{name}] Running...")
        try:
            results[name] = fn(snapshot_dir)
        except Exception as e:
            print(f"[{name}] ERROR: {e}")
            results[name] = {"error": str(e)}
    return results


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------
# Each entry: (name, callable). The callable takes snapshot_dir and returns
# export(snapshot_dir) -> dict. Names and order are derived from
# modules.manifest.MODULE_NAMES — the single source of truth shared with
# restore.py's ALL_MODULES — so the export module *set* can never drift from
# the restore module set (Req 2.5). Only "apps" is wrapped, to bind its
# CLI-selected headless-selection kwargs (show_all/selection/selection_file)
# without forking the run loop.

def _build_modules(args) -> list:
    modules = []
    for name in manifest.MODULE_NAMES:
        mod = importlib.import_module(f"modules.{name}")
        if name == "apps":
            fn = lambda d, mod=mod: mod.export(
                d,
                show_all=args.show_all,
                selection=args.apps_selection,
                selection_file=args.apps_from,
            )
        else:
            fn = mod.export
        modules.append((name, fn))
    return modules


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="WinSnap — export your Windows settings to a portable snapshot."
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path.home() / "Desktop",
        help="Where to save the .winsnap file (default: Desktop)"
    )
    parser.add_argument(
        "--name", "-n",
        default=None,
        help="Snapshot name (default: winsnap_<timestamp>)"
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help=("Show every installed entry in the apps checklist, including "
              "OS components, updaters, runtimes, and MSI patches. "
              "By default these are filtered out for a cleaner list.")
    )
    apps_selection_group = parser.add_mutually_exclusive_group()
    apps_selection_group.add_argument(
        "--all-apps",
        action="store_true",
        help=("Select every discovered winget/manual app for export without "
              "showing the interactive checklist (headless, Req 8.1).")
    )
    apps_selection_group.add_argument(
        "--apps-from",
        type=Path,
        default=None,
        metavar="FILE",
        help=("Select apps to export from a JSON selection file "
              '(`{"winget": [...], "manual": [...]}`) instead of the '
              "interactive checklist (headless, Req 8.2).")
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=("With --name, overwrite/delete a pre-existing snapshot folder "
              "or .winsnap file at the destination instead of failing fast.")
    )
    parser.add_argument(
        "--skip", nargs="+", metavar="MODULE", default=[],
        help="Modules to skip during export (e.g. --skip fonts startup)"
    )
    parser.add_argument(
        "--only", nargs="+", metavar="MODULE", default=[],
        help="Run only these modules (e.g. --only wallpaper taskbar)"
    )
    args = parser.parse_args()

    # Resolve the single "how should apps be selected" mode from the two
    # mutually exclusive flags -- no flag keeps the interactive default
    # (Req 8.3). _build_modules reads args.apps_selection/args.apps_from.
    if args.all_apps:
        args.apps_selection = "all"
    elif args.apps_from:
        args.apps_selection = "file"
    else:
        args.apps_selection = "interactive"

    print("=" * 55)
    print("  WinSnap — Windows Settings Exporter")
    print("=" * 55)

    # Warn if not running as admin (power plan export needs it)
    is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    if not is_admin:
        print("\n  NOTE: Not running as Administrator.")
        print("  Power plan export will be skipped.")
        print("  Right-click export.py → 'Run as administrator' to include it.")

    # Create working directory. When --name is given, resolve any collision
    # with a pre-existing snapshot folder/.winsnap file BEFORE running any
    # module (Req 13.3) via resolve_snapshot_dir -- this replaces the old
    # bare `snapshot_dir.rename(named)`, which crashed with an unhelpful
    # OSError whenever the destination already existed.
    try:
        snapshot_dir = resolve_snapshot_dir(args.output, args.name, args.force)
    except FileExistsError as e:
        print(f"\n[export] ERROR: {e}")
        sys.exit(1)
    print(f"\nSnapshot folder: {snapshot_dir}\n")

    # --- Resolve which modules to run (before metadata construction, so
    # modules_attempted can be passed straight into build_snapshot_metadata
    # instead of assigned onto the dict after the fact) ---
    all_modules = _build_modules(args)
    skip = set(args.skip)
    only = set(args.only)
    modules_to_run = [
        (name, fn) for name, fn in all_modules
        if (not only or name in only) and name not in skip
    ]
    modules_attempted = [name for name, _ in modules_to_run]

    print(f"Modules to export: {', '.join(modules_attempted)}")

    # --- Run each module ---
    results = run_export_modules(modules_to_run, snapshot_dir)

    # --- Build metadata and write snapshot.json ---
    snapshot = build_snapshot_metadata(modules_attempted)
    snapshot["modules"] = results
    write_snapshot_json(snapshot_dir, snapshot)
    print(f"\n[export] snapshot.json written.")

    # --- Zip it up ---
    zip_path = zip_snapshot(snapshot_dir)

    # Clean up the unzipped folder — force-clear read-only flags on Windows
    cleanup_snapshot_dir(snapshot_dir)
    print(f"\n{'='*55}")
    print(f"  Done! Snapshot saved to:")
    print(f"  {zip_path}")
    print(f"  Format version: {SNAPSHOT_FORMAT_VERSION}")
    print(f"{'='*55}")
    print("\nCopy this .winsnap file to your new PC and run restore.py")


if __name__ == "__main__":
    main()
