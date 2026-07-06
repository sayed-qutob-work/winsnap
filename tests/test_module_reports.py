"""
tests/test_module_reports.py — Tests for the hardened report-returning
restore() + new verify() in modules/startup.py, modules/fonts.py,
modules/explorer.py, modules/desktop_icons.py, modules/region_lang.py.

Feature: backend-roundtrip-hardening, Task 11 (Req 2.3, 2.4, 7.1, 7.4, 7.6,
15.1; Design D1, Per-module table).

Covers:
  - startup.restore(): a Run entry skipped because its binary is missing
    carries the command path in the skipped item's detail (Req 2.4).
  - Each of the five modules' restore() returns a well-formed finalized
    report.Report dict (status/reason/items/explorer_restart_required, each
    item shaped name/status/detail/expected/actual).
  - Each of the five modules' verify() is read-only and reports matched on
    a live state identical to the snapshot, and failed/skipped when the
    live state diverges from or is absent relative to the snapshot.

**Validates: Requirements 2.3, 2.4, 7.1, 7.4, 7.6, 15.1**
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import FakeWinReg, _build_winreg_module

from modules import startup, fonts, explorer, desktop_icons, region_lang


# ===========================================================================
# Shared helpers
# ===========================================================================

class _NullWindll:
    """Swallows ctypes.windll.user32/gdi32 calls so restore() doesn't touch
    the real user32/gdi32 DLLs during tests."""

    class user32:
        @staticmethod
        def SendMessageTimeoutW(*args, **kwargs):
            return 1

    class gdi32:
        @staticmethod
        def AddFontResourceW(*args, **kwargs):
            return 1


_VALID_STATUSES = {"matched", "partial", "failed", "skipped"}
_VALID_ITEM_STATUSES = {"matched", "failed", "skipped"}


def _assert_well_formed_report(report: dict, phase: str) -> None:
    """Shared structural assertions for any module's finalized report dict."""
    assert isinstance(report, dict)
    assert report["status"] in _VALID_STATUSES
    if report["status"] == "skipped":
        assert report["reason"], "a skipped report must always carry a reason"
    assert isinstance(report["items"], list)
    for item in report["items"]:
        assert set(item.keys()) == {"name", "status", "detail", "expected", "actual"}
        assert item["status"] in _VALID_ITEM_STATUSES
        assert isinstance(item["name"], str) and item["name"]
    if phase == "restore":
        assert isinstance(report["explorer_restart_required"], bool)
    else:
        assert "explorer_restart_required" not in report


def _fake_reg(monkeypatch, module):
    fake_reg = FakeWinReg()
    monkeypatch.setattr(module, "winreg", _build_winreg_module(fake_reg))
    return fake_reg


# ===========================================================================
# startup.py
# ===========================================================================

_RUN_REG_PATH = dict(startup._RUN_PATHS)["Run"]


