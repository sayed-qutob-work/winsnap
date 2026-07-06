"""
test_wallpaper_multimon_bug.py — post-fix contract for wallpaper.restore.

Feature: backend-roundtrip-hardening, Task 6 (Req 5.5/5.6: remove the dead
per-monitor IDesktopWallpaper COM path).

History: this file previously encoded the *old* expectation that a
multi-monitor restore drives a per-monitor `IDesktopWallpaper` COM interface.
That COM path was provably dead (comtypes.CoCreateInstance was handed a bare
GUID rather than an interface class, so it threw on every real machine) and
Req 5.5 chose option (b): delete it entirely and apply the single captured
image across all monitors via the legacy SystemParametersInfoW API. This test
now pins that corrected behavior:

  - `wallpaper` has no `_apply_wallpaper_per_monitor` symbol and imports no
    comtypes.
  - restore() drives SPI_SETDESKWALLPAPER exactly once regardless of monitor
    count, and never probes GetSystemMetrics(SM_CMONITORS) or CoCreateInstance.

**Validates: Requirements 5.5, 5.6**
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import FakeWinReg, _build_winreg_module

import modules.wallpaper as wp
from modules import winutil


SPI_SETDESKWALLPAPER = 0x0014
SM_CMONITORS = 80


# ---------------------------------------------------------------------------
# Fake ctypes.windll that records SPI calls and GetSystemMetrics probes
# ---------------------------------------------------------------------------

@dataclass
class _RecordingUser32:
    spi_calls: list = field(default_factory=list)
    get_metrics_calls: list = field(default_factory=list)
    spi_result: int = 1

    def SystemParametersInfoW(self, action, uiParam, pvParam, fWinIni):
        self.spi_calls.append((action, uiParam, pvParam, fWinIni))
        return self.spi_result

    def GetSystemMetrics(self, index):
        self.get_metrics_calls.append(index)
        return 1


class _RecordingWindll:
    def __init__(self, user32):
        self.user32 = user32


class _RecordingCtypes:
    def __init__(self, user32):
        self.windll = _RecordingWindll(user32)


def _stage(tmp_path, filename="wallpaper.jpg"):
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(exist_ok=True)
    (snapshot_dir / filename).write_bytes(b"\xff\xd8\xff" + b"\x00" * 64)
    return snapshot_dir


def _patch_restore_env(monkeypatch, tmp_path, user32):
    """Route wallpaper's ctypes at the SystemParametersInfoW boundary, its
    style/tile writes at a fake registry, and Path.home at tmp_path."""
    monkeypatch.setattr(wp, "ctypes", _RecordingCtypes(user32))
    monkeypatch.setattr(winutil, "winreg", _build_winreg_module(FakeWinReg()))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(wp.Path, "home", staticmethod(lambda: home))


# ---------------------------------------------------------------------------
# Structural assertions: no COM surface remains
# ---------------------------------------------------------------------------

def test_no_per_monitor_com_symbol():
    assert not hasattr(wp, "_apply_wallpaper_per_monitor")


def test_module_source_imports_no_comtypes():
    source = Path(wp.__file__).read_text(encoding="utf-8")
    # The only permitted mention is prose in the module docstring explaining
    # the removal; there must be no actual import statement.
    assert "import comtypes" not in source
    assert "from comtypes" not in source


# ---------------------------------------------------------------------------
# Behavioral: legacy SPI path is used regardless of monitor count
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("monitor_count", [1, 2, 3, 4])
def test_restore_uses_legacy_spi_regardless_of_monitor_count(monkeypatch, tmp_path, monitor_count):
    snapshot_dir = _stage(tmp_path)
    user32 = _RecordingUser32()
    user32.get_metrics_calls.append  # no-op; presence just documents intent
    _patch_restore_env(monkeypatch, tmp_path, user32)

    snapshot = {"enabled": True, "filename": "wallpaper.jpg",
                "original_path": r"C:\Users\Test\Pictures\wallpaper.jpg",
                "style": "10", "tile": "0", "image_format": "jpg",
                "sha256": "deadbeef"}

    report = wp.restore(snapshot, snapshot_dir)

    # Exactly one legacy apply call, targeting SPI_SETDESKWALLPAPER.
    spi_actions = [c[0] for c in user32.spi_calls]
    assert spi_actions == [SPI_SETDESKWALLPAPER]
    # The module never probes the monitor count; it applies one image to all.
    assert SM_CMONITORS not in user32.get_metrics_calls
    # And the report records the applied SPI item as matched.
    spi_items = [i for i in report["items"] if i["name"] == "SPI apply"]
    assert spi_items and spi_items[0]["status"] == "matched"
