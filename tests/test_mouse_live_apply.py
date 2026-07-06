"""
test_mouse_live_apply.py — Tests for the hardened modules/mouse_display.py.

Feature: backend-roundtrip-hardening, Task 8 (Req 11: remove fake DPI
coverage; Req 12: live application of mouse/keyboard settings).

Covers:
  - export() no longer captures "display" (LogPixels/DpiScaling) or
    "cursor_scheme", and now captures the real MouseThreshold1/2 (Req 11.1,
    11.2, 12.4).
  - restore() makes the matching SPI call (with SPIF_UPDATEINIFILE |
    SPIF_SENDCHANGE) after each registry write, per the design's SPI table
    (Req 12.1, 12.2, 12.3).
  - SPI_SETMOUSE always uses the captured MouseThreshold1/2 values -- never
    a hardcoded 6/10 -- falling back to (0, 0) only when a snapshot predates
    threshold capture (Req 12.4).
  - An SPI failure records a failed live-apply item ("logoff may be
    required") while the registry write itself still stands (Req 12.5).
  - Legacy "display"/"cursor_scheme" keys from old snapshots are ignored
    without error and reported as a skipped "DPI not covered" item, in both
    restore() and verify() (Req 11.3).
  - verify() re-reads the Mouse/Keyboard/Desktop registry values (and the
    live SPI_GETMOUSESPEED counterpart) against the snapshot, reporting
    matched/failed/skipped appropriately, never defaulting absent old
    fields to matched (Req 12.6, 14.2, 14.4).

**Validates: Requirements 11.1, 11.2, 11.3, 11.4, 12.1, 12.2, 12.3, 12.4,
12.5, 12.6, 14.2**
"""

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import FakeWinReg, FakeUser32, _build_winreg_module

import modules.mouse_display as md


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_ctypes(monkeypatch, user32_obj):
    """Patches only ctypes.windll (to route to `user32_obj`), leaving the
    real ctypes.c_int/ctypes.byref/array-construction in place so SPI_SETMOUSE's
    pvParam is a real, inspectable ctypes array rather than an opaque mock."""
    mock_windll = MagicMock()
    mock_windll.user32 = user32_obj
    monkeypatch.setattr(md.ctypes, "windll", mock_windll)


@dataclass
class _FailingActionUser32(FakeUser32):
    """FakeUser32 variant whose SystemParametersInfoW reports failure (0)
    for a chosen set of SPI action codes, success (1) for everything else."""
    failing_actions: frozenset = frozenset()

    def SystemParametersInfoW(self, action, uiParam, pvParam, fWinIni):
        self.spi_calls.append((action, uiParam, pvParam, fWinIni))
        return 0 if action in self.failing_actions else 1


@dataclass
class _GetMouseSpeedUser32(FakeUser32):
    """FakeUser32 variant that answers SPI_GETMOUSESPEED by writing a fixed
    value into the caller's pvParam pointer, mimicking the real API filling
    in an out-parameter."""
    live_speed: int = 0

    def SystemParametersInfoW(self, action, uiParam, pvParam, fWinIni):
        self.spi_calls.append((action, uiParam, pvParam, fWinIni))
        if action == md.SPI_GETMOUSESPEED:
            ctypes.cast(pvParam, ctypes.POINTER(ctypes.c_int))[0] = self.live_speed
        return 1


FULL_SNAPSHOT = {
    "mouse": {
        "speed": "12",
        "double_click_speed": "450",
        "swap_buttons": "0",
        "enhance_precision": "1",
        "scroll_lines": "3",
        "threshold1": "4",
        "threshold2": "9",
    },
    "keyboard": {
        "repeat_delay": "1",
        "repeat_speed": "28",
    },
}


# ---------------------------------------------------------------------------
# export(): DPI/cursor_scheme removed, thresholds captured (Req 11.1, 11.2, 12.4)
# ---------------------------------------------------------------------------

def test_export_does_not_capture_display_or_cursor_scheme(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseThreshold1")] = ("6", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseThreshold2")] = ("10", 1)
    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))

    result = md.export(tmp_path)

    assert "display" not in result
    assert "cursor_scheme" not in result
    assert result["mouse"]["threshold1"] == "6"
    assert result["mouse"]["threshold2"] == "10"


def test_export_captures_real_threshold_values(monkeypatch, tmp_path):
    """The captured thresholds must reflect whatever is actually in the
    registry, not a fixed pair -- proving there is no hardcoding on the
    export side either."""
    fake_reg = FakeWinReg()
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseThreshold1")] = (3, 4)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseThreshold2")] = (17, 4)
    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))

    result = md.export(tmp_path)

    assert result["mouse"]["threshold1"] == 3
    assert result["mouse"]["threshold2"] == 17


# ---------------------------------------------------------------------------
# restore(): SPI call table (Req 12.1, 12.2, 12.3)
# ---------------------------------------------------------------------------

