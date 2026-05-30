"""
Bug Condition Exploration Test — Property 3: Wallpaper glitched on multiple monitors.

**Validates: Requirements 1.3, 2.3**

This test encodes the EXPECTED (correct) behavior for wallpaper.restore when
the monitor count is greater than 1 and wallpaper is enabled. On the UNFIXED
code it MUST FAIL, proving the bug exists: only the legacy SPI_SETDESKWALLPAPER
API is called with no per-monitor IDesktopWallpaper handling.

After the fix is applied, this same test will PASS, confirming the fix works.

Bug Condition (from design):
    isBugCondition_wallpaper(X) = X.wallpaper.enabled AND monitorCount() > 1

Expected Behavior (Property 3):
    The per-monitor IDesktopWallpaper COM path is used, NOT only the legacy
    single-surface SPI_SETDESKWALLPAPER call.
"""

import sys
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import FakeUser32, make_fake_desktop_wallpaper, stage_wallpaper_file


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Monitor counts > 1 (the bug condition requires multi-monitor)
monitor_count_strategy = st.integers(min_value=2, max_value=4)

# Wallpaper filenames (always enabled for bug condition)
wallpaper_filename_strategy = st.sampled_from([
    "wallpaper.jpg",
    "wallpaper.png",
    "wallpaper.bmp",
])


@st.composite
def multimonitor_wallpaper_scenario(draw):
    """
    Generate a scenario where wallpaper is enabled and monitor count > 1.
    This satisfies the bug condition: X.wallpaper.enabled AND monitorCount() > 1
    """
    monitor_count = draw(monitor_count_strategy)
    filename = draw(wallpaper_filename_strategy)

    snapshot = {
        "enabled": True,
        "filename": filename,
        "original_path": f"C:\\Users\\Test\\Pictures\\{filename}",
    }

    return snapshot, monitor_count


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPI_SETDESKWALLPAPER = 0x0014
SM_CMONITORS = 80


# ---------------------------------------------------------------------------
# Property Test
# ---------------------------------------------------------------------------

@given(scenario=multimonitor_wallpaper_scenario())
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_wallpaper_restore_uses_per_monitor_path_on_multimonitor(scenario, tmp_path):
    """
    Property 3 (Bug Condition): For any restore environment where wallpaper is
    enabled and monitor count > 1, wallpaper.restore MUST:
      1. Use the per-monitor IDesktopWallpaper COM path
      2. NOT rely solely on the legacy SPI_SETDESKWALLPAPER call

    On UNFIXED code this test FAILS — the code always calls the legacy
    SPI_SETDESKWALLPAPER with no per-monitor handling, proving the bug exists.

    **Validates: Requirements 1.3, 2.3**
    """
    snapshot, monitor_count = scenario

    # Set up snapshot directory with a wallpaper file
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(exist_ok=True)
    stage_wallpaper_file(snapshot_dir, snapshot["filename"])

    # Set up fake user32 with the generated monitor count
    fake_u32 = FakeUser32()
    fake_u32.metrics[SM_CMONITORS] = monitor_count

    # Track calls to GetSystemMetrics and the COM path
    get_system_metrics_calls = []
    com_was_used = []

    def tracking_get_system_metrics(index):
        get_system_metrics_calls.append(index)
        return fake_u32.GetSystemMetrics(index)

    # Build mock ctypes that routes windll.user32 to our fake
    mock_ctypes = MagicMock()
    mock_ctypes.windll.user32.SystemParametersInfoW = fake_u32.SystemParametersInfoW
    mock_ctypes.windll.user32.GetSystemMetrics = tracking_get_system_metrics

    # We inject a mock comtypes module that the fixed code would import
    mock_comtypes = MagicMock()
    fake_com = make_fake_desktop_wallpaper(monitor_count)

    def track_cocreate(*args, **kwargs):
        com_was_used.append(True)
        return fake_com

    mock_comtypes.CoCreateInstance = track_cocreate

    # Mock Path.home to use tmp_path so shutil.copy2 works
    mock_home = tmp_path / "home"
    mock_home.mkdir(exist_ok=True)

    import modules.wallpaper as wp

    with patch.object(wp, "ctypes", mock_ctypes), \
         patch.object(wp.Path, "home", return_value=mock_home), \
         patch.dict(sys.modules, {"comtypes": mock_comtypes}):
        wp.restore(snapshot, snapshot_dir)

    # --- Assertions (expected correct behavior) ---

    # The per-monitor IDesktopWallpaper COM path MUST be used when monitors > 1
    # On unfixed code, this will be False because the code never uses COM
    legacy_spi_calls = fake_u32.get_spi_calls_for(SPI_SETDESKWALLPAPER)

    # Assert: The code must NOT rely solely on the legacy single-surface API
    # for multi-monitor setups. It should use the per-monitor COM path.
    #
    # On unfixed code: only legacy SPI_SETDESKWALLPAPER is called, no COM,
    # no GetSystemMetrics check — this assertion will FAIL.

    # Check 1: GetSystemMetrics must have been called to detect monitor count
    # The code must be monitor-count-aware (either checks metrics or uses COM)
    monitor_aware = (
        len(get_system_metrics_calls) > 0 or len(com_was_used) > 0
    )

    assert monitor_aware, (
        f"Bug confirmed: wallpaper.restore does not check monitor count. "
        f"On a {monitor_count}-monitor setup, the code should call "
        f"GetSystemMetrics(SM_CMONITORS) or use IDesktopWallpaper COM, "
        f"but neither was invoked. Only the legacy SPI_SETDESKWALLPAPER "
        f"path was used, which produces glitched results on multi-monitor. "
        f"Legacy SPI calls: {legacy_spi_calls}"
    )

    # Check 2: If monitor count > 1, the per-monitor COM path should be used
    # (not just the legacy single-surface API)
    assert len(com_was_used) > 0, (
        f"Bug confirmed: wallpaper.restore detected {monitor_count} monitors "
        f"but did not use the IDesktopWallpaper COM interface for per-monitor "
        f"wallpaper application. Legacy SPI calls: {legacy_spi_calls}"
    )
