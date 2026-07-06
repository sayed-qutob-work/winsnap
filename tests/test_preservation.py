"""
Property-based preservation tests for non-buggy inputs (Property 5).

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

Goal: Confirm that for all inputs where NONE of the four bug conditions hold,
apps.py/mouse_display.py/wallpaper.py/taskbar.py/startup.py produce specific
observable outputs. These tests capture the baseline behavior that must be
preserved after the fixes are applied.

Non-bug-condition inputs:
  - Winget: any package selection (the Packages/SourceDetails content is preserved)
  - Mouse: enhance_precision == None (other fields still written)
  - Wallpaper: monitor count <= 1 (legacy SPI_SETDESKWALLPAPER path)
  - Taskbar: pins backup with only .lnk files (no uncopyable files)
  - Startup: entries referencing missing binaries are skipped

Post-hardening note (backend-roundtrip-hardening, Task 17): these tests
originally asserted on printed stdout text (e.g. "No winget apps to
install.", "installed successfully") because that was how the pre-hardening
modules communicated outcomes. Req 7's reporting overhaul replaced ad hoc
prints with structured `Report` dicts (`restore()`/`verify()` now return
`{"status", "reason", "items": [...]}"`), and Req 11.1 removed the dead
LogPixels/DpiScaling "DPI restore" coverage entirely. The assertions below
have been updated to read the returned report (status/items/detail) instead
of stdout where the old print no longer exists, and the LogPixels
expectation has been replaced with an assertion that it is never written.
The underlying property under test (content/ordering preserved, non-bug-
condition fields still applied, missing-binary entries still skipped) is
unchanged; only the observation mechanism was updated to match the new
contract.
"""

import json
import os
import sys
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from io import StringIO

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.apps import _write_filtered_winget_export
from tests.conftest import (
    FakeWinReg,
    FakeUser32,
    FakeSubprocess,
    FakeSubprocessResult,
    _build_winreg_module,
)


# ===========================================================================
# Strategies
# ===========================================================================

# --- Winget package strategies ---
_package_id_segment = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"),
                           min_codepoint=65, max_codepoint=122),
    min_size=2,
    max_size=15,
)

_package_identifier = st.builds(
    lambda parts: ".".join(parts),
    st.lists(_package_id_segment, min_size=2, max_size=3),
)

_package_entry = st.builds(
    lambda pid: {"PackageIdentifier": pid},
    _package_identifier,
)

# Non-empty list of selected packages
_selected_packages = st.lists(_package_entry, min_size=1, max_size=8)

# --- Mouse field strategies (non-bug-condition: enhance_precision is None) ---
_mouse_speed = st.one_of(st.none(), st.sampled_from(["5", "10", "15", "20"]))
_double_click_speed = st.one_of(st.none(), st.sampled_from(["200", "400", "500", "900"]))
_swap_buttons = st.one_of(st.none(), st.sampled_from(["0", "1"]))
_scroll_lines = st.one_of(st.none(), st.sampled_from(["1", "3", "5"]))
_keyboard_delay = st.one_of(st.none(), st.sampled_from(["0", "1", "2", "3"]))
_keyboard_speed = st.one_of(st.none(), st.sampled_from(["0", "15", "31"]))
_log_pixels = st.one_of(st.none(), st.sampled_from([96, 120, 144]))

# --- Taskbar .lnk name strategies ---
_lnk_name = st.builds(
    lambda name: f"{name}.lnk",
    st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"),
                               min_codepoint=65, max_codepoint=122),
        min_size=3,
        max_size=15,
    ),
)

_lnk_names_list = st.lists(_lnk_name, min_size=1, max_size=6, unique=True)


# ===========================================================================
# Preservation Test 1: Winget package content preservation
# ===========================================================================

