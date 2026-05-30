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

from modules import (
    wallpaper, apps, mouse_display, power, taskbar,
    explorer, desktop_icons, sound_scheme, cursors,
    fonts, startup, env_vars, region_lang,
)


# Snapshot format version. Bump the MINOR when adding categories so older
# restore.py tools can refuse newer snapshots gracefully.
SNAPSHOT_FORMAT_VERSION = "0.2.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_snapshot_dir(base_output: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = base_output / f"winsnap_{timestamp}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def zip_snapshot(snapshot_dir: Path) -> Path:
    zip_path = snapshot_dir.parent / (snapshot_dir.name + ".winsnap")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in snapshot_dir.rglob("*"):
            zf.write(file, file.relative_to(snapshot_dir.parent))
    return zip_path


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------
# Each entry: (name, callable_factory). The factory takes parsed args and
# returns a callable export(snapshot_dir) -> dict. This lets us bind per-module
# CLI options (e.g. show_all for apps) without forking the run loop.

def _build_modules(args) -> list:
    return [
        ("wallpaper",     wallpaper.export),
        ("apps",          lambda d: apps.export(d, show_all=args.show_all)),
        ("mouse_display", mouse_display.export),
        ("power",         power.export),
        ("taskbar",       taskbar.export),
        ("explorer",      explorer.export),
        ("desktop_icons", desktop_icons.export),
        ("sound_scheme",  sound_scheme.export),
        ("cursors",       cursors.export),
        ("fonts",         fonts.export),
        ("startup",       startup.export),
        ("env_vars",      env_vars.export),
        ("region_lang",   region_lang.export),
    ]


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
    parser.add_argument(
        "--skip", nargs="+", metavar="MODULE", default=[],
        help="Modules to skip during export (e.g. --skip fonts startup)"
    )
    parser.add_argument(
        "--only", nargs="+", metavar="MODULE", default=[],
        help="Run only these modules (e.g. --only wallpaper taskbar)"
    )
    args = parser.parse_args()

    print("=" * 55)
    print("  WinSnap — Windows Settings Exporter")
    print("=" * 55)

    # Warn if not running as admin (power plan export needs it)
    is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    if not is_admin:
        print("\n  NOTE: Not running as Administrator.")
        print("  Power plan export will be skipped.")
        print("  Right-click export.py → 'Run as administrator' to include it.")

    # Create working directory
    snapshot_dir = create_snapshot_dir(args.output)
    if args.name:
        named = snapshot_dir.parent / args.name
        snapshot_dir.rename(named)
        snapshot_dir = named
    print(f"\nSnapshot folder: {snapshot_dir}\n")

    snapshot = {
        "winsnap_version":         SNAPSHOT_FORMAT_VERSION,
        "snapshot_format_version": SNAPSHOT_FORMAT_VERSION,
        "exported_at":             datetime.now().isoformat(),
        "exported_on": {
            "user":    os.environ.get("USERNAME", ""),
            "machine": os.environ.get("COMPUTERNAME", ""),
        },
        "modules": {},
    }

    # --- Resolve which modules to run ---
    all_modules = _build_modules(args)
    skip = set(args.skip)
    only = set(args.only)
    modules_to_run = [
        (name, fn) for name, fn in all_modules
        if (not only or name in only) and name not in skip
    ]
    snapshot["modules_attempted"] = [name for name, _ in modules_to_run]

    print(f"Modules to export: {', '.join(snapshot['modules_attempted'])}")

    # --- Run each module ---
    for name, fn in modules_to_run:
        print(f"\n[{name}] Running...")
        try:
            result = fn(snapshot_dir)
            snapshot["modules"][name] = result
        except Exception as e:
            print(f"[{name}] ERROR: {e}")
            snapshot["modules"][name] = {"error": str(e)}

    # --- Write snapshot.json ---
    json_path = snapshot_dir / "snapshot.json"
    json_path.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\n[export] snapshot.json written.")

    # --- Zip it up ---
    zip_path = zip_snapshot(snapshot_dir)

    # Clean up the unzipped folder — force-clear read-only flags on Windows
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
    print(f"\n{'='*55}")
    print(f"  Done! Snapshot saved to:")
    print(f"  {zip_path}")
    print(f"  Format version: {SNAPSHOT_FORMAT_VERSION}")
    print(f"{'='*55}")
    print("\nCopy this .winsnap file to your new PC and run restore.py")


if __name__ == "__main__":
    main()
