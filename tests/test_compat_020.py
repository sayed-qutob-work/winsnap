"""
tests/test_compat_020.py — Backward-compatibility suite for 0.2.0 snapshots.

Feature: backend-roundtrip-hardening, Task 15 (Req 1.5, 9.3, 11.3, 14.2,
14.3, 14.4; Design D7).

Uses the Task 3 `stage_v020_snapshot` conftest helper, which builds a full
snapshot dict in the pre-hardening 0.2.0 shape covering env_vars (flat map,
no source_profile/vars wrapper), taskbar (no "pins" list, no "taskband"
blob, theme without the accent trio), wallpaper (no style/tile/image_format/
sha256), mouse_display (legacy "display"/"cursor_scheme" keys, no
threshold1/threshold2), and cursors/sound_scheme (no "bundled" key).

Covers:
  - Every one of those six modules' restore() completes without raising and
    returns a well-formed report ({"status", "items"}), given 0.2.0-shape
    data (D7's uniform compatibility rule: `.get(...)` defaults, never raise
    on absent keys).
  - Every field/aspect that is new in 0.3.0 (Taskband blob, pin-name list,
    accent palette, wallpaper style/tile/sha256, mouse acceleration
    thresholds, cursors/sound_scheme bundled-file maps) is reported
    "skipped" with a reason -- never "matched", never "failed" -- in both
    the restore and verify phases wherever the module exposes that aspect
    (Req 1.5, 9.3, 11.3, 14.2, 14.4).
  - A snapshot whose snapshot_format_version is a newer, unsupported major
    ("1.0.0") makes restore.py's real CLI entrypoint (`restore.main()`) exit
    2 before any module's restore() is invoked (Req 14.3).

**Validates: Requirements 1.5, 9.3, 11.3, 14.2, 14.3, 14.4**
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import (
    FakeWinReg,
    FakeUser32,
    _build_winreg_module,
    stage_v020_snapshot,
    make_winsnap_zip,
)

import modules.env_vars as env_vars
import modules.taskbar as taskbar
import modules.wallpaper as wallpaper
import modules.mouse_display as mouse_display
import modules.cursors as cursors
import modules.sound_scheme as sound_scheme
from modules import winutil

import restore as restore_module


# ---------------------------------------------------------------------------
# Small local helpers (self-contained -- conftest.py is not modified by this
# task; other Phase D tasks may be editing it in parallel).
# ---------------------------------------------------------------------------

def _windll_with(user32) -> SimpleNamespace:
    """Minimal ctypes.windll stand-in exposing only `.user32`."""
    return SimpleNamespace(user32=user32)


def _item(report: dict, name: str) -> dict:
    """Look up a single report item by name, failing loudly (with the full
    item list) if it isn't present -- easier to debug than a bare KeyError."""
    matches = [i for i in report["items"] if i["name"] == name]
    assert matches, f"no item named {name!r} in report items: {report['items']}"
    return matches[0]


def _assert_well_formed(report: dict) -> None:
    """Req 7.1: every restore()/verify() call returns a report dict shaped
    like modules/report.py's Report.finalize() output."""
    assert isinstance(report, dict)
    assert "status" in report
    assert "items" in report
    assert report["status"] in ("matched", "partial", "failed", "skipped")


def _assert_skipped(report: dict, name: str) -> None:
    """A named item must be reported skipped -- never matched, never failed
    -- and must carry a reason (Req 14.2, 14.4)."""
    item = _item(report, name)
    assert item["status"] == "skipped", (
        f"expected item {name!r} to be skipped, got {item['status']!r}: {item}"
    )