@given(selected=_selected_packages)
@settings(max_examples=50, deadline=5000,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_winget_package_content_preserved(tmp_path, selected):
    """
    Property 5 (Preservation): Winget package content and ordering preserved.

    **Validates: Requirements 3.3**

    For any non-empty set of selected winget packages, the written
    winget_export.json MUST contain:
      - Exactly the same Packages list (content and ordering)
      - The same SourceDetails structure

    On UNFIXED code this test PASSES because the package content is already
    correctly written (only $schema is missing, which is a separate bug).
    """
    snapshot_dir = tmp_path / f"snapshot_{os.urandom(8).hex()}"
    snapshot_dir.mkdir(exist_ok=True)

    # Act: write the filtered export
    _write_filtered_winget_export(snapshot_dir, selected)

    # Load the written file
    out_file = snapshot_dir / "winget_export.json"
    assert out_file.exists(), "winget_export.json was not written"
    data = json.loads(out_file.read_text(encoding="utf-8"))

    # Assert: Sources structure is present
    assert "Sources" in data, "Expected 'Sources' key in output"
    sources = data["Sources"]
    assert len(sources) == 1, f"Expected exactly 1 source, got {len(sources)}"

    # Assert: SourceDetails preserved
    source_details = sources[0].get("SourceDetails", {})
    assert source_details["Name"] == "winget"
    assert source_details["Identifier"] == "Microsoft.Winget.Source_8wekyb3d8bbwe"
    assert source_details["Argument"] == "https://cdn.winget.microsoft.com/cache"
    assert source_details["Type"] == "Microsoft.PreIndexed.Package"

    # Assert: Packages list is exactly the selected list (content + ordering)
    written_packages = sources[0].get("Packages", [])
    assert written_packages == selected, (
        f"Packages mismatch: expected {selected}, got {written_packages}"
    )


# ===========================================================================
# Preservation Test 2: Empty winget selection yields "No winget apps" path
# ===========================================================================

def test_winget_empty_selection_no_install_path(tmp_path, capsys, monkeypatch):
    """
    Property 5 (Preservation): Empty selection yields an empty, skipped report.

    **Validates: Requirements 3.3, 3.6**

    When the winget selection (and manual list) is empty, apps.restore installs
    nothing and no install-count message is printed; the returned report has
    no items and rolls up to "skipped" (Req 7's empty-report aggregation rule).
    """
    from modules import apps

    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(exist_ok=True)

    # No winget_export.json and empty winget list
    snapshot = {"winget": [], "manual": []}

    # winget must be "present" so we exercise the empty-selection path rather
    # than the winget-absent skip_all path.
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    # Mock subprocess so we don't actually call winget
    with patch.object(apps, "subprocess", MagicMock()):
        result = apps.restore(snapshot, snapshot_dir)

    captured = capsys.readouterr()
    assert "Installing" not in captured.out
    assert result["items"] == []
    assert result["status"] == "skipped"


# ===========================================================================
# Preservation Test 3: Mouse/keyboard/display fields written (no acceleration)
# ===========================================================================

@given(
    speed=_mouse_speed,
    dbl_click=_double_click_speed,
    swap=_swap_buttons,
    scroll=_scroll_lines,
    kb_delay=_keyboard_delay,
    kb_speed=_keyboard_speed,
    log_px=_log_pixels,
)
@settings(max_examples=50, deadline=5000,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_mouse_keyboard_display_preservation(
    tmp_path, speed, dbl_click, swap, scroll, kb_delay, kb_speed, log_px
):
    """
    Property 5 (Preservation): Other mouse/keyboard/display fields written identically.

    **Validates: Requirements 3.2**

    With enhance_precision == None (non-bug-condition), the exact set of registry
    writes for MouseSensitivity, DoubleClickSpeed, SwapMouseButtons, WheelScrollLines,
    KeyboardDelay, KeyboardSpeed and the WM_SETTINGCHANGE broadcast must occur.

    LogPixels is a separate case: Req 11.1 removed the dead DPI "restore" entirely
    (it was captured but never applied on modern Windows), so LogPixels must NEVER
    be written regardless of the legacy "display" key's content, and the module
    instead records the legacy "display" key as an explicitly skipped "dpi" item
    (Req 11.3) rather than presenting it as covered.
    """
    from modules import mouse_display

    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()

    snapshot = {
        "mouse": {
            "speed": speed,
            "double_click_speed": dbl_click,
            "swap_buttons": swap,
            "enhance_precision": None,  # Non-bug-condition
            "scroll_lines": scroll,
        },
        "keyboard": {
            "repeat_delay": kb_delay,
            "repeat_speed": kb_speed,
        },
        "display": {
            "log_pixels": log_px,
        },
    }

    # Patch winreg and ctypes in the module
    mock_winreg = _build_winreg_module(fake_reg)
    mock_ctypes = MagicMock()
    mock_ctypes.windll.user32 = fake_u32

    with patch.object(mouse_display, "winreg", mock_winreg), \
         patch.object(mouse_display, "ctypes", mock_ctypes):
        report = mouse_display.restore(snapshot, tmp_path)

    # Verify: each non-None field was written
    if speed is not None:
        writes = fake_reg.get_writes_for("MouseSensitivity")
        assert len(writes) == 1, "Expected MouseSensitivity write"
        assert writes[0][5] == speed
    else:
        assert len(fake_reg.get_writes_for("MouseSensitivity")) == 0

    if dbl_click is not None:
        writes = fake_reg.get_writes_for("DoubleClickSpeed")
        assert len(writes) == 1, "Expected DoubleClickSpeed write"
        assert writes[0][5] == dbl_click
    else:
        assert len(fake_reg.get_writes_for("DoubleClickSpeed")) == 0

    if swap is not None:
        writes = fake_reg.get_writes_for("SwapMouseButtons")
        assert len(writes) == 1, "Expected SwapMouseButtons write"
        assert writes[0][5] == swap
    else:
        assert len(fake_reg.get_writes_for("SwapMouseButtons")) == 0

    if scroll is not None:
        writes = fake_reg.get_writes_for("WheelScrollLines")
        assert len(writes) == 1, "Expected WheelScrollLines write"
        assert writes[0][5] == scroll
    else:
        assert len(fake_reg.get_writes_for("WheelScrollLines")) == 0

    if kb_delay is not None:
        writes = fake_reg.get_writes_for("KeyboardDelay")
        assert len(writes) == 1, "Expected KeyboardDelay write"
        assert writes[0][5] == kb_delay
    else:
        assert len(fake_reg.get_writes_for("KeyboardDelay")) == 0

    if kb_speed is not None:
        writes = fake_reg.get_writes_for("KeyboardSpeed")
        assert len(writes) == 1, "Expected KeyboardSpeed write"
        assert writes[0][5] == kb_speed
    else:
        assert len(fake_reg.get_writes_for("KeyboardSpeed")) == 0

    # Req 11.1: LogPixels is dead DPI coverage and must never be written,
    # regardless of what the legacy "display" key carries.
    assert len(fake_reg.get_writes_for("LogPixels")) == 0, (
        "LogPixels must never be written -- DPI restore was removed (Req 11.1)"
    )
    # Req 11.3: the legacy "display" key is reported as an explicit skipped
    # "dpi" item, not silently dropped or presented as covered.
    dpi_items = [i for i in report["items"] if i["name"] == "dpi"]
    assert len(dpi_items) == 1
    assert dpi_items[0]["status"] == "skipped"

    # Verify: WM_SETTINGCHANGE broadcast always happens
    assert len(fake_u32.send_message_calls) == 1, "Expected exactly one WM_SETTINGCHANGE broadcast"
    msg_call = fake_u32.send_message_calls[0]
    assert msg_call[0] == 0xFFFF  # HWND_BROADCAST
    assert msg_call[1] == 0x001A  # WM_SETTINGCHANGE

    # Verify: No MouseSpeed write (enhance_precision is None)
    assert len(fake_reg.get_writes_for("MouseSpeed")) == 0, (
        "MouseSpeed should NOT be written when enhance_precision is None"
    )

    # Verify: No SPI_SETMOUSE call (0x0004)
    spi_mouse_calls = fake_u32.get_spi_calls_for(0x0004)
    assert len(spi_mouse_calls) == 0, (
        "SPI_SETMOUSE should NOT be called when enhance_precision is None"
    )


# ===========================================================================
# Preservation Test 4: Single-monitor wallpaper uses legacy SPI path
# ===========================================================================

@given(monitor_count=st.integers(min_value=0, max_value=1))
@settings(max_examples=20, deadline=5000,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_single_monitor_wallpaper_legacy_path(tmp_path, monitor_count):
    """
    Property 5 (Preservation): Single-monitor wallpaper uses legacy SPI_SETDESKWALLPAPER.

    **Validates: Requirements 3.1**

    With monitor count <= 1, wallpaper.restore MUST use the legacy
    SystemParametersInfoW(SPI_SETDESKWALLPAPER) call. This is the existing
    behavior that must be preserved.
    """
    from modules import wallpaper

    fake_u32 = FakeUser32()
    fake_u32.metrics[80] = monitor_count  # SM_CMONITORS

    snapshot_dir = tmp_path / f"snapshot_{os.urandom(8).hex()}"
    snapshot_dir.mkdir(exist_ok=True)

    # Stage a wallpaper file
    wp_file = snapshot_dir / "wallpaper.jpg"
    wp_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    snapshot = {
        "enabled": True,
        "filename": "wallpaper.jpg",
        "original_path": "C:\\Users\\Test\\Pictures\\wall.jpg",
    }

    # Mock ctypes and shutil/Path.home so we don't touch the real filesystem
    mock_ctypes = MagicMock()
    mock_ctypes.windll.user32 = fake_u32

    fake_home = tmp_path / f"home_{os.urandom(8).hex()}"
    fake_home.mkdir(parents=True, exist_ok=True)
    pictures_dir = fake_home / "Pictures" / "WinSnap"
    pictures_dir.mkdir(parents=True, exist_ok=True)

    # Make SystemParametersInfoW return 1 (success)
    fake_u32.SystemParametersInfoW = lambda action, ui, pv, flags: (
        fake_u32.spi_calls.append((action, ui, pv, flags)) or 1
    )

    with patch.object(wallpaper, "ctypes", mock_ctypes), \
         patch.object(wallpaper.Path, "home", return_value=fake_home):
        wallpaper.restore(snapshot, snapshot_dir)

    # Assert: SPI_SETDESKWALLPAPER (0x0014) was called
    spi_wallpaper_calls = fake_u32.get_spi_calls_for(0x0014)
    assert len(spi_wallpaper_calls) == 1, (
        f"Expected exactly 1 SPI_SETDESKWALLPAPER call, got {len(spi_wallpaper_calls)}"
    )

    # Assert: The path argument points to the copied wallpaper
    call_args = spi_wallpaper_calls[0]
    assert "wallpaper.jpg" in call_args[2], (
        f"Expected wallpaper path in SPI call, got: {call_args[2]}"
    )


# ===========================================================================
# Preservation Test 5: Wallpaper disabled/missing-file guards short-circuit
# ===========================================================================

def test_wallpaper_disabled_guard(tmp_path, capsys):
    """
    Property 5 (Preservation): Disabled wallpaper guard short-circuits.

    **Validates: Requirements 3.1**

    When wallpaper is not enabled, restore does nothing and reports the whole
    category skipped with a reason (Req 7 Report contract; there is no print
    for this guard, only the returned report).
    """
    from modules import wallpaper

    snapshot = {"enabled": False}
    result = wallpaper.restore(snapshot, tmp_path)

    assert result["status"] == "skipped"
    assert "disabled" in result["reason"]
    assert result["items"] == []


def test_wallpaper_missing_file_guard(tmp_path, capsys):
    """
    Property 5 (Preservation): Missing wallpaper file guard short-circuits.

    **Validates: Requirements 3.1**

    When the wallpaper file doesn't exist in snapshot_dir, restore records a
    failed "file copy" item with the missing path (Req 7 Report contract;
    there is no print for this guard, only the returned report).
    """
    from modules import wallpaper

    snapshot = {
        "enabled": True,
        "filename": "nonexistent.jpg",
        "original_path": "C:\\somewhere\\wall.jpg",
    }

    result = wallpaper.restore(snapshot, tmp_path)

    items = {i["name"]: i for i in result["items"]}
    assert items["file copy"]["status"] == "failed"
    assert "missing from snapshot" in items["file copy"]["detail"]


# ===========================================================================
# Preservation Test 6: Manual app reporting preserved
# ===========================================================================

def test_manual_app_reporting_preserved(tmp_path, capsys, monkeypatch):
    """
    Property 5 (Preservation): Manual install list reported, never as a failure.

    **Validates: Requirements 3.3**

    When manual apps are present, apps.restore records each as a skipped
    report item carrying its name and URL (Req 7 Report contract; the old
    "manual installation" print no longer exists).
    """
    from modules import apps

    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(exist_ok=True)

    manual_apps = [
        {"name": "CustomApp", "urlinfoabout": "https://example.com/custom"},
        {"name": "AnotherApp", "urlinfoabout": "https://example.com/another"},
    ]

    snapshot = {"winget": [], "manual": manual_apps}
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    with patch.object(apps, "subprocess", MagicMock()):
        result = apps.restore(snapshot, snapshot_dir)

    items = {i["name"]: i for i in result["items"]}
    assert items["CustomApp"]["status"] == "skipped"
    assert "https://example.com/custom" in items["CustomApp"]["detail"]
    assert items["AnotherApp"]["status"] == "skipped"
    assert "https://example.com/another" in items["AnotherApp"]["detail"]


def test_manual_app_no_url_preserved(tmp_path, capsys, monkeypatch):
    """
    Property 5 (Preservation): Manual apps without URL show "no URL saved".

    **Validates: Requirements 3.3**
    """
    from modules import apps

    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(exist_ok=True)

    manual_apps = [{"name": "NoUrlApp"}]
    snapshot = {"winget": [], "manual": manual_apps}
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    with patch.object(apps, "subprocess", MagicMock()):
        result = apps.restore(snapshot, snapshot_dir)

    item = next(i for i in result["items"] if i["name"] == "NoUrlApp")
    assert item["status"] == "skipped"
    assert "no URL saved" in item["detail"]


# ===========================================================================
# Preservation Test 7: Taskbar normal pins restore (no uncopyable files)
# ===========================================================================

@given(lnk_names=_lnk_names_list)
@settings(max_examples=30, deadline=10000,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_taskbar_normal_pins_preservation(tmp_path, lnk_names):
    """
    Property 5 (Preservation): Pins backup with only .lnk files restores correctly.

    **Validates: Requirements 3.4**

    With a pins backup containing only .lnk files (no uncopyable files),
    the taskbar restore copies all shortcuts, writes theme settings, and
    restarts Explorer.
    """
    from modules import taskbar, winutil

    snapshot_dir = tmp_path / f"snapshot_{os.urandom(8).hex()}"
    snapshot_dir.mkdir(exist_ok=True)

    # Stage pins backup with only .lnk files
    pins_dir = snapshot_dir / "taskbar_pins"
    pins_dir.mkdir(exist_ok=True)
    for name in lnk_names:
        (pins_dir / name).write_bytes(b"\x4c\x00\x00\x00" + b"\x00" * 50)

    # Create a fake target directory for pins
    fake_pins_target = tmp_path / f"target_pins_{os.urandom(8).hex()}"
    fake_pins_target.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "pins_backup": "taskbar_pins",
        "theme": {
            "apps_light_theme": 0,
            "system_light_theme": 0,
            "accent_color": 0xFF0000,
            "colorization_color": 0xFF0000,
            "color_on_taskbar": 1,
            "transparency": 1,
        },
    }

    fake_reg = FakeWinReg()
    mock_winreg = _build_winreg_module(fake_reg)

    # Track whether winutil.restart_explorer was called
    explorer_restarted = []

    def mock_restart_explorer():
        explorer_restarted.append(True)

    with patch.object(taskbar, "winreg", mock_winreg), \
         patch.object(taskbar, "TASKBAR_PINS_DIR", fake_pins_target), \
         patch.object(winutil, "restart_explorer", mock_restart_explorer):
        taskbar.restore(snapshot, snapshot_dir)

    # Assert: All .lnk files were copied to the target
    restored_files = list(fake_pins_target.glob("*.lnk"))
    restored_names = sorted(f.name for f in restored_files)
    expected_names = sorted(lnk_names)
    assert restored_names == expected_names, (
        f"Expected pins {expected_names}, got {restored_names}"
    )

    # Assert: Theme settings were written to registry
    # The theme writes should include at least some of the theme values
    theme_writes = [w for w in fake_reg.writes if w[2] in (
        "AppsUseLightTheme", "SystemUsesLightTheme", "AccentColor",
        "ColorizationColor", "ColorPrevalence", "EnableTransparency"
    )]
    assert len(theme_writes) > 0, "Expected theme registry writes"

    # Assert: Explorer was restarted
    assert len(explorer_restarted) == 1, "Expected Explorer restart"


# ===========================================================================
# Preservation Test 8: Startup binary-not-found skip
# ===========================================================================

def test_startup_skips_missing_binary(tmp_path, capsys):
    """
    Property 5 (Preservation): Startup skips entries with missing binaries.

    **Validates: Requirements 3.5**

    When a startup registry entry references a binary that doesn't exist, the
    system skips it (Req 7 Report contract: a skipped report item carrying
    "binary not found: <command>" in its detail) rather than writing it or
    raising. This is expected behavior, not a defect.
    """
    from modules import startup

    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(exist_ok=True)

    # Create the startup_shortcuts directory (empty)
    (snapshot_dir / "startup_shortcuts").mkdir(exist_ok=True)

    snapshot = {
        "registry": {
            "Run": {
                "NonExistentApp": r"C:\NonExistent\Path\app.exe --arg",
                "AnotherMissing": r'"C:\Also\Missing\tool.exe" /start',
            },
            "RunOnce": {},
        },
        "shortcuts": [],
    }

    # Mock winreg so we don't touch real registry
    mock_winreg = MagicMock()
    mock_winreg.HKEY_CURRENT_USER = 0x80000001
    mock_winreg.KEY_SET_VALUE = 0x0002
    mock_winreg.REG_SZ = 1

    with patch.object(startup, "winreg", mock_winreg):
        result = startup.restore(snapshot, snapshot_dir)

    # Both entries should be skipped because their binaries don't exist.
    # Item names are namespaced "<Run-key label>:<value name>".
    items = {i["name"]: i for i in result["items"]}
    assert items["Run:NonExistentApp"]["status"] == "skipped"
    assert "binary not found" in items["Run:NonExistentApp"]["detail"]
    assert items["Run:AnotherMissing"]["status"] == "skipped"
    assert "binary not found" in items["Run:AnotherMissing"]["detail"]
    # No matched/failed items present -> the whole category rolls up skipped.
    assert result["status"] == "skipped"


# ===========================================================================
# Preservation Test 9: Winget success/failure reporting
# ===========================================================================

def test_winget_success_reporting(tmp_path, capsys, monkeypatch):
    """
    Property 5 (Preservation): Winget package reported matched on returncode == 0.

    **Validates: Requirements 3.6**

    Req 7 replaced the old "installed successfully" print with a report item;
    `result.stdout`/`result.stderr` must also be real strings (not absent
    attributes) since restore() reads them to classify the outcome (Req 3.3).
    """
    from modules import apps

    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(exist_ok=True)

    # Create a winget_export.json
    winget_file = snapshot_dir / "winget_export.json"
    winget_file.write_text(json.dumps({"Sources": [{"Packages": [{"PackageIdentifier": "Git.Git"}]}]}))

    snapshot = {"winget": [{"PackageIdentifier": "Git.Git"}], "manual": []}
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    fake_subproc = FakeSubprocess()
    # run returns success, with real stdout/stderr text (capture_output=True)
    fake_subproc.run_side_effect = lambda args, **kw: FakeSubprocessResult(
        returncode=0, stdout="Successfully installed", stderr="")

    mock_subprocess = MagicMock()
    mock_subprocess.run = fake_subproc.run

    with patch.object(apps, "subprocess", mock_subprocess):
        result = apps.restore(snapshot, snapshot_dir)

    item = next(i for i in result["items"] if i["name"] == "Git.Git")
    assert item["status"] == "matched"
    assert "installed" in item["detail"]
    assert result["status"] == "matched"


def test_winget_failure_reporting(tmp_path, capsys, monkeypatch):
    """
    Property 5 (Preservation): Winget package reported failed on non-zero returncode.

    **Validates: Requirements 3.6**

    Req 7 replaced the old "may have failed" print with a failed report item.
    """
    from modules import apps

    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(exist_ok=True)

    # Create a winget_export.json
    winget_file = snapshot_dir / "winget_export.json"
    winget_file.write_text(json.dumps({"Sources": [{"Packages": [{"PackageIdentifier": "Git.Git"}]}]}))

    snapshot = {"winget": [{"PackageIdentifier": "Git.Git"}], "manual": []}
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    mock_subprocess = MagicMock()
    mock_subprocess.run = MagicMock(return_value=FakeSubprocessResult(
        returncode=1, stdout="some other winget failure", stderr="boom"))

    with patch.object(apps, "subprocess", mock_subprocess):
        result = apps.restore(snapshot, snapshot_dir)

    item = next(i for i in result["items"] if i["name"] == "Git.Git")
    assert item["status"] == "failed"
    assert "returncode=1" in item["detail"]
    assert result["status"] == "failed"
