"""
conftest.py — Reusable fixtures that mock the OS boundaries for WinSnap tests.

Provides:
  - fake_winreg: record/replay registry reads and capture writes
  - fake_user32: capture SystemParametersInfoW / SendMessageTimeoutW / GetSystemMetrics calls
  - fake_subprocess: capture winget / taskkill / explorer invocations
  - fake_desktop_wallpaper: mock IDesktopWallpaper COM object
  - snapshot_dir: temporary directory pre-populated as a snapshot folder
  - read_winget_export: helper to load winget_export.json from a snapshot dir
"""

import json
import sys
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import Any

import pytest

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Registry mock (winreg)
# ---------------------------------------------------------------------------

@dataclass
class FakeRegistryKey:
    """Represents an open registry key handle."""
    hive: int
    path: str


@dataclass
class FakeWinReg:
    """
    Record/replay registry reads and capture writes.

    Usage:
        - Pre-populate `values` dict for reads:
            fake_winreg.values[(hive, path, name)] = (value, reg_type)
        - After restore, inspect `writes` list:
            Each entry is (hive, path, name, reserved, reg_type, value)
    """
    values: dict = field(default_factory=dict)
    writes: list = field(default_factory=list)
    _open_keys: list = field(default_factory=list)

    # Registry type constants (mirror winreg)
    HKEY_CURRENT_USER: int = 0x80000001
    HKEY_LOCAL_MACHINE: int = 0x80000002
    REG_SZ: int = 1
    REG_DWORD: int = 4
    KEY_SET_VALUE: int = 0x0002

    def OpenKey(self, hive, path, reserved=0, access=None):
        """Open a fake registry key. Always succeeds."""
        key = FakeRegistryKey(hive=hive, path=path)
        self._open_keys.append(key)
        return key

    def CloseKey(self, key):
        """No-op close."""
        pass

    def QueryValueEx(self, key, name):
        """Return a pre-populated value or raise OSError."""
        lookup = (key.hive, key.path, name)
        if lookup in self.values:
            val, reg_type = self.values[lookup]
            return val, reg_type
        raise OSError(f"Registry value not found: {lookup}")

    def SetValueEx(self, key, name, reserved, reg_type, value):
        """Record a registry write."""
        self.writes.append((key.hive, key.path, name, reserved, reg_type, value))

    def get_writes_for(self, name: str) -> list:
        """Helper: return all writes where the value name matches."""
        return [(h, p, n, r, t, v) for h, p, n, r, t, v in self.writes if n == name]


@pytest.fixture
def fake_winreg():
    """
    Fixture that patches `winreg` in the target modules with a FakeWinReg instance.

    Returns the FakeWinReg so tests can pre-populate values and inspect writes.
    """
    reg = FakeWinReg()
    return reg


def _apply_winreg_patch(monkeypatch, fake_reg, module):
    """
    Apply the fake_winreg to a specific module's winreg import.

    Call this in your test after getting the fake_winreg fixture:
        _apply_winreg_patch(monkeypatch, fake_reg, modules.mouse_display)
    """
    monkeypatch.setattr(module, "winreg", _build_winreg_module(fake_reg))


def _build_winreg_module(fake_reg: FakeWinReg):
    """Build a mock module object that delegates to FakeWinReg."""
    mock_mod = MagicMock()
    mock_mod.HKEY_CURRENT_USER = fake_reg.HKEY_CURRENT_USER
    mock_mod.HKEY_LOCAL_MACHINE = fake_reg.HKEY_LOCAL_MACHINE
    mock_mod.REG_SZ = fake_reg.REG_SZ
    mock_mod.REG_DWORD = fake_reg.REG_DWORD
    mock_mod.KEY_SET_VALUE = fake_reg.KEY_SET_VALUE
    mock_mod.OpenKey = fake_reg.OpenKey
    mock_mod.CloseKey = fake_reg.CloseKey
    mock_mod.QueryValueEx = fake_reg.QueryValueEx
    mock_mod.SetValueEx = fake_reg.SetValueEx
    return mock_mod