def test_restore_calls_matching_spi_for_every_setting(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()
    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))
    _patch_ctypes(monkeypatch, fake_u32)

    report = md.restore(FULL_SNAPSHOT, tmp_path)

    apply_flags = md.SPIF_UPDATEINIFILE | md.SPIF_SENDCHANGE

    speed_calls = fake_u32.get_spi_calls_for(md.SPI_SETMOUSESPEED)
    assert len(speed_calls) == 1
    assert speed_calls[0][1] == 0                      # uiParam unused
    assert speed_calls[0][2] == 12                      # pvParam = speed
    assert speed_calls[0][3] == apply_flags

    dbl_calls = fake_u32.get_spi_calls_for(md.SPI_SETDOUBLECLICKTIME)
    assert len(dbl_calls) == 1
    assert dbl_calls[0][1] == 450                       # uiParam = ms
    assert dbl_calls[0][3] == apply_flags

    kb_delay_calls = fake_u32.get_spi_calls_for(md.SPI_SETKEYBOARDDELAY)
    assert len(kb_delay_calls) == 1
    assert kb_delay_calls[0][1] == 1
    assert kb_delay_calls[0][3] == apply_flags

    kb_speed_calls = fake_u32.get_spi_calls_for(md.SPI_SETKEYBOARDSPEED)
    assert len(kb_speed_calls) == 1
    assert kb_speed_calls[0][1] == 28
    assert kb_speed_calls[0][3] == apply_flags

    mouse_calls = fake_u32.get_spi_calls_for(md.SPI_SETMOUSE)
    assert len(mouse_calls) == 1
    assert mouse_calls[0][3] == apply_flags

    # Every setting produced a matched registry-write item.
    matched_names = {i["name"] for i in report["items"] if i["status"] == "matched"}
    assert {"mouse_speed", "double_click_speed", "swap_buttons", "scroll_lines",
            "mouse_acceleration", "keyboard_delay", "keyboard_speed"} <= matched_names
    assert report["status"] == "matched"


def test_restore_mouse_acceleration_uses_captured_thresholds_not_hardcoded(monkeypatch, tmp_path):
    """Regression for the removed 6/10 hardcode: SPI_SETMOUSE's pvParam must
    carry the snapshot's own threshold1/threshold2, which here are
    deliberately NOT 6/10, to prove no hardcoded fallback is in play."""
    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()
    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))
    _patch_ctypes(monkeypatch, fake_u32)

    snapshot = {
        "mouse": {"enhance_precision": "1", "threshold1": "4", "threshold2": "9"},
        "keyboard": {},
    }
    md.restore(snapshot, tmp_path)

    calls = fake_u32.get_spi_calls_for(md.SPI_SETMOUSE)
    assert len(calls) == 1
    pv_param = calls[0][2]
    assert list(pv_param) == [4, 9, 1]


@given(t1=st.integers(min_value=0, max_value=20), t2=st.integers(min_value=0, max_value=20),
       speed=st.sampled_from(["0", "1"]))
@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_mouse_acceleration_thresholds_always_propagated_verbatim(t1, t2, speed):
    """Property: for any captured threshold pair, SPI_SETMOUSE's pvParam
    always equals exactly (threshold1, threshold2, speed) -- never a fixed
    (6, 10, ...) regardless of what was actually captured."""
    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()

    mock_winreg = _build_winreg_module(fake_reg)
    mock_windll = MagicMock()
    mock_windll.user32 = fake_u32

    snapshot = {
        "mouse": {"enhance_precision": speed, "threshold1": t1, "threshold2": t2},
        "keyboard": {},
    }

    import unittest.mock as mock
    with mock.patch.object(md, "winreg", mock_winreg), \
         mock.patch.object(md.ctypes, "windll", mock_windll):
        md.restore(snapshot, Path("/fake/snapshot"))

    calls = fake_u32.get_spi_calls_for(md.SPI_SETMOUSE)
    assert len(calls) == 1
    assert list(calls[0][2]) == [t1, t2, int(speed)]


def test_mouse_acceleration_missing_thresholds_falls_back_to_zero_not_six_ten(monkeypatch, tmp_path):
    """When a snapshot predates threshold capture (Req 14.2), acceleration
    still applies live, but with (0, 0) -- not the removed 6/10 hardcode."""
    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()
    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))
    _patch_ctypes(monkeypatch, fake_u32)

    snapshot = {"mouse": {"enhance_precision": "1"}, "keyboard": {}}
    md.restore(snapshot, tmp_path)

    calls = fake_u32.get_spi_calls_for(md.SPI_SETMOUSE)
    assert len(calls) == 1
    assert list(calls[0][2]) == [0, 0, 1]


# ---------------------------------------------------------------------------
# restore(): SPI failure does not undo the registry write (Req 12.5)
# ---------------------------------------------------------------------------

