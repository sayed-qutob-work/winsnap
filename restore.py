"""
restore.py  —  WinSnap restorer
Run this on your TARGET (new) machine.

Usage:
    python restore.py my_snapshot.winsnap
    python restore.py my_snapshot.winsnap --skip apps
    python restore.py my_snapshot.winsnap --only wallpaper taskbar
    python restore.py my_snapshot.winsnap --dry-run

What it does:
1. Extracts the .winsnap archive to a temp folder
2. Reads snapshot.json
3. Runs each module's restore() in order
4. Cleans up the temp folder
"""

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

# Force UTF-8 stdout/stderr so unicode in our messages doesn't crash on
# Windows consoles that default to cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent))

from modules import (
    wallpaper, apps, mouse_display, power, taskbar,
    explorer, desktop_icons, sound_scheme, cursors,
    fonts, startup, env_vars, region_lang,
)


# Maximum snapshot format MAJOR version this restorer understands.
# We accept anything in the same MAJOR series; refuse a newer MAJOR.
SUPPORTED_MAJOR = 0


# Ordered list of (key, module). Order matters for restore:
#   - settings before things that restart Explorer
#   - apps last (longest-running)
ALL_MODULES = [
    ("env_vars",      env_vars),
    ("region_lang",   region_lang),
    ("wallpaper",     wallpaper),
    ("mouse_display", mouse_display),
    ("cursors",       cursors),
    ("sound_scheme",  sound_scheme),
    ("power",         power),
    ("explorer",      explorer),
    ("desktop_icons", desktop_icons),
    ("fonts",         fonts),
    ("startup",       startup),
    ("taskbar",       taskbar),   # restarts Explorer — keep near end
    ("apps",          apps),      # potentially long — keep last
]


def _check_format_version(snapshot: dict) -> bool:
    """Return True if we can safely restore this snapshot."""
    raw = (snapshot.get("snapshot_format_version")
           or snapshot.get("winsnap_version")
           or "0.1.0")
    try:
        major = int(str(raw).split(".")[0])
    except (ValueError, IndexError):
        print(f"  WARNING: unrecognized version format {raw!r}, "
              f"attempting restore anyway.")
        return True

    if major > SUPPORTED_MAJOR:
        print(f"  ERROR: snapshot format v{raw} is newer than this restorer "
              f"supports (v{SUPPORTED_MAJOR}.x). Update WinSnap and try again.")
        return False
    return True


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
            return f"would restore {len(data)} environment variable(s)"
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

    # --- Extract snapshot ---
    tmp_dir = Path(tempfile.mkdtemp(prefix="winsnap_restore_"))
    print(f"\nExtracting snapshot to: {tmp_dir}")
    with zipfile.ZipFile(args.snapshot, "r") as zf:
        zf.extractall(tmp_dir)

    # The zip contains one top-level folder named winsnap_<timestamp>
    extracted_dirs = [d for d in tmp_dir.iterdir() if d.is_dir()]
    if not extracted_dirs:
        print("Error: snapshot archive appears empty.")
        shutil.rmtree(tmp_dir)
        sys.exit(1)
    snapshot_dir = extracted_dirs[0]

    # --- Load snapshot.json ---
    json_path = snapshot_dir / "snapshot.json"
    if not json_path.exists():
        print("Error: snapshot.json not found in archive.")
        shutil.rmtree(tmp_dir)
        sys.exit(1)

    snapshot = json.loads(json_path.read_text(encoding="utf-8"))
    print(f"\nSnapshot from: {snapshot.get('exported_at', 'unknown date')}")
    print(f"WinSnap version: {snapshot.get('winsnap_version', '?')}")
    fmt_ver = snapshot.get("snapshot_format_version", "?")
    print(f"Snapshot format: {fmt_ver}\n")

    if not _check_format_version(snapshot):
        shutil.rmtree(tmp_dir)
        sys.exit(2)

    # --- Run restore modules (or just summarize on dry-run) ---
    modules_data = snapshot.get("modules", {})
    errors = []

    for key, mod in modules_to_run:
        if key not in modules_data:
            print(f"[{key}] Not found in snapshot. Skipping.")
            continue
        if isinstance(modules_data[key], dict) and "error" in modules_data[key]:
            print(f"[{key}] Was not captured (export error). Skipping.")
            continue

        if args.dry_run:
            summary = _summarize(key, modules_data[key])
            print(f"[{key}] {summary}")
            continue

        print(f"\n[{key}] Restoring...")
        try:
            mod.restore(modules_data[key], snapshot_dir)
        except Exception as e:
            print(f"[{key}] ERROR during restore: {e}")
            errors.append((key, str(e)))

    # --- Cleanup ---
    shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n{'='*55}")
    if args.dry_run:
        print("  Dry-run complete. Nothing was changed.")
    elif errors:
        print(f"  Restore completed with {len(errors)} error(s):")
        for key, err in errors:
            print(f"    [{key}] {err}")
    else:
        print("  Restore completed successfully!")
    print(f"{'='*55}")
    if not args.dry_run:
        print("\nNote: Some changes (DPI, theme, env vars) may require a "
              "logout/restart to fully apply.")


if __name__ == "__main__":
    main()