@pytest.fixture
def v020(tmp_path):
    """Stage the full 0.2.0-shape snapshot (Task 3 helper) on disk and
    return (snapshot_dict, snapshot_dir) for direct module-level restore()/
    verify() calls."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    snapshot = stage_v020_snapshot(snapshot_dir)
    return snapshot, snapshot_dir


# ===========================================================================
# env_vars: flat {name: {value, type}} map, no source_profile/vars wrapper
# ===========================================================================

class TestEnvVarsCompat:
    def _patch(self, monkeypatch):
        fake_reg = FakeWinReg()
        monkeypatch.setattr(env_vars, "winreg", _build_winreg_module(fake_reg))
        monkeypatch.setattr(env_vars.ctypes, "windll", _windll_with(FakeUser32()))
        return fake_reg

    def test_restore_completes_and_well_formed(self, monkeypatch, v020):
        snapshot, snapshot_dir = v020
        self._patch(monkeypatch)
        report = env_vars.restore(snapshot["modules"]["env_vars"], snapshot_dir)
        _assert_well_formed(report)

    def test_verify_completes_and_well_formed(self, monkeypatch, v020):
        snapshot, snapshot_dir = v020
        fake_reg = self._patch(monkeypatch)
        report = env_vars.verify(snapshot["modules"]["env_vars"], snapshot_dir)
        _assert_well_formed(report)
        assert fake_reg.writes == []  # verify is read-only


# ===========================================================================
# taskbar: no "pins" list, no "taskband" blob, theme without the accent trio
# ===========================================================================

class TestTaskbarCompat:
    def _patch(self, monkeypatch, tmp_path):
        fake_reg = FakeWinReg()
        monkeypatch.setattr(taskbar, "winreg", _build_winreg_module(fake_reg))
        # Orchestrated-mode gating (Req 1.3, 2.2, D2): avoid any real
        # taskkill/Popen call regardless of INLINE_EXPLORER_RESTART's default.
        monkeypatch.setattr(taskbar, "INLINE_EXPLORER_RESTART", False)
        monkeypatch.setattr(winutil, "restart_explorer", lambda: None)
        monkeypatch.setattr(taskbar, "TASKBAR_PINS_DIR", tmp_path / "live_pins")
        return fake_reg

    def test_restore_completes_and_skips_new_fields(self, monkeypatch, tmp_path, v020):
        snapshot, snapshot_dir = v020
        self._patch(monkeypatch, tmp_path)

        report = taskbar.restore(snapshot["modules"]["taskbar"], snapshot_dir)

        _assert_well_formed(report)
        # 0.2.0 has no "taskband" key at all -> pin-state portion skipped,
        # never a false success (Req 1.5).
        _assert_skipped(report, "taskband")
        assert "predates Taskband capture" in _item(report, "taskband")["detail"]
        # 0.2.0's theme dict has no "accent_palette" key -> accent portion
        # skipped, legacy DWM values still restored (Req 9.3).
        _assert_skipped(report, "accent_palette")
        assert "predates capture" in _item(report, "accent_palette")["detail"]
        # The .lnk file was still restored from the staged pins backup.
        assert (tmp_path / "live_pins" / "Notepad.lnk").exists()

    def test_verify_skips_new_fields(self, monkeypatch, tmp_path, v020):
        snapshot, snapshot_dir = v020
        fake_reg = self._patch(monkeypatch, tmp_path)
        data = snapshot["modules"]["taskbar"]

        report = taskbar.verify(data, snapshot_dir)

        _assert_well_formed(report)
        # 0.2.0 has no "pins" filename list -> skipped, not a mismatch (Req 1.6).
        _assert_skipped(report, "pins")
        _assert_skipped(report, "taskband")
        _assert_skipped(report, "accent_palette")
        assert fake_reg.writes == []  # verify is read-only


# ===========================================================================
# wallpaper: no style/tile/image_format/sha256
# ===========================================================================

class TestWallpaperCompat:
    def _patch(self, monkeypatch, tmp_path):
        fake_reg = FakeWinReg()
        monkeypatch.setattr(wallpaper, "winreg", _build_winreg_module(fake_reg))
        monkeypatch.setattr(winutil, "winreg", _build_winreg_module(fake_reg))
        fake_u32 = FakeUser32()
        monkeypatch.setattr(
            wallpaper, "ctypes", SimpleNamespace(windll=_windll_with(fake_u32))
        )
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        monkeypatch.setattr(wallpaper.Path, "home", staticmethod(lambda: home))
        return fake_reg, fake_u32

    def test_restore_completes_and_skips_style_tile(self, monkeypatch, tmp_path, v020):
        snapshot, snapshot_dir = v020
        self._patch(monkeypatch, tmp_path)

        report = wallpaper.restore(snapshot["modules"]["wallpaper"], snapshot_dir)

        _assert_well_formed(report)
        _assert_skipped(report, "style write")
        assert "predates style capture" in _item(report, "style write")["detail"]
        _assert_skipped(report, "tile write")
        assert "predates tile capture" in _item(report, "tile write")["detail"]

    def test_verify_skips_style_tile_and_content_hash(self, monkeypatch, tmp_path, v020):
        snapshot, snapshot_dir = v020
        fake_reg, _ = self._patch(monkeypatch, tmp_path)

        report = wallpaper.verify(snapshot["modules"]["wallpaper"], snapshot_dir)

        _assert_well_formed(report)
        _assert_skipped(report, "style")
        _assert_skipped(report, "tile")
        _assert_skipped(report, "wallpaper content")
        assert "predates sha256 capture" in _item(report, "wallpaper content")["detail"]
        assert fake_reg.writes == []  # verify is read-only


# ===========================================================================
# mouse_display: legacy "display"/"cursor_scheme" keys, no thresholds
# ===========================================================================

class TestMouseDisplayCompat:
    def _patch(self, monkeypatch):
        fake_reg = FakeWinReg()
        fake_u32 = FakeUser32()
        monkeypatch.setattr(mouse_display, "winreg", _build_winreg_module(fake_reg))
        mock_windll = MagicMock()
        mock_windll.user32 = fake_u32
        monkeypatch.setattr(mouse_display.ctypes, "windll", mock_windll)
        return fake_reg, fake_u32

    def test_restore_completes_and_skips_dpi(self, monkeypatch, v020):
        snapshot, snapshot_dir = v020
        fake_reg, _ = self._patch(monkeypatch)

        report = mouse_display.restore(
            snapshot["modules"]["mouse_display"], snapshot_dir
        )

        _assert_well_formed(report)
        # Legacy display/cursor_scheme keys are ignored without error and
        # reported as an explicit not-covered skip (Req 11.3).
        _assert_skipped(report, "dpi")
        assert _item(report, "dpi")["detail"] == "DPI not covered"
        # The dead LogPixels write must never occur.
        assert fake_reg.get_writes_for("LogPixels") == []

    def test_verify_skips_dpi_and_thresholds(self, monkeypatch, v020):
        snapshot, snapshot_dir = v020
        fake_reg, _ = self._patch(monkeypatch)

        report = mouse_display.verify(
            snapshot["modules"]["mouse_display"], snapshot_dir
        )

        _assert_well_formed(report)
        _assert_skipped(report, "dpi")
        # 0.2.0 never captured MouseThreshold1/2 -- verify must report them
        # skipped, never matched/failed (Req 14.2, 14.4).
        _assert_skipped(report, "mouse_threshold1")
        _assert_skipped(report, "mouse_threshold2")
        assert fake_reg.writes == []  # verify is read-only


# ===========================================================================
# cursors: no "bundled" key
# ===========================================================================

class TestCursorsCompat:
    def _patch(self, monkeypatch, tmp_path):
        fake_reg = FakeWinReg()
        monkeypatch.setattr(cursors, "winreg", _build_winreg_module(fake_reg))
        monkeypatch.setattr(
            cursors, "ctypes", SimpleNamespace(windll=_windll_with(FakeUser32()))
        )
        monkeypatch.setenv("SystemRoot", r"C:\Windows")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
        return fake_reg

    def test_restore_completes_and_skips_bundled(self, monkeypatch, tmp_path, v020):
        snapshot, snapshot_dir = v020
        self._patch(monkeypatch, tmp_path)

        report = cursors.restore(snapshot["modules"]["cursors"], snapshot_dir)

        _assert_well_formed(report)
        _assert_skipped(report, "bundled files")
        assert "predates bundling" in _item(report, "bundled files")["detail"]

    def test_verify_skips_bundled(self, monkeypatch, tmp_path, v020):
        snapshot, snapshot_dir = v020
        fake_reg = self._patch(monkeypatch, tmp_path)

        report = cursors.verify(snapshot["modules"]["cursors"], snapshot_dir)

        _assert_well_formed(report)
        _assert_skipped(report, "bundled files")
        assert fake_reg.writes == []  # verify is read-only


# ===========================================================================
# sound_scheme: no "bundled" key
# ===========================================================================

class TestSoundSchemeCompat:
    def _patch(self, monkeypatch, tmp_path):
        fake_reg = FakeWinReg()
        monkeypatch.setattr(sound_scheme, "winreg", _build_winreg_module(fake_reg))
        monkeypatch.setenv("SystemRoot", r"C:\Windows")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
        return fake_reg

    def test_restore_completes_and_skips_bundled(self, monkeypatch, tmp_path, v020):
        snapshot, snapshot_dir = v020
        self._patch(monkeypatch, tmp_path)

        report = sound_scheme.restore(snapshot["modules"]["sound_scheme"], snapshot_dir)

        _assert_well_formed(report)
        _assert_skipped(report, "bundled files")
        assert "predates bundling" in _item(report, "bundled files")["detail"]

    def test_verify_skips_bundled(self, monkeypatch, tmp_path, v020):
        snapshot, snapshot_dir = v020
        fake_reg = self._patch(monkeypatch, tmp_path)

        report = sound_scheme.verify(snapshot["modules"]["sound_scheme"], snapshot_dir)

        _assert_well_formed(report)
        _assert_skipped(report, "bundled files")
        assert fake_reg.writes == []  # verify is read-only


# ===========================================================================
# Newer-major-version refusal drives restore.main() itself (Req 14.3)
# ===========================================================================

class TestNewerMajorVersionRefusal:
    def test_1_0_0_snapshot_exits_2_before_any_module_runs(self, tmp_path, monkeypatch):
        call_log: list = []

        def _spy_restore(data, snapshot_dir):
            call_log.append("restore")
            return {"status": "matched", "items": []}

        stub = SimpleNamespace(restore=_spy_restore)
        monkeypatch.setattr(restore_module, "ALL_MODULES", [("stub_mod", stub)])

        zip_path = make_winsnap_zip(
            tmp_path, version="1.0.0", modules={"stub_mod": {"anything": True}}
        )
        monkeypatch.setattr(sys, "argv", ["restore.py", str(zip_path)])

        with pytest.raises(SystemExit) as exc_info:
            restore_module.main()

        assert exc_info.value.code == 2
        assert call_log == [], "no module's restore() may run before the version refusal"
