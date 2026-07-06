"""
Unit tests for modules/winutil.py's image-sniffing and hashing helpers,
plus wallpaper-module-level behavior for the hardened modules/wallpaper.py.

Covers the magic-byte table from design Decision D4 (Req 5.2, 5.3) —
jpg/png/bmp/gif recognition, extensionless "TranscodedWallpaper"-style
filenames, unknown/short/empty files — a sanity check for sha256_file
(Req 5.7), and the wallpaper module's style/tile capture, style-before-SPI
write order, sha256-based verify, unknown-format best-effort path, and 0.2.0
backward compatibility.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.7, 14.2**
"""

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import FakeWinReg, _build_winreg_module

from modules import winutil
import modules.wallpaper as wp


# ---------------------------------------------------------------------------
# sniff_image_type — magic-byte table
# ---------------------------------------------------------------------------

# (header bytes, expected format) for each recognized image type. Filenames
# used in the tests below are extensionless, mirroring the real-world case
# this function exists for: Windows' TranscodedWallpaper cache file.
MAGIC_BYTE_CASES = [
    pytest.param(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01" + b"\x00" * 10, "jpg", id="jpeg"),
    pytest.param(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10, "png", id="png"),
    pytest.param(b"BM" + b"\x00" * 20, "bmp", id="bmp"),
    pytest.param(b"GIF89a" + b"\x00" * 10, "gif", id="gif89a"),
    pytest.param(b"GIF87a" + b"\x00" * 10, "gif", id="gif87a"),
]


@pytest.mark.parametrize("header, expected_format", MAGIC_BYTE_CASES)
def test_sniff_image_type_recognizes_known_formats(tmp_path, header, expected_format):
    path = tmp_path / "TranscodedWallpaper"
    path.write_bytes(header)
    assert winutil.sniff_image_type(path) == expected_format


def test_sniff_image_type_unknown_bytes_returns_none(tmp_path):
    path = tmp_path / "TranscodedWallpaper"
    path.write_bytes(b"NOT_AN_IMAGE_HEADER_AT_ALL_")
    assert winutil.sniff_image_type(path) is None


def test_sniff_image_type_short_file_returns_none(tmp_path):
    """A file shorter than any signature must not raise and must return None."""
    path = tmp_path / "tiny"
    path.write_bytes(b"\xFF")
    assert winutil.sniff_image_type(path) is None


def test_sniff_image_type_empty_file_returns_none(tmp_path):
    path = tmp_path / "empty"
    path.write_bytes(b"")
    assert winutil.sniff_image_type(path) is None


def test_sniff_image_type_missing_file_returns_none(tmp_path):
    path = tmp_path / "does_not_exist"
    assert winutil.sniff_image_type(path) is None


# ---------------------------------------------------------------------------
# sha256_file — sanity
# ---------------------------------------------------------------------------

def test_sha256_file_matches_hashlib_reference(tmp_path):
    content = b"some wallpaper bytes, not actually an image but hashable" * 100
    path = tmp_path / "wallpaper.bin"
    path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert winutil.sha256_file(path) == expected


def test_sha256_file_empty_file(tmp_path):
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert winutil.sha256_file(path) == hashlib.sha256(b"").hexdigest()


def test_sha256_file_differs_for_different_content(tmp_path):
    path_a = tmp_path / "a.bin"
    path_b = tmp_path / "b.bin"
    path_a.write_bytes(b"content A")
    path_b.write_bytes(b"content B")
    assert winutil.sha256_file(path_a) != winutil.sha256_file(path_b)


# ===========================================================================
# Task 6: wallpaper-module-level behavior (export/restore/verify)
# ===========================================================================

@dataclass
class _RecordingUser32:
    """Records SystemParametersInfoW calls into a shared event log so tests
    can assert that the style/tile registry writes precede the SPI apply."""
    events: list = field(default_factory=list)
    spi_result: int = 1

    def SystemParametersInfoW(self, action, uiParam, pvParam, fWinIni):
        self.events.append(("spi", action))
        return self.spi_result


class _Windll:
    def __init__(self, user32):
        self.user32 = user32


class _Ctypes:
    def __init__(self, user32):
        self.windll = _Windll(user32)


def _patch_wallpaper(monkeypatch, tmp_path, user32, events):
    """Route wallpaper.ctypes at the SPI boundary and wrap winutil.write_reg_value
    so both style/tile writes and the SPI apply land in the same order-sensitive
    `events` list. Path.home is redirected under tmp_path."""
    user32.events = events          # SPI apply and reg writes share one ordered log
    monkeypatch.setattr(wp, "ctypes", _Ctypes(user32))

    def recording_write(path, name, value, reg_type):
        events.append(("write", name, value))

    monkeypatch.setattr(winutil, "write_reg_value", recording_write)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(wp.Path, "home", staticmethod(lambda: home))
    return home


def _snapshot_dir_with(tmp_path, filename, content=b"\xff\xd8\xff" + b"\x00" * 32):
    d = tmp_path / "snap"
    d.mkdir(exist_ok=True)
    (d / filename).write_bytes(content)
    return d


def test_export_captures_style_tile_and_sniffed_format(monkeypatch, tmp_path):
    """A TranscodedWallpaper-style extensionless source is sniffed, and the
    WallpaperStyle/TileWallpaper registry values are recorded (Req 5.1, 5.2)."""
    src = tmp_path / "TranscodedWallpaper"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)

    fake = FakeWinReg()
    fake.values[(fake.HKEY_CURRENT_USER, r"Control Panel\Desktop", "Wallpaper")] = (str(src), fake.REG_SZ)
    fake.values[(fake.HKEY_CURRENT_USER, r"Control Panel\Desktop", "WallpaperStyle")] = ("10", fake.REG_SZ)
    fake.values[(fake.HKEY_CURRENT_USER, r"Control Panel\Desktop", "TileWallpaper")] = ("0", fake.REG_SZ)
    monkeypatch.setattr(wp, "winreg", _build_winreg_module(fake))
    monkeypatch.setattr(winutil, "winreg", _build_winreg_module(fake))

    result = wp.export(tmp_path)

    assert result["enabled"] is True
    assert result["style"] == "10"
    assert result["tile"] == "0"
    assert result["image_format"] == "png"          # sniffed from magic bytes
    assert result["filename"] == "wallpaper.png"
    assert result["sha256"] == winutil.sha256_file(tmp_path / "wallpaper.png")