def test_startup_skipped_entry_carries_command_path(monkeypatch, tmp_path):
    """Req 2.4: a Run entry whose binary is missing must be recorded as a
    skipped item that includes the command path, not silently dropped."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    _fake_reg(monkeypatch, startup)

    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()

    missing_command = r"C:\NoSuchPlace\NoSuchApp.exe"
    snapshot = {"registry": {"Run": {"MyApp": missing_command}}, "shortcuts": []}

    report = startup.restore(snapshot, snapshot_dir)

    _assert_well_formed_report(report, phase="restore")
    assert report["status"] == "skipped"
    item = report["items"][0]
    assert item["status"] == "skipped"
    assert missing_command in item["detail"]


def test_startup_restore_well_formed_and_matched(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    fake_reg = _fake_reg(monkeypatch, startup)

    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()

    existing_binary = sys.executable  # guaranteed to exist on disk
    snapshot = {"registry": {"Run": {"MyApp": existing_binary}}, "shortcuts": []}

    report = startup.restore(snapshot, snapshot_dir)

    _assert_well_formed_report(report, phase="restore")
    assert report["status"] == "matched"
    assert fake_reg.get_writes_for("MyApp")


def test_startup_verify_matched_on_identical_state(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    fake_reg = _fake_reg(monkeypatch, startup)

    existing_binary = sys.executable
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, _RUN_REG_PATH, "MyApp")] = (
        existing_binary, fake_reg.REG_SZ)

    data = {"registry": {"Run": {"MyApp": existing_binary}}, "shortcuts": []}
    report = startup.verify(data, tmp_path)

    _assert_well_formed_report(report, phase="verify")
    assert report["status"] == "matched"
    assert fake_reg.writes == []  # verify is read-only


def test_startup_verify_failed_on_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    fake_reg = _fake_reg(monkeypatch, startup)

    existing_binary = sys.executable
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, _RUN_REG_PATH, "MyApp")] = (
        r"C:\Something\Else.exe", fake_reg.REG_SZ)

    data = {"registry": {"Run": {"MyApp": existing_binary}}, "shortcuts": []}
    report = startup.verify(data, tmp_path)

    assert report["status"] == "failed"
    item = report["items"][0]
    assert item["status"] == "failed"
    assert item["expected"] == existing_binary
    assert item["actual"] == r"C:\Something\Else.exe"
    assert fake_reg.writes == []


def test_startup_verify_shortcut_missing_is_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    _fake_reg(monkeypatch, startup)

    data = {"registry": {}, "shortcuts": [{"filename": "MyApp.lnk"}]}
    report = startup.verify(data, tmp_path)

    assert report["status"] == "failed"
    assert report["items"][0]["status"] == "failed"


# ===========================================================================
# fonts.py
# ===========================================================================

def test_fonts_restore_well_formed_and_matched(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    fake_reg = _fake_reg(monkeypatch, fonts)
    monkeypatch.setattr(fonts.ctypes, "windll", _NullWindll())

    snapshot_dir = tmp_path / "snap"
    bundle_dir = snapshot_dir / "fonts"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "MyFont.ttf").write_bytes(b"\x00" * 32)

    snapshot = {"fonts": [{"filename": "MyFont.ttf", "display_name": "My Font"}]}
    report = fonts.restore(snapshot, snapshot_dir)

    _assert_well_formed_report(report, phase="restore")
    assert report["status"] == "matched"
    assert fake_reg.get_writes_for("My Font")
    assert (fonts._user_fonts_dir() / "MyFont.ttf").exists()


def test_fonts_verify_matched_on_identical_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    fake_reg = _fake_reg(monkeypatch, fonts)

    target_dir = fonts._user_fonts_dir()
    target_dir.mkdir(parents=True)
    dst = target_dir / "MyFont.ttf"
    dst.write_bytes(b"\x00" * 32)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, fonts._FONTS_REG, "My Font")] = (
        str(dst), fake_reg.REG_SZ)

    data = {"fonts": [{"filename": "MyFont.ttf", "display_name": "My Font"}]}
    report = fonts.verify(data, tmp_path)

    _assert_well_formed_report(report, phase="verify")
    # Per the D1 aggregation rule, matched + skipped (no failed) aggregates to
    # "matched"; the live-load aspect is still listed as an explicit skipped item.
    assert report["status"] == "matched"
    matched_items = [i for i in report["items"] if i["status"] == "matched"]
    skipped_items = [i for i in report["items"] if i["status"] == "skipped"]
    assert matched_items and matched_items[0]["name"] == "MyFont.ttf"
    assert any("live font load" in i["detail"] for i in skipped_items)
    assert fake_reg.writes == []


def test_fonts_verify_failed_when_file_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    _fake_reg(monkeypatch, fonts)

    data = {"fonts": [{"filename": "MissingFont.ttf", "display_name": "Missing"}]}
    report = fonts.verify(data, tmp_path)

    failed_items = [i for i in report["items"] if i["status"] == "failed"]
    assert failed_items
    assert "file" in failed_items[0]["detail"]


# ===========================================================================
# explorer.py
# ===========================================================================

def test_explorer_restore_well_formed_and_matched(monkeypatch, tmp_path):
    fake_reg = _fake_reg(monkeypatch, explorer)
    monkeypatch.setattr(explorer.ctypes, "windll", _NullWindll())

    snapshot = {"Hidden": 1, "HideFileExt": 0}
    report = explorer.restore(snapshot, tmp_path)

    _assert_well_formed_report(report, phase="restore")
    assert report["status"] == "matched"
    assert report["explorer_restart_required"] is True
    assert fake_reg.get_writes_for("Hidden")
    assert fake_reg.get_writes_for("HideFileExt")


def test_explorer_verify_matched_on_identical_state(monkeypatch, tmp_path):
    fake_reg = _fake_reg(monkeypatch, explorer)

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, explorer._ADV_PATH, "Hidden")] = (
        1, fake_reg.REG_DWORD)

    data = {"Hidden": 1}
    report = explorer.verify(data, tmp_path)

    _assert_well_formed_report(report, phase="verify")
    assert report["status"] == "matched"
    assert fake_reg.writes == []


def test_explorer_verify_failed_on_mismatch(monkeypatch, tmp_path):
    fake_reg = _fake_reg(monkeypatch, explorer)

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, explorer._ADV_PATH, "Hidden")] = (
        2, fake_reg.REG_DWORD)

    data = {"Hidden": 1}
    report = explorer.verify(data, tmp_path)

    assert report["status"] == "failed"
    item = report["items"][0]
    assert item["expected"] == 1
    assert item["actual"] == 2
    assert fake_reg.writes == []


def test_explorer_verify_absent_snapshot_field_is_skipped(monkeypatch, tmp_path):
    _fake_reg(monkeypatch, explorer)
    report = explorer.verify({}, tmp_path)
    assert all(item["status"] == "skipped" for item in report["items"])
    assert report["status"] == "skipped"


# ===========================================================================
# desktop_icons.py
# ===========================================================================

def test_desktop_icons_restore_well_formed_and_matched(monkeypatch, tmp_path):
    fake_reg = _fake_reg(monkeypatch, desktop_icons)

    snapshot = {"this_pc": 0, "recycle_bin": 1}
    report = desktop_icons.restore(snapshot, tmp_path)

    _assert_well_formed_report(report, phase="restore")
    assert report["status"] == "matched"
    assert report["explorer_restart_required"] is True
    assert fake_reg.get_writes_for(desktop_icons._ICONS["this_pc"])
    assert fake_reg.get_writes_for(desktop_icons._ICONS["recycle_bin"])


def test_desktop_icons_verify_matched_on_identical_state(monkeypatch, tmp_path):
    fake_reg = _fake_reg(monkeypatch, desktop_icons)

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER,
                      desktop_icons._PATH,
                      desktop_icons._ICONS["this_pc"])] = (0, fake_reg.REG_DWORD)

    data = {"this_pc": 0}
    report = desktop_icons.verify(data, tmp_path)

    _assert_well_formed_report(report, phase="verify")
    assert report["status"] == "matched"
    assert fake_reg.writes == []


def test_desktop_icons_verify_missing_value_treated_as_zero_default(monkeypatch, tmp_path):
    """A CLSID with no registry value at all defaults to 0 (visible),
    matching export()'s own fill-in behavior."""
    _fake_reg(monkeypatch, desktop_icons)

    data = {"this_pc": 0}  # expects the default; no live value populated
    report = desktop_icons.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert report["items"][0]["actual"] == 0


