"""
build.py
Build standalone .exe versions of WinSnap with PyInstaller.

Usage:
    pip install pyinstaller
    python build.py
    python build.py --clean       # remove build/ dist/ first
    python build.py --onedir      # multi-file build (faster startup)

Outputs:
    dist/winsnap-export.exe
    dist/winsnap-restore.exe

Notes:
- The default --onefile mode produces a single executable that unpacks
  to a temp dir on each run (slower start, easier to ship).
- --onedir produces a folder with the .exe + DLLs, faster but harder to ship.
- The msvcrt + ctypes + winreg modules used by WinSnap are stdlib and
  pulled in automatically by PyInstaller. The 'modules' package is added
  via --collect-submodules so every settings module is bundled even though
  some imports are conditional (e.g. 'from modules import checklist').
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def _run(cmd: list[str]) -> int:
    print(f"\n$ {' '.join(cmd)}\n", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT)
    return proc.returncode


def _ensure_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        print("ERROR: PyInstaller is not installed.")
        print("       Install it with:  pip install pyinstaller")
        return False


def _clean():
    for d in ("build", "dist", "__pycache__"):
        target = ROOT / d
        if target.exists():
            print(f"  removing {d}/")
            shutil.rmtree(target, ignore_errors=True)
    for spec in ROOT.glob("*.spec"):
        print(f"  removing {spec.name}")
        spec.unlink(missing_ok=True)


def _build(entry: str, name: str, mode: str) -> int:
    """
    entry: 'export.py' or 'restore.py'
    name:  output executable name (without .exe)
    mode:  'onefile' or 'onedir'
    """
    cmd = [
        sys.executable, "-m", "PyInstaller",
        f"--{mode}",
        "--console",
        "--clean",
        "--name", name,
        "--collect-submodules", "modules",
        # Suppress the bundled-with-WinSnap UPX prompt
        "--noupx",
        entry,
    ]
    return _run(cmd)


def main():
    parser = argparse.ArgumentParser(
        description="Build WinSnap as standalone .exe files."
    )
    parser.add_argument("--clean", action="store_true",
                        help="Remove build/, dist/, and *.spec before building")
    parser.add_argument("--onedir", action="store_true",
                        help="Use --onedir instead of --onefile (faster startup)")
    args = parser.parse_args()

    if not _ensure_pyinstaller():
        sys.exit(1)

    if args.clean:
        print("Cleaning previous build artifacts...")
        _clean()

    mode = "onedir" if args.onedir else "onefile"
    print(f"Build mode: {mode}\n")

    rc = _build("export.py", "winsnap-export", mode)
    if rc != 0:
        print(f"\nERROR: export build failed with exit code {rc}")
        sys.exit(rc)

    rc = _build("restore.py", "winsnap-restore", mode)
    if rc != 0:
        print(f"\nERROR: restore build failed with exit code {rc}")
        sys.exit(rc)

    dist = ROOT / "dist"
    print("\n" + "=" * 55)
    print("  Build successful.")
    print("=" * 55)
    if mode == "onefile":
        print(f"  {dist / 'winsnap-export.exe'}")
        print(f"  {dist / 'winsnap-restore.exe'}")
    else:
        print(f"  {dist / 'winsnap-export'}/")
        print(f"  {dist / 'winsnap-restore'}/")
    print()
    print("First run on a fresh PC will trigger Microsoft Defender SmartScreen")
    print("because the .exe is unsigned. Click 'More info -> Run anyway'.")


if __name__ == "__main__":
    main()
