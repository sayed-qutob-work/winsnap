"""
conftest.py — Reusable fixtures that mock the OS boundaries for WinSnap tests.

Provides:
  - fake_winreg: record/replay registry reads and capture writes
  - fake_user32: capture SystemParametersInfoW / SendMessageTimeoutW / GetSystemMetrics calls
  - fake_subprocess: capture winget / taskkill / explorer invocations
  - snapshot_dir: temporary directory pre-populated as a snapshot folder
  - read_winget_export: helper to load winget_export.json from a snapshot dir
  - stage_snapshot_json / make_winsnap_zip / stage_v020_snapshot: build full
    snapshot.json / .winsnap archives for restore.py- and verify-level tests
"""

import json
import sys
import shutil
import tempfile
import zipfile
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
    REG_EXPAND_SZ: int = 2
    REG_BINARY: int = 3
    REG_DWORD: int = 4
    KEY_SET_VALUE: int = 0x0002

    def OpenKey(self, hive, path, reserved=0, access=None):
        """Open a fake registry key. Always succeeds."""
        key = FakeRegistryKey(hive=hive, path=path)
        self._open_keys.append(key)
        return key

    def CreateKey(self, hive, path):
        """
        Create (or open) a fake registry key. Always succeeds (no-op), mirroring
        winreg.CreateKey. Needed by code that writes REG_BINARY blobs (Taskband,
        Accent) or environment variables via CreateKey + OpenKey(KEY_SET_VALUE).
        """
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

    def EnumValue(self, key, index):
        """
        Replay pre-populated values for `key` (matched by hive+path) in
        insertion order, mirroring winreg.EnumValue(key, index) -> (name,
        value, type). Raises OSError once `index` is past the number of
        values recorded for this (hive, path), matching real winreg behavior
        at the end of enumeration.

        Needed for env_vars' HKCU\\Environment enumeration and verify-phase
        reads that don't know value names up front.
        """
        names = [name for (hive, path, name) in self.values
                 if hive == key.hive and path == key.path]
        if index >= len(names):
            raise OSError(f"No more values at index {index} for {key.path}")
        name = names[index]
        value, reg_type = self.values[(key.hive, key.path, name)]
        return name, value, reg_type

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
    mock_mod.REG_EXPAND_SZ = fake_reg.REG_EXPAND_SZ
    mock_mod.REG_BINARY = fake_reg.REG_BINARY
    mock_mod.REG_DWORD = fake_reg.REG_DWORD
    mock_mod.KEY_SET_VALUE = fake_reg.KEY_SET_VALUE
    mock_mod.OpenKey = fake_reg.OpenKey
    mock_mod.CreateKey = fake_reg.CreateKey
    mock_mod.CloseKey = fake_reg.CloseKey
    mock_mod.QueryValueEx = fake_reg.QueryValueEx
    mock_mod.EnumValue = fake_reg.EnumValue
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
    _scripts: list = field(default_factory=list)

    def run(self, args, **kwargs):
        """Record a subprocess.run call and return a fake result."""
        self.run_calls.append((args, kwargs))
        if self.run_side_effect:
            return self.run_side_effect(args, **kwargs)
        return FakeSubprocessResult(returncode=0)

    def script(self, matcher, result):
        """
        Register a scripted response for `run()`: when `matcher(args)` is
        truthy, the call returns `result` (a FakeSubprocessResult) instead of
        the default success. Matchers are tried in registration order — the
        first match wins — so calling `script()` repeatedly builds up a whole
        command sequence (e.g. a per-package winget install loop, or a
        powercfg import/setactive flow) without hand-writing a
        `run_side_effect` callable. Calls that match nothing fall back to
        `FakeSubprocessResult(returncode=0)`.

        Args:
            matcher: callable(args) -> bool, tested against the `args` list
                passed to `subprocess.run`.
            result: the FakeSubprocessResult to return on a match.
        """
        self._scripts.append((matcher, result))
        self.run_side_effect = self._dispatch_scripts

    def _dispatch_scripts(self, args, **kwargs):
        """run_side_effect installed by `script()`; picks the first matcher match."""
        for matcher, result in self._scripts:
            if matcher(args):
                return result
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


# ---------------------------------------------------------------------------
# Full-snapshot / archive staging helpers
#
# These build the top-level snapshot.json scaffolding (and, for
# make_winsnap_zip, a real .winsnap archive) that restore.py- and
# verify-level tests need, without every test hand-rolling the same
# boilerplate export.py already defines (winsnap_version, exported_at, etc).
# ---------------------------------------------------------------------------

