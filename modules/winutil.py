"""
winutil.py
Shared Windows utility helpers used across capture/restore modules.

Provides:
  - restart_explorer(): kill and relaunch explorer.exe so shell-level changes
    (taskbar pins, theme, icon layout) take effect. The canonical version of
    the logic previously inlined in modules/taskbar.py:_restart_explorer.
  - is_admin(): whether the current process holds administrator privileges.
    The canonical version of the logic previously inlined in
    modules/power.py:_is_admin.
  - sniff_image_type(): identify an image file's format from its magic bytes,
    for files that lack (or have an untrustworthy) extension — e.g. Windows'
    TranscodedWallpaper cache file.
  - sha256_file(): content hash of a file, used by wallpaper verification.
  - read_reg_value()/write_reg_value(): thin HKCU-only registry helpers for
    new call sites (Taskband blob, Accent key, wallpaper style values, ...).
    Existing module-local registry helpers are left as-is; these are for new
    code so the diff for existing modules stays reviewable.

Stdlib only — no comtypes or other third-party imports.
"""

import ctypes
import hashlib
import subprocess
import winreg
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Explorer restart
# ---------------------------------------------------------------------------

def restart_explorer() -> bool:
    """Kills and restarts Explorer so all shell changes take effect.

    Returns True if the kill/relaunch steps were attempted without raising.
    Explorer restart is best-effort: `taskkill` returns non-zero if Explorer
    was not running, which is not itself treated as a failure here.
    """
    print("[winutil] Restarting Explorer to apply changes...")
    try:
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"],
                       capture_output=True)
        subprocess.Popen(["explorer.exe"])
    except OSError as e:
        print(f"[winutil] Failed to restart Explorer: {e}")
        return False
    print("[winutil] Explorer restarted.")
    return True


# ---------------------------------------------------------------------------
# Admin check
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    """Returns True if the current process has administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Image magic-byte sniffing
# ---------------------------------------------------------------------------

# (signature bytes, format name), checked in order against a file's header.
_IMAGE_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xFF\xD8\xFF", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"BM", "bmp"),
    (b"GIF8", "gif"),
]


def sniff_image_type(path: Path) -> str | None:
    """Identifies an image file's format from its first bytes (magic numbers).

    Returns "jpg", "png", "bmp", or "gif" when recognized, else None. Used
    for wallpaper files with no usable extension (e.g. Windows'
    TranscodedWallpaper cache file) or an untrusted one.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(16)
    except OSError:
        return None

    for signature, fmt in _IMAGE_SIGNATURES:
        if header.startswith(signature):
            return fmt
    return None


# ---------------------------------------------------------------------------
# File hashing
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    """Returns the hex-encoded SHA-256 digest of a file's contents."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# HKCU registry helpers (for new call sites)
# ---------------------------------------------------------------------------

def read_reg_value(path: str, name: str) -> tuple[Any, int] | None:
    """Reads a value from HKEY_CURRENT_USER\\<path>.

    Returns (value, reg_type) as given by winreg.QueryValueEx, or None if
    the key or value does not exist.
    """
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        try:
            return winreg.QueryValueEx(key, name)
        finally:
            winreg.CloseKey(key)
    except OSError:
        return None


def write_reg_value(path: str, name: str, value: Any, reg_type: int) -> None:
    """Writes a value to HKEY_CURRENT_USER\\<path>, creating the key if needed.

    Raises OSError on failure — callers are expected to catch this and record
    a failed item on their Report rather than let it propagate silently.
    """
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
    try:
        winreg.SetValueEx(key, name, 0, reg_type, value)
    finally:
        winreg.CloseKey(key)
