"""
test_taskband.py — tests for the hardened modules/taskbar.py.

Feature: backend-roundtrip-hardening, Task 9.

Covers:
  - The Taskband Favorites/FavoritesResolve REG_BINARY blobs are captured
    base64 and written back byte-for-byte on restore (Req 1.1, 1.2).
  - A 0.2.0 snapshot without a taskband blob restores the .lnk files only and
    records an explicit skipped "pin state predates capture" item, never a
    silent success (Req 1.5).
  - An incomplete/failed blob write yields a failed item so the category
    aggregates to partial/failed rather than matched (Req 1.4).
  - INLINE_EXPLORER_RESTART gates the inline Explorer restart: True (legacy /
    GUI default) restarts inline; False (restore.py orchestration) instead
    marks explorer_restart_required and does not restart (Req 1.3, 2.2, D2).
  - verify() compares the Taskband blob byte-for-byte and the AccentPalette
    byte-for-byte, matched/failed appropriately, and is read-only (Req 1.6,
    9.4).

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 9.1, 9.2, 9.3, 9.4, 2.2**
"""

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import FakeWinReg, _build_winreg_module

import modules.taskbar as taskbar
from modules import winutil


FAV_BYTES = b"\x00\x01\x02\xfa\xfb\xfc" * 4
FAVRES_BYTES = b"\x10\x20\x30\x40" * 8
PALETTE_BYTES = b"\xaa\xbb\xcc\xdd" * 6


def _patch(monkeypatch, fake, *, inline_restart=False, pins_dir=None):
    monkeypatch.setattr(taskbar, "winreg", _build_winreg_module(fake))
    monkeypatch.setattr(taskbar, "INLINE_EXPLORER_RESTART", inline_restart)
    restarts = []
    monkeypatch.setattr(winutil, "restart_explorer", lambda: restarts.append(True))
    if pins_dir is not None:
        monkeypatch.setattr(taskbar, "TASKBAR_PINS_DIR", pins_dir)
    return restarts


def _b64(b):
    return base64.b64encode(b).decode("ascii")


# ---------------------------------------------------------------------------
# Restore: Taskband blob byte round trip
# ---------------------------------------------------------------------------

