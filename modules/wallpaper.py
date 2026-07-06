"""
wallpaper.py
Captures and restores the desktop wallpaper.

Export: reads the current wallpaper path, style (WallpaperStyle) and tiling
        (TileWallpaper) from the registry, copies the image file into the
        snapshot folder (sniffing its real format from magic bytes when the
        source has no usable extension, e.g. Windows' TranscodedWallpaper
        cache file), and returns metadata + a content hash for snapshot.json.

Restore: copies the saved wallpaper image to a stable location, writes the
         captured WallpaperStyle/TileWallpaper registry values, and applies
         the image via the legacy SystemParametersInfoW API. There is no
         per-monitor COM path: SystemParametersInfoW(SPI_SETDESKWALLPAPER)
         applies the single captured image across all monitors, which is all
         this snapshot format represents (per-monitor distinct wallpapers
         would be a different, out-of-scope settings category).

Verify: re-reads Wallpaper/WallpaperStyle/TileWallpaper from the registry and
        compares the applied image's content hash against the snapshot.
"""

import os
import shutil
import ctypes
import winreg
from pathlib import Path

from modules import winutil
from modules.report import Report


# Extensions we trust at face value; anything else (or no extension at all,
# e.g. Windows' TranscodedWallpaper cache file) is sniffed from magic bytes.
_KNOWN_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif"}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(snapshot_dir: Path) -> dict:
    """
    Returns a dict that will be stored under snapshot["wallpaper"].
    Copies the wallpaper image into snapshot_dir/wallpaper.<ext> (or
    wallpaper.img when the format cannot be determined) and records the
    WallpaperStyle/TileWallpaper registry values alongside it.
    """
    wallpaper_path = _get_current_wallpaper_path()

    if not wallpaper_path or not os.path.isfile(wallpaper_path):
        print("[wallpaper] No wallpaper set or file not found. Skipping.")
        return {"enabled": False}

    style, tile = _read_style_and_tile()

    ext = Path(wallpaper_path).suffix.lower()
    if ext in _KNOWN_EXTENSIONS:
        image_format = ext.lstrip(".")
        dest = snapshot_dir / f"wallpaper{ext}"
    else:
        sniffed = winutil.sniff_image_type(Path(wallpaper_path))
        if sniffed:
            image_format = sniffed
            dest = snapshot_dir / f"wallpaper.{sniffed}"
        else:
            image_format = "unknown"
            dest = snapshot_dir / "wallpaper.img"

    shutil.copy2(wallpaper_path, dest)
    sha256 = winutil.sha256_file(dest)

    print(f"[wallpaper] Captured: {wallpaper_path}")
    return {
        "enabled": True,
        "filename": dest.name,          # e.g. "wallpaper.jpg"
        "original_path": wallpaper_path,
        "style": style,                 # WallpaperStyle, e.g. "10", or None
        "tile": tile,                   # TileWallpaper, e.g. "0", or None
        "image_format": image_format,   # "jpg"|"jpeg"|"png"|"bmp"|"gif"|"unknown"
        "sha256": sha256,
    }