# ---------------------------------------------------------------------------
# ctypes.windll.user32 mock
# ---------------------------------------------------------------------------

@dataclass
class FakeUser32:
    """
    Captures SystemParametersInfoW, SendMessageTimeoutW, and GetSystemMetrics calls.

    Attributes:
        spi_calls: list of (action, uiParam, pvParam, fWinIni) tuples
        send_message_calls: list of (hWnd, Msg, wParam, lParam, flags, timeout, result) tuples
        metrics: dict mapping metric_index -> return value (e.g. {80: 2} for SM_CMONITORS=2)
    """
    spi_calls: list = field(default_factory=list)
    send_message_calls: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def SystemParametersInfoW(self, action, uiParam, pvParam, fWinIni):
        """Record a SystemParametersInfoW call. Returns 1 (success)."""
        self.spi_calls.append((action, uiParam, pvParam, fWinIni))
        return 1

    def SendMessageTimeoutW(self, hWnd, Msg, wParam, lParam, flags, timeout, result):
        """Record a SendMessageTimeoutW call. Returns 1 (success)."""
        self.send_message_calls.append((hWnd, Msg, wParam, lParam, flags, timeout, result))
        return 1

    def GetSystemMetrics(self, index):
        """Return a pre-configured metric value, defaulting to 1."""
        return self.metrics.get(index, 1)

    def get_spi_calls_for(self, action: int) -> list:
        """Helper: return all SPI calls matching a specific action code."""
        return [(a, u, p, f) for a, u, p, f in self.spi_calls if a == action]


@pytest.fixture
def fake_user32():
    """
    Fixture providing a FakeUser32 instance.

    Tests should patch ctypes.windll.user32 in the target module with this.
    """
    return FakeUser32()


# ---------------------------------------------------------------------------
# subprocess mock
# ---------------------------------------------------------------------------

@dataclass
class FakeSubprocessResult:
    """Mimics subprocess.CompletedProcess."""
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeSubprocess:
    """
    Captures subprocess.run and subprocess.Popen invocations.

    Attributes:
        run_calls: list of (args, kwargs) from subprocess.run calls
        popen_calls: list of (args, kwargs) from subprocess.Popen calls
        run_side_effect: optional callable(args, **kwargs) -> FakeSubprocessResult
    """
    run_calls: list = field(default_factory=list)
    popen_calls: list = field(default_factory=list)
    run_side_effect: Any = None

    def run(self, args, **kwargs):
        """Record a subprocess.run call and return a fake result."""
        self.run_calls.append((args, kwargs))
        if self.run_side_effect:
            return self.run_side_effect(args, **kwargs)
        return FakeSubprocessResult(returncode=0)

    def Popen(self, args, **kwargs):
        """Record a subprocess.Popen call and return a mock process."""
        self.popen_calls.append((args, kwargs))
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    def get_run_calls_for(self, executable: str) -> list:
        """Helper: return all run calls whose first arg matches the executable."""
        return [(a, kw) for a, kw in self.run_calls
                if a and a[0].lower() == executable.lower()]


@pytest.fixture
def fake_subprocess():
    """
    Fixture providing a FakeSubprocess instance.

    Tests should patch `subprocess` in the target module with this.
    """
    return FakeSubprocess()


# ---------------------------------------------------------------------------
# IDesktopWallpaper COM mock
# ---------------------------------------------------------------------------

@dataclass
class FakeDesktopWallpaper:
    """
    Mock for the IDesktopWallpaper COM interface.

    Attributes:
        monitor_count: number of monitors to report
        set_wallpaper_calls: list of (monitor_id, path) tuples
        monitor_ids: list of monitor device path strings
    """
    monitor_count: int = 1
    set_wallpaper_calls: list = field(default_factory=list)
    monitor_ids: list = field(default_factory=list)

    def __post_init__(self):
        if not self.monitor_ids:
            self.monitor_ids = [
                f"\\\\.\\DISPLAY{i+1}" for i in range(self.monitor_count)
            ]

    def GetMonitorDevicePathCount(self):
        """Return the number of monitors."""
        return self.monitor_count

    def GetMonitorDevicePathAt(self, index):
        """Return the monitor device path at the given index."""
        if 0 <= index < len(self.monitor_ids):
            return self.monitor_ids[index]
        raise IndexError(f"Monitor index {index} out of range")

    def SetWallpaper(self, monitor_id, path):
        """Record a per-monitor wallpaper set call."""
        self.set_wallpaper_calls.append((monitor_id, path))