def test_restore_writes_taskband_blob_byte_for_byte(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch(monkeypatch, fake)

    snapshot = {
        "pins_backup": None,
        "taskband": {"favorites": _b64(FAV_BYTES),
                     "favorites_resolve": _b64(FAVRES_BYTES)},
        "theme": {},
    }
    report = taskbar.restore(snapshot, tmp_path)

    fav_writes = fake.get_writes_for("Favorites")
    favres_writes = fake.get_writes_for("FavoritesResolve")
    assert len(fav_writes) == 1 and fav_writes[0][4] == fake.REG_BINARY
    assert fav_writes[0][5] == FAV_BYTES                 # exact bytes, decoded
    assert favres_writes[0][5] == FAVRES_BYTES
    taskband_item = next(i for i in report["items"] if i["name"] == "taskband")
    assert taskband_item["status"] == "matched"


def test_restore_020_snapshot_skips_taskband(monkeypatch, tmp_path):
    """A snapshot predating Taskband capture restores .lnk only and records a
    skipped pin-state item — never a false success (Req 1.5)."""
    pins_src = tmp_path / "snap" / "taskbar_pins"
    pins_src.mkdir(parents=True)
    (pins_src / "App.lnk").write_bytes(b"lnk")
    pins_dir = tmp_path / "live_pins"
    fake = FakeWinReg()
    _patch(monkeypatch, fake, pins_dir=pins_dir)

    snapshot = {"pins_backup": "taskbar_pins", "theme": {}}   # no "taskband" key
    report = taskbar.restore(snapshot, tmp_path / "snap")

    taskband_item = next(i for i in report["items"] if i["name"] == "taskband")
    assert taskband_item["status"] == "skipped"
    assert "predates Taskband capture" in taskband_item["detail"]
    # The .lnk file was still copied.
    assert (pins_dir / "App.lnk").exists()
    assert fake.get_writes_for("Favorites") == []


def test_restore_incomplete_blob_is_failed_not_matched(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch(monkeypatch, fake)

    snapshot = {
        "pins_backup": None,
        "taskband": {"favorites": _b64(FAV_BYTES)},       # missing favorites_resolve
        "theme": {"apps_light_theme": 1},                 # a matched item -> partial
    }
    report = taskbar.restore(snapshot, tmp_path)

    taskband_item = next(i for i in report["items"] if i["name"] == "taskband")
    assert taskband_item["status"] == "failed"
    assert report["status"] == "partial"                  # failed blob + matched theme


# ---------------------------------------------------------------------------
# Explorer restart gating (Req 1.3, 2.2, D2)
# ---------------------------------------------------------------------------

def test_orchestrated_mode_defers_restart(monkeypatch, tmp_path):
    fake = FakeWinReg()
    restarts = _patch(monkeypatch, fake, inline_restart=False)

    snapshot = {"pins_backup": None,
                "taskband": {"favorites": _b64(FAV_BYTES),
                             "favorites_resolve": _b64(FAVRES_BYTES)},
                "theme": {}}
    report = taskbar.restore(snapshot, tmp_path)

    assert restarts == []                                 # no inline restart
    assert report["explorer_restart_required"] is True


def test_legacy_mode_restarts_inline(monkeypatch, tmp_path):
    fake = FakeWinReg()
    restarts = _patch(monkeypatch, fake, inline_restart=True)

    snapshot = {"pins_backup": None,
                "taskband": {"favorites": _b64(FAV_BYTES),
                             "favorites_resolve": _b64(FAVRES_BYTES)},
                "theme": {}}
    report = taskbar.restore(snapshot, tmp_path)

    assert restarts == [True]                             # inline restart happened
    assert report["explorer_restart_required"] is False


# ---------------------------------------------------------------------------
# Verify: byte-for-byte blob + accent palette (Req 1.6, 9.4), read-only
# ---------------------------------------------------------------------------

def _populate_taskband(fake, fav, favres):
    fake.values[(fake.HKEY_CURRENT_USER, taskbar.TASKBAND_KEY_PATH, "Favorites")] = (fav, fake.REG_BINARY)
    fake.values[(fake.HKEY_CURRENT_USER, taskbar.TASKBAND_KEY_PATH, "FavoritesResolve")] = (favres, fake.REG_BINARY)


def test_verify_matches_identical_taskband_blob(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch(monkeypatch, fake)
    _populate_taskband(fake, FAV_BYTES, FAVRES_BYTES)

    data = {"pins": None,
            "taskband": {"favorites": _b64(FAV_BYTES),
                         "favorites_resolve": _b64(FAVRES_BYTES)},
            "theme": {}}
    report = taskbar.verify(data, tmp_path)

    taskband_item = next(i for i in report["items"] if i["name"] == "taskband")
    assert taskband_item["status"] == "matched"
    assert fake.writes == []                              # verify is read-only


def test_verify_fails_on_taskband_blob_mismatch(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch(monkeypatch, fake)
    _populate_taskband(fake, b"\x99" * 12, FAVRES_BYTES)  # live Favorites differs

    data = {"pins": None,
            "taskband": {"favorites": _b64(FAV_BYTES),
                         "favorites_resolve": _b64(FAVRES_BYTES)},
            "theme": {}}
    report = taskbar.verify(data, tmp_path)

    taskband_item = next(i for i in report["items"] if i["name"] == "taskband")
    assert taskband_item["status"] == "failed"


def test_verify_accent_palette_byte_for_byte(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch(monkeypatch, fake)
    fake.values[(fake.HKEY_CURRENT_USER, taskbar.ACCENT_KEY_PATH, "AccentPalette")] = (
        PALETTE_BYTES, fake.REG_BINARY)

    data = {"pins": None, "taskband": None,
            "theme": {"accent_palette": _b64(PALETTE_BYTES),
                      "accent_color_menu": None, "start_color_menu": None}}
    report = taskbar.verify(data, tmp_path)

    accent_item = next(i for i in report["items"] if i["name"] == "accent_palette")
    assert accent_item["status"] == "matched"
    assert fake.writes == []