def test_restore_writes_style_and_tile_before_spi(monkeypatch, tmp_path):
    """Req 5.4: the style/tile registry values must be written before the
    SPI_SETDESKWALLPAPER call so the image renders with the intended fit."""
    snapshot_dir = _snapshot_dir_with(tmp_path, "wallpaper.jpg")
    events = []
    user32 = _RecordingUser32()
    _patch_wallpaper(monkeypatch, tmp_path, user32, events)

    snapshot = {"enabled": True, "filename": "wallpaper.jpg",
                "style": "10", "tile": "0", "image_format": "jpg", "sha256": "x"}
    report = wp.restore(snapshot, snapshot_dir)

    kinds = [e[0] for e in events]
    assert kinds == ["write", "write", "spi"]           # style, tile, then SPI
    assert events[0][1] == "WallpaperStyle"
    assert events[1][1] == "TileWallpaper"
    assert report["status"] == "matched"


def test_restore_missing_style_tile_is_skipped_not_error(monkeypatch, tmp_path):
    """A 0.2.0 snapshot lacking style/tile restores without error, recording
    skipped items for the absent aspects (Req 14.2)."""
    snapshot_dir = _snapshot_dir_with(tmp_path, "wallpaper.jpg")
    events = []
    _patch_wallpaper(monkeypatch, tmp_path, _RecordingUser32(), events)

    snapshot = {"enabled": True, "filename": "wallpaper.jpg"}   # no style/tile/format
    report = wp.restore(snapshot, snapshot_dir)

    skipped = {i["name"] for i in report["items"] if i["status"] == "skipped"}
    assert "style write" in skipped
    assert "tile write" in skipped
    assert not any(e[0] == "write" for e in events)     # no writes for absent values
    assert report["status"] in ("matched", "partial")   # SPI still applied


