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

restore() returns a report.Report dict: each font is one item covering both
the file copy and the registry registration. verify() confirms the font
file exists in the user fonts directory and its registry value is present;
loading a font into the *running* session (AddFontResourceW) cannot be
verified after the fact, so that aspect is always reported as an explicit
skipped item rather than silently counted as matched.
"""

import ctypes
import os
import shutil
import winreg
from pathlib import Path

from modules.report import Report


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


def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    report = Report("fonts", "restore")
    fonts = snapshot.get("fonts") or []
    if not fonts:
        return report.skip_all("no fonts in snapshot")

    bundle_dir = snapshot_dir / "fonts"
    target_dir = _user_fonts_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    for entry in fonts:
        filename = entry.get("filename")
        if not filename:
            report.add_failed("<unnamed>", detail="entry missing filename")
            continue
        src = bundle_dir / filename
        if not src.exists():
            report.add_skipped(filename, detail="missing in snapshot bundle")
            continue

        dst = target_dir / filename
        try:
            if not dst.exists():
                shutil.copy2(src, dst)
        except OSError as e:
            report.add_failed(filename, detail=f"could not copy: {e}")
            continue

        # Register with HKCU
        display = entry.get("display_name", Path(filename).stem)
        try:
            winreg.CreateKey(winreg.HKEY_CURRENT_USER, _FONTS_REG)
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _FONTS_REG, 0,
                                 winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, display, 0, winreg.REG_SZ, str(dst))
            winreg.CloseKey(key)
        except OSError as e:
            report.add_failed(filename, detail=f"could not register: {e}")
            continue

        # AddFontResourceW for the running session -- best effort; the file
        # copy and registry registration above are what verify() can check
        # after the fact, so a failure here doesn't change this item's status.
        try:
            ctypes.windll.gdi32.AddFontResourceW(str(dst))
        except OSError:
            pass

        report.add_matched(filename, detail="copied and registered")

    # WM_FONTCHANGE = 0x001D, broadcast so other apps reload the font list
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001D, 0, None, 0x0002, 1000, None
    )
    result = report.finalize()
    print(f"[fonts] restore: {result['status']} ({len(report.items)} item(s)).")
    return result


def verify(data: dict, snapshot_dir: Path) -> dict:
    """Read-only: confirms each bundled font's file exists in the user fonts
    directory and its registry value is present. Live AddFontResourceW
    session state cannot be re-checked after the fact and is reported as an
    explicit skipped item rather than assumed matched (Req 7.6)."""
    report = Report("fonts", "verify")
    fonts = data.get("fonts") or []
    if not fonts:
        return report.skip_all("no fonts in snapshot")

    target_dir = _user_fonts_dir()

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

    registered_files = {Path(v).name.lower() for v in registered.values() if v}

    for entry in fonts:
        filename = entry.get("filename")
        if not filename:
            continue
        dst = target_dir / filename
        file_exists = dst.exists()
        reg_present = filename.lower() in registered_files

        if file_exists and reg_present:
            report.add_matched(filename, detail="file present and registered",
                                expected=str(dst), actual=str(dst))
        else:
            missing = []
            if not file_exists:
                missing.append("file")
            if not reg_present:
                missing.append("registry value")
            report.add_failed(filename, detail=f"missing: {', '.join(missing)}",
                               expected=str(dst),
                               actual=str(dst) if file_exists else None)

    report.add_skipped("live_font_load", detail="live font load not verifiable")
    return report.finalize()