def test_desktop_icons_verify_failed_on_mismatch(monkeypatch, tmp_path):
    fake_reg = _fake_reg(monkeypatch, desktop_icons)

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER,
                      desktop_icons._PATH,
                      desktop_icons._ICONS["recycle_bin"])] = (0, fake_reg.REG_DWORD)

    data = {"recycle_bin": 1}
    report = desktop_icons.verify(data, tmp_path)

    assert report["status"] == "failed"
    # Icons absent from the snapshot are reported as skipped items, so the
    # recycle_bin mismatch is not necessarily items[0]; select it by name.
    rb_item = next(i for i in report["items"] if i["name"] == "recycle_bin")
    assert rb_item["status"] == "failed"
    assert rb_item["expected"] == 1
    assert rb_item["actual"] == 0


# ===========================================================================
# region_lang.py
# ===========================================================================

def test_region_lang_restore_well_formed_and_matched(monkeypatch, tmp_path):
    fake_reg = _fake_reg(monkeypatch, region_lang)
    monkeypatch.setattr(region_lang.ctypes, "windll", _NullWindll())

    snapshot = {
        "international": {"sCountry": {"value": "United States", "type": 1}},
        "keyboard_layouts": {"1": {"value": "00000409", "type": 1}},
        "layout_substitutes": {},
    }
    report = region_lang.restore(snapshot, tmp_path)

    _assert_well_formed_report(report, phase="restore")
    assert report["status"] == "matched"
    assert fake_reg.get_writes_for("sCountry")
    assert fake_reg.get_writes_for("1")


def test_region_lang_verify_matched_on_identical_state(monkeypatch, tmp_path):
    fake_reg = _fake_reg(monkeypatch, region_lang)

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, region_lang._INTL_PATH, "sCountry")] = (
        "United States", fake_reg.REG_SZ)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, region_lang._LAYOUT_PRELOAD, "1")] = (
        "00000409", fake_reg.REG_SZ)

    data = {
        "international": {"sCountry": {"value": "United States", "type": 1}},
        "keyboard_layouts": {"1": {"value": "00000409", "type": 1}},
    }
    report = region_lang.verify(data, tmp_path)

    _assert_well_formed_report(report, phase="verify")
    assert report["status"] == "matched"
    assert fake_reg.writes == []


def test_region_lang_verify_failed_on_mismatch(monkeypatch, tmp_path):
    fake_reg = _fake_reg(monkeypatch, region_lang)

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, region_lang._INTL_PATH, "sCountry")] = (
        "France", fake_reg.REG_SZ)

    data = {"international": {"sCountry": {"value": "United States", "type": 1}}}
    report = region_lang.verify(data, tmp_path)

    assert report["status"] == "failed"
    item = report["items"][0]
    assert item["expected"] == "United States"
    assert item["actual"] == "France"
    assert fake_reg.writes == []


def test_region_lang_verify_empty_snapshot_is_skipped(monkeypatch, tmp_path):
    _fake_reg(monkeypatch, region_lang)
    report = region_lang.verify({}, tmp_path)
    assert report["status"] == "skipped"
    assert report["reason"]