def test_restore_unknown_format_records_best_effort_skip(monkeypatch, tmp_path):
    snapshot_dir = _snapshot_dir_with(tmp_path, "wallpaper.img")
    events = []
    _patch_wallpaper(monkeypatch, tmp_path, _RecordingUser32(), events)

    snapshot = {"enabled": True, "filename": "wallpaper.img",
                "style": "10", "tile": "0", "image_format": "unknown", "sha256": "x"}
    report = wp.restore(snapshot, snapshot_dir)

    assert any(i["name"] == "image format" and i["status"] == "skipped"
               for i in report["items"])


def test_verify_matches_on_identical_hash_style_tile(monkeypatch, tmp_path):
    """Req 5.7: verify recomputes the applied image hash and compares style/tile."""
    home = tmp_path / "home"
    (home / "Pictures" / "WinSnap").mkdir(parents=True)
    img = home / "Pictures" / "WinSnap" / "wallpaper.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"\x11" * 40)
    expected_hash = winutil.sha256_file(img)

    monkeypatch.setattr(wp.Path, "home", staticmethod(lambda: home))
    fake = FakeWinReg()
    fake.values[(fake.HKEY_CURRENT_USER, r"Control Panel\Desktop", "Wallpaper")] = (str(img), fake.REG_SZ)
    fake.values[(fake.HKEY_CURRENT_USER, r"Control Panel\Desktop", "WallpaperStyle")] = ("10", fake.REG_SZ)
    fake.values[(fake.HKEY_CURRENT_USER, r"Control Panel\Desktop", "TileWallpaper")] = ("0", fake.REG_SZ)
    monkeypatch.setattr(wp, "winreg", _build_winreg_module(fake))
    monkeypatch.setattr(winutil, "winreg", _build_winreg_module(fake))

    snapshot = {"enabled": True, "filename": "wallpaper.jpg",
                "style": "10", "tile": "0", "sha256": expected_hash}
    report = wp.verify(snapshot, tmp_path)

    assert report["status"] == "matched"
    assert fake.writes == []                              # verify is read-only


def test_verify_fails_on_style_mismatch(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "Pictures" / "WinSnap").mkdir(parents=True)
    img = home / "Pictures" / "WinSnap" / "wallpaper.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"\x22" * 40)
    expected_hash = winutil.sha256_file(img)

    monkeypatch.setattr(wp.Path, "home", staticmethod(lambda: home))
    fake = FakeWinReg()
    fake.values[(fake.HKEY_CURRENT_USER, r"Control Panel\Desktop", "Wallpaper")] = (str(img), fake.REG_SZ)
    fake.values[(fake.HKEY_CURRENT_USER, r"Control Panel\Desktop", "WallpaperStyle")] = ("2", fake.REG_SZ)
    fake.values[(fake.HKEY_CURRENT_USER, r"Control Panel\Desktop", "TileWallpaper")] = ("0", fake.REG_SZ)
    monkeypatch.setattr(wp, "winreg", _build_winreg_module(fake))
    monkeypatch.setattr(winutil, "winreg", _build_winreg_module(fake))

    snapshot = {"enabled": True, "filename": "wallpaper.jpg",
                "style": "10", "tile": "0", "sha256": expected_hash}
    report = wp.verify(snapshot, tmp_path)

    assert report["status"] in ("failed", "partial")
    style_item = next(i for i in report["items"] if i["name"] == "style")
    assert style_item["status"] == "failed"
    assert style_item["expected"] == "10"
    assert style_item["actual"] == "2"
