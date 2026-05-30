"""
fonts.py
Captures and restores user-installed fonts.

User fonts live at:
    %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts
and are registered under:
    HKCU\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts

System fonts (in C:\\Windows\\Fonts) are NOT touched -- those ship with Windows.

The font files (.ttf / .otf / .ttc) are bundled inside the snapshot zip
under a "fonts/" subfolder so they travel with the rest of the snapshot.

Size guard:
  If the total bundle size exceeds FONT_SIZE_WARN_MB (default 100), we print
  a warning. Set environment variable WINSNAP_SKIP_FONTS=1 to skip the export.
"""

import ctypes
import os
import shutil
import winreg
from pathlib import Path


_FONTS_REG = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
_USER_FONT_EXTS = {".ttf", ".otf", ".ttc", ".fon"}
FONT_SIZE_WARN_MB = 100


def _user_fonts_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"


def export(snapshot_dir: Path) -> dict:
    if os.environ.get("WINSNAP_SKIP_FONTS") == "1":
        print("[fonts] Skipped (WINSNAP_SKIP_FONTS=1).")
        return {"fonts": []}

    src_dir = _user_fonts_dir()
    if not src_dir.exists():
        print("[fonts] No user fonts directory found. Skipping.")
        return {"fonts": []}

    # Read registered user fonts
    registered = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _FONTS_REG)
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
                registered[name] = value
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
    except OSError:
        pass

    # Copy each font file into the snapshot under fonts/
    bundle_dir = snapshot_dir / "fonts"
    bundle_dir.mkdir(exist_ok=True)

    bundled = []
    total_bytes = 0
    for font_file in src_dir.iterdir():
        if not font_file.is_file():
            continue
        if font_file.suffix.lower() not in _USER_FONT_EXTS:
            continue
        try:
            shutil.copy2(font_file, bundle_dir / font_file.name)
            total_bytes += font_file.stat().st_size
            bundled.append({
                "filename":     font_file.name,
                # Find the friendly name from the registry, if any
                "display_name": next(
                    (k for k, v in registered.items()
                     if Path(v).name.lower() == font_file.name.lower()),
                    font_file.stem,
                ),
            })
        except OSError as e:
            print(f"[fonts] Could not copy {font_file.name}: {e}")

    size_mb = total_bytes / (1024 * 1024)
    print(f"[fonts] Bundled {len(bundled)} user fonts ({size_mb:.1f} MB).")
    if size_mb > FONT_SIZE_WARN_MB:
        print(f"[fonts] WARNING: font bundle is {size_mb:.0f} MB. "
              f"Set WINSNAP_SKIP_FONTS=1 to exclude.")

    return {"fonts": bundled}


def restore(snapshot: dict, snapshot_dir: Path):
    fonts = snapshot.get("fonts") or []
    if not fonts:
        print("[fonts] No bundled fonts to restore.")
        return

    bundle_dir = snapshot_dir / "fonts"
    target_dir = _user_fonts_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    installed = 0
    for entry in fonts:
        filename = entry.get("filename")
        if not filename:
            continue
        src = bundle_dir / filename
        if not src.exists():
            print(f"[fonts] Missing in snapshot: {filename}, skipping.")
            continue

        dst = target_dir / filename
        try:
            if not dst.exists():
                shutil.copy2(src, dst)
            # Register with HKCU
            display = entry.get("display_name", Path(filename).stem)
            try:
                winreg.CreateKey(winreg.HKEY_CURRENT_USER, _FONTS_REG)
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _FONTS_REG, 0,
                                     winreg.KEY_SET_VALUE)
                winreg.SetValueEx(key, display, 0, winreg.REG_SZ, str(dst))
                winreg.CloseKey(key)
            except OSError:
                pass

            # AddFontResourceW for the running session
            ctypes.windll.gdi32.AddFontResourceW(str(dst))
            installed += 1
        except OSError as e:
            print(f"[fonts] Could not install {filename}: {e}")

    # WM_FONTCHANGE = 0x001D, broadcast so other apps reload the font list
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001D, 0, None, 0x0002, 1000, None
    )
    print(f"[fonts] Restored {installed} user fonts.")