def stage_snapshot_json(snapshot_dir: Path, version: str = "0.3.0",
                         modules: dict | None = None) -> Path:
    """
    Write a snapshot.json into `snapshot_dir` with the given format version
    and module data, filling in the standard top-level fields export.py
    writes (winsnap_version, snapshot_format_version, exported_at,
    exported_on, modules_attempted).

    Args:
        snapshot_dir: directory to write snapshot.json into (must exist).
        version: value for both winsnap_version and snapshot_format_version.
        modules: dict of {module_name: module_data}; defaults to {}.

    Returns the path to the written snapshot.json.
    """
    modules = modules or {}
    snapshot = {
        "winsnap_version": version,
        "snapshot_format_version": version,
        "exported_at": "2024-01-01T00:00:00",
        "exported_on": {"user": "testuser", "machine": "TEST-PC"},
        "modules_attempted": list(modules.keys()),
        "modules": modules,
    }
    json_path = snapshot_dir / "snapshot.json"
    json_path.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return json_path


def make_winsnap_zip(tmp_path: Path, *, folder_name: str = "winsnap_test",
                      version: str = "0.3.0", modules: dict | None = None,
                      member_names: list | None = None) -> Path:
    """
    Build a .winsnap archive under tmp_path, mimicking export.py's
    zip_snapshot() layout: a single top-level folder (`folder_name`)
    containing snapshot.json.

    If `member_names` is given, each name is written into the zip as an
    additional member with dummy content — this is how zip-slip tests inject
    hostile paths (e.g. "../evil.txt", an absolute path, or
    "winsnap_test/../../escaped.txt") alongside an otherwise well-formed
    archive, so restore.py's extraction guard has something real to reject.

    Returns the path to the created .winsnap file.
    """
    build_dir = tmp_path / f"_{folder_name}_build"
    build_dir.mkdir(exist_ok=True)
    snap_folder = build_dir / folder_name
    snap_folder.mkdir(exist_ok=True)
    stage_snapshot_json(snap_folder, version=version, modules=modules)

    zip_path = tmp_path / f"{folder_name}.winsnap"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in snap_folder.rglob("*"):
            zf.write(file, file.relative_to(build_dir))
        for bad_name in member_names or []:
            zf.writestr(bad_name, "hostile content")

    return zip_path


def stage_v020_snapshot(snapshot_dir: Path) -> dict:
    """
    Build a full snapshot dict in the pre-hardening 0.2.0 format: no Taskband
    blob/pins list, no accent-palette fields, no wallpaper style/tile/sha256,
    no mouse acceleration thresholds, and env_vars as a bare flat map (no
    "source_profile"/"vars" wrapper) — i.e. exactly what an old export
    produced, for backward-compatibility tests (Req 14).

    Also stages the on-disk files a restore would look for (wallpaper image,
    taskbar pins folder) so a restore driven by the returned snapshot doesn't
    fail on missing files unrelated to format compatibility, and writes
    snapshot.json into `snapshot_dir`.

    Returns the full snapshot dict (the same one written to snapshot.json).
    """
    wallpaper_file = stage_wallpaper_file(snapshot_dir, "wallpaper.jpg")
    stage_taskbar_pins(snapshot_dir, ["Notepad.lnk"])

    modules = {
        "env_vars": {
            # 0.2.0: bare {name: {value, type}} map, no wrapper
            "TEMP": {"value": r"C:\Users\alice\AppData\Local\Temp", "type": 1},
            "PATH": {"value": r"C:\Tools", "type": 1},
        },
        "taskbar": {
            # 0.2.0: pins_backup + theme only — no "pins", "taskband", or
            # accent fields on theme
            "pins_backup": "taskbar_pins",
            "theme": {
                "apps_light_theme": 1,
                "system_light_theme": 1,
                "accent_color": 4292311040,
                "colorization_color": 3298534883328,
                "color_on_taskbar": 0,
                "transparency": 1,
            },
        },
        "wallpaper": {
            # 0.2.0: no style/tile/image_format/sha256
            "enabled": True,
            "filename": wallpaper_file.name,
            "original_path": r"C:\Users\alice\AppData\Roaming\Microsoft\Windows\Themes\TranscodedWallpaper",
        },
        "mouse_display": {
            # 0.2.0: legacy "display" (LogPixels/DpiScaling) and
            # "cursor_scheme" still present; no threshold1/threshold2
            "mouse": {
                "speed": "10",
                "double_click_speed": "500",
                "swap_buttons": "0",
                "enhance_precision": "1",
                "scroll_lines": "3",
            },
            "keyboard": {
                "repeat_delay": "1",
                "repeat_speed": "31",
            },
            "display": {"log_pixels": 96, "dpi_scaling": None},
            "cursor_scheme": "Windows Default",
        },
        "cursors": {
            # 0.2.0: no "bundled" key
            "scheme": "Windows Default",
            "cursors": {},
        },
        "sound_scheme": {
            # 0.2.0: no "bundled" key
            "scheme": ".Default",
            "event_sounds": {},
            "beep": None,
        },
    }

    snapshot = {
        "winsnap_version": "0.2.0",
        "snapshot_format_version": "0.2.0",
        "exported_at": "2024-01-01T00:00:00",
        "exported_on": {"user": "alice", "machine": "ALICE-PC"},
        "modules_attempted": list(modules.keys()),
        "modules": modules,
    }

    (snapshot_dir / "snapshot.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return snapshot