def test_spi_failure_records_failed_item_but_registry_write_stands(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    fake_u32 = _FailingActionUser32(failing_actions=frozenset({md.SPI_SETMOUSESPEED}))
    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))
    _patch_ctypes(monkeypatch, fake_u32)

    snapshot = {"mouse": {"speed": "15"}, "keyboard": {}}
    report = md.restore(snapshot, tmp_path)

    # Registry write still happened.
    writes = fake_reg.get_writes_for("MouseSensitivity")
    assert len(writes) == 1
    assert writes[0][5] == "15"

    # The live-apply failure is recorded distinctly from the write.
    items_by_name = {i["name"]: i for i in report["items"]}
    assert items_by_name["mouse_speed"]["status"] == "matched"
    assert items_by_name["mouse_speed_live"]["status"] == "failed"
    assert "logoff may be required" in items_by_name["mouse_speed_live"]["detail"]

    # A failed item alongside other matched items rolls up to "partial".
    assert report["status"] == "partial"


# ---------------------------------------------------------------------------
# restore(): legacy display/cursor_scheme handling (Req 11.3)
# ---------------------------------------------------------------------------

def test_old_snapshot_with_display_and_cursor_scheme_restores_without_error(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()
    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))
    _patch_ctypes(monkeypatch, fake_u32)

    legacy_snapshot = {
        "mouse": {"speed": "10"},
        "keyboard": {},
        "display": {"log_pixels": 96, "dpi_scaling": None},
        "cursor_scheme": "Windows Default",
    }

    report = md.restore(legacy_snapshot, tmp_path)  # must not raise

    # LogPixels is never written -- the dead DPI write is gone.
    assert fake_reg.get_writes_for("LogPixels") == []

    dpi_items = [i for i in report["items"] if i["name"] == "dpi"]
    assert len(dpi_items) == 1
    assert dpi_items[0]["status"] == "skipped"
    assert dpi_items[0]["detail"] == "DPI not covered"


def test_fresh_export_has_no_legacy_fields_so_restore_adds_no_dpi_item(monkeypatch, tmp_path):
    """A snapshot produced by the new export() (no display/cursor_scheme
    keys at all) must not get a "dpi" item -- there is nothing to skip."""
    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()
    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))
    _patch_ctypes(monkeypatch, fake_u32)

    report = md.restore({"mouse": {"speed": "10"}, "keyboard": {}}, tmp_path)

    assert [i for i in report["items"] if i["name"] == "dpi"] == []


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------

def test_verify_reports_matched_when_registry_reflects_snapshot(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    fake_u32 = _GetMouseSpeedUser32(live_speed=12)
    mouse_path = r"Control Panel\Mouse"
    kb_path = r"Control Panel\Keyboard"
    desk_path = r"Control Panel\Desktop"

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "MouseSensitivity")] = ("12", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "DoubleClickSpeed")] = ("450", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "SwapMouseButtons")] = ("0", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, desk_path, "WheelScrollLines")] = ("3", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "MouseSpeed")] = ("1", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "MouseThreshold1")] = ("4", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "MouseThreshold2")] = ("9", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, kb_path, "KeyboardDelay")] = ("1", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, kb_path, "KeyboardSpeed")] = ("28", 1)

    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))
    _patch_ctypes(monkeypatch, fake_u32)

    report = md.verify(FULL_SNAPSHOT, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []   # verify() is read-only
    live_item = next(i for i in report["items"] if i["name"] == "mouse_speed_live")
    assert live_item["status"] == "matched"


def test_verify_reports_failed_on_mismatch(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()
    mouse_path = r"Control Panel\Mouse"

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "MouseSensitivity")] = ("99", 1)  # mismatch

    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))
    _patch_ctypes(monkeypatch, fake_u32)

    report = md.verify({"mouse": {"speed": "12"}, "keyboard": {}}, tmp_path)

    item = next(i for i in report["items"] if i["name"] == "mouse_speed")
    assert item["status"] == "failed"
    assert item["expected"] == "12"
    assert item["actual"] == "99"
    assert report["status"] == "failed"


def test_verify_skips_fields_absent_from_old_snapshot(monkeypatch, tmp_path):
    """A 0.2.0 snapshot has no threshold1/threshold2 -- verify must report
    them skipped, never matched or failed (Req 14.2, 14.4)."""
    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()
    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))
    _patch_ctypes(monkeypatch, fake_u32)

    old_snapshot = {
        "mouse": {"speed": "10"},  # no threshold1/threshold2
        "keyboard": {},
    }
    report = md.verify(old_snapshot, tmp_path)

    t1_item = next(i for i in report["items"] if i["name"] == "mouse_threshold1")
    t2_item = next(i for i in report["items"] if i["name"] == "mouse_threshold2")
    assert t1_item["status"] == "skipped"
    assert t2_item["status"] == "skipped"


def test_verify_reports_dpi_not_covered_for_legacy_snapshot(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()
    monkeypatch.setattr(md, "winreg", _build_winreg_module(fake_reg))
    _patch_ctypes(monkeypatch, fake_u32)

    legacy_snapshot = {
        "mouse": {},
        "keyboard": {},
        "display": {"log_pixels": 96},
        "cursor_scheme": "Windows Default",
    }
    report = md.verify(legacy_snapshot, tmp_path)

    dpi_item = next(i for i in report["items"] if i["name"] == "dpi")
    assert dpi_item["status"] == "skipped"
    assert dpi_item["detail"] == "DPI not covered"
    assert fake_reg.writes == []