def _get_current_wallpaper_path() -> str | None:
    """Reads the wallpaper path from the Windows registry."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Desktop"
        )
        value, _ = winreg.QueryValueEx(key, "Wallpaper")
        winreg.CloseKey(key)
        return value if value else None
    except OSError:
        return None


def _read_style_and_tile() -> tuple[str | None, str | None]:
    """Reads WallpaperStyle and TileWallpaper (REG_SZ) from
    HKCU\\Control Panel\\Desktop. Returns (style, tile), either of which may
    be None if the value is absent (older Windows/registry state)."""
    style_result = winutil.read_reg_value(r"Control Panel\Desktop", "WallpaperStyle")
    tile_result = winutil.read_reg_value(r"Control Panel\Desktop", "TileWallpaper")
    style = style_result[0] if style_result else None
    tile = tile_result[0] if tile_result else None
    return style, tile


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE   = 0x01
SPIF_SENDCHANGE      = 0x02


def _apply_wallpaper_legacy(dest: Path) -> bool:
    """Apply wallpaper using the SystemParametersInfoW API. Returns whether
    the API call reported success (nonzero return value)."""
    result = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER,
        0,
        str(dest),
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
    )
    if result:
        print(f"[wallpaper] Applied: {dest}")
    else:
        print("[wallpaper] Failed to apply wallpaper (SystemParametersInfoW returned 0).")
    return bool(result)


def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    """
    Applies the wallpaper from snapshot_dir onto the current Windows session.
    snapshot is the dict stored under snapshot["wallpaper"].

    Returns a finalized restore-phase Report. Item order: file copy, style
    write, tile write, SPI apply -- the style/tile registry values are
    written before the SPI call so the applied image renders with the
    intended fit/tiling from the moment it appears (Req 5.4).
    """
    rpt = Report("wallpaper", "restore")

    if not snapshot.get("enabled"):
        return rpt.skip_all("nothing to restore (wallpaper capture disabled)")

    src = snapshot_dir / snapshot["filename"]
    if not src.exists():
        rpt.add_failed("file copy", detail=f"wallpaper file missing from snapshot: {src}")
        return rpt.finalize()

    # Copy to a permanent location (Pictures folder) so it survives USB removal
    pictures = Path.home() / "Pictures" / "WinSnap"
    try:
        pictures.mkdir(parents=True, exist_ok=True)
        dest = pictures / snapshot["filename"]
        shutil.copy2(src, dest)
        rpt.add_matched("file copy", detail=str(dest))
    except OSError as e:
        rpt.add_failed("file copy", detail=str(e))
        return rpt.finalize()

    if snapshot.get("image_format") == "unknown":
        rpt.add_skipped(
            "image format",
            detail="image format could not be determined at export time; "
                   "applying best-effort",
        )

    style = snapshot.get("style")
    if style is not None:
        try:
            winutil.write_reg_value(r"Control Panel\Desktop", "WallpaperStyle",
                                     str(style), winreg.REG_SZ)
            rpt.add_matched("style write", detail=f"WallpaperStyle={style}")
        except OSError as e:
            rpt.add_failed("style write", detail=str(e))
    else:
        rpt.add_skipped("style write", detail="snapshot predates style capture")

    tile = snapshot.get("tile")
    if tile is not None:
        try:
            winutil.write_reg_value(r"Control Panel\Desktop", "TileWallpaper",
                                     str(tile), winreg.REG_SZ)
            rpt.add_matched("tile write", detail=f"TileWallpaper={tile}")
        except OSError as e:
            rpt.add_failed("tile write", detail=str(e))
    else:
        rpt.add_skipped("tile write", detail="snapshot predates tile capture")

    applied = _apply_wallpaper_legacy(dest)
    if applied:
        rpt.add_matched("SPI apply", detail=str(dest))
    else:
        rpt.add_failed("SPI apply", detail="SystemParametersInfoW returned 0")

    return rpt.finalize()


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def verify(snapshot: dict, snapshot_dir: Path) -> dict:
    """
    Read-only re-check of the applied wallpaper state against the snapshot:
    re-reads Wallpaper/WallpaperStyle/TileWallpaper from the registry and
    compares the applied image's content hash against the snapshot's
    recorded sha256. Fields absent from an older snapshot are reported
    skipped, never matched or failed (Req 14.4).
    """
    rpt = Report("wallpaper", "verify")

    if not snapshot.get("enabled"):
        return rpt.skip_all("wallpaper capture was disabled in snapshot")

    expected_hash = snapshot.get("sha256")
    if expected_hash is None:
        rpt.add_skipped("wallpaper content", detail="snapshot predates sha256 capture")
    else:
        candidates = []
        current_path = _get_current_wallpaper_path()
        if current_path and os.path.isfile(current_path):
            candidates.append(Path(current_path))
        winsnap_copy = Path.home() / "Pictures" / "WinSnap" / snapshot.get("filename", "")
        if winsnap_copy.is_file():
            candidates.append(winsnap_copy)

        actual_hash = None
        matched = False
        for candidate in candidates:
            try:
                candidate_hash = winutil.sha256_file(candidate)
            except OSError:
                continue
            actual_hash = candidate_hash
            if candidate_hash == expected_hash:
                matched = True
                break

        if matched:
            rpt.add_matched("wallpaper content", expected=expected_hash, actual=actual_hash)
        elif candidates:
            rpt.add_failed("wallpaper content",
                            detail="no candidate wallpaper file matched the snapshot hash",
                            expected=expected_hash, actual=actual_hash)
        else:
            rpt.add_failed("wallpaper content",
                            detail="registry Wallpaper path missing and no WinSnap copy found",
                            expected=expected_hash, actual=None)

    expected_style = snapshot.get("style")
    if expected_style is None:
        rpt.add_skipped("style", detail="snapshot predates style capture")
    else:
        result = winutil.read_reg_value(r"Control Panel\Desktop", "WallpaperStyle")
        actual_style = result[0] if result else None
        if actual_style == expected_style:
            rpt.add_matched("style", expected=expected_style, actual=actual_style)
        else:
            rpt.add_failed("style", expected=expected_style, actual=actual_style)

    expected_tile = snapshot.get("tile")
    if expected_tile is None:
        rpt.add_skipped("tile", detail="snapshot predates tile capture")
    else:
        result = winutil.read_reg_value(r"Control Panel\Desktop", "TileWallpaper")
        actual_tile = result[0] if result else None
        if actual_tile == expected_tile:
            rpt.add_matched("tile", expected=expected_tile, actual=actual_tile)
        else:
            rpt.add_failed("tile", expected=expected_tile, actual=actual_tile)

    return rpt.finalize()