@pytest.fixture
def fake_desktop_wallpaper():
    """
    Fixture providing a FakeDesktopWallpaper COM mock.

    Configure monitor_count before use:
        fake_desktop_wallpaper.monitor_count = 2
        fake_desktop_wallpaper.__post_init__()  # regenerate monitor_ids
    """
    return FakeDesktopWallpaper()


def make_fake_desktop_wallpaper(monitor_count: int) -> FakeDesktopWallpaper:
    """Factory helper to create a FakeDesktopWallpaper with a specific monitor count."""
    return FakeDesktopWallpaper(monitor_count=monitor_count)


# ---------------------------------------------------------------------------
# Snapshot directory helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def snapshot_dir(tmp_path):
    """
    Provides a temporary directory that acts as a snapshot folder.

    The directory is automatically cleaned up after the test.
    Returns a pathlib.Path to the temp directory.
    """
    snap = tmp_path / "snapshot"
    snap.mkdir()
    return snap


def populate_winget_export(snapshot_dir: Path, packages: list, schema: str = None):
    """
    Write a winget_export.json into the snapshot directory.

    Args:
        snapshot_dir: Path to the snapshot directory
        packages: list of package dicts (e.g. [{"PackageIdentifier": "Git.Git"}])
        schema: optional $schema URL to include (None = omit, matching the bug)
    """
    data = {
        "Sources": [
            {
                "SourceDetails": {
                    "Name": "winget",
                    "Identifier": "Microsoft.Winget.Source_8wekyb3d8bbwe",
                    "Argument": "https://cdn.winget.microsoft.com/cache",
                    "Type": "Microsoft.PreIndexed.Package",
                },
                "Packages": packages,
            }
        ]
    }
    if schema:
        data["$schema"] = schema
    out_file = snapshot_dir / "winget_export.json"
    out_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_winget_export(snapshot_dir: Path) -> dict:
    """
    Read and parse winget_export.json from a snapshot directory.

    Returns the parsed JSON dict, or raises FileNotFoundError if missing.
    """
    out_file = snapshot_dir / "winget_export.json"
    if not out_file.exists():
        raise FileNotFoundError(f"winget_export.json not found in {snapshot_dir}")
    return json.loads(out_file.read_text(encoding="utf-8"))


def stage_wallpaper_file(snapshot_dir: Path, filename: str = "wallpaper.jpg") -> Path:
    """
    Create a dummy wallpaper image file in the snapshot directory.

    Returns the path to the created file.
    """
    wp_file = snapshot_dir / filename
    # Write minimal content (tests don't need a real image)
    wp_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # JPEG-like header
    return wp_file


def stage_taskbar_pins(snapshot_dir: Path, lnk_names: list, include_desktop_ini: bool = False) -> Path:
    """
    Create a taskbar_pins backup folder in the snapshot directory.

    Args:
        snapshot_dir: Path to the snapshot directory
        lnk_names: list of .lnk filenames to create (e.g. ["Notepad.lnk", "Terminal.lnk"])
        include_desktop_ini: if True, also create a desktop.ini file

    Returns the path to the taskbar_pins folder.
    """
    pins_dir = snapshot_dir / "taskbar_pins"
    pins_dir.mkdir(exist_ok=True)

    for name in lnk_names:
        (pins_dir / name).write_bytes(b"\x4c\x00\x00\x00" + b"\x00" * 50)  # .lnk-like header

    if include_desktop_ini:
        ini_file = pins_dir / "desktop.ini"
        ini_file.write_text("[.ShellClassInfo]\nIconResource=imageres.dll,-1023\n", encoding="utf-8")

    return pins_dir
