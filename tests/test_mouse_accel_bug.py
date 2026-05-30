"""
Bug Condition Exploration Test — Property 2: Pointer acceleration not applied.

**Validates: Requirements 1.2, 2.2**

This test encodes the EXPECTED (correct) behavior for mouse_display.restore when
enhance_precision is non-null. On the UNFIXED code it MUST FAIL, proving the bug
exists: MouseSpeed is never written and SPI_SETMOUSE is never called.

After the fix is applied, this same test will PASS, confirming the fix works.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import FakeWinReg, FakeUser32, _build_winreg_module


# ---------------------------------------------------------------------------
# Strategy: generate non-null enhance_precision values
# ---------------------------------------------------------------------------

# MouseSpeed in Windows is typically "0" or "1" (string), but we also test
# other string representations of integers to cover the full bug condition:
# X.enhance_precision IS NOT NULL
enhance_precision_strategy = st.sampled_from(["0", "1", "2"])


# Strategy for other mouse fields (may be None or string values)
optional_mouse_str = st.one_of(st.none(), st.sampled_from(["5", "10", "15", "20"]))


@st.composite
def mouse_snapshot_with_enhance_precision(draw):
    """
    Generate a snapshot dict where mouse.enhance_precision is always non-null.
    Other mouse fields may or may not be present.
    """
    enhance = draw(enhance_precision_strategy)
    speed = draw(optional_mouse_str)
    double_click = draw(optional_mouse_str)
    swap = draw(optional_mouse_str)
    scroll = draw(optional_mouse_str)

    snapshot = {
        "mouse": {
            "enhance_precision": enhance,
            "speed": speed,
            "double_click_speed": double_click,
            "swap_buttons": swap,
            "scroll_lines": scroll,
        },
        "keyboard": {},
        "display": {},
    }
    return snapshot


# ---------------------------------------------------------------------------
# Property Test
# ---------------------------------------------------------------------------

SPI_SETMOUSE = 0x0004


@given(snapshot=mouse_snapshot_with_enhance_precision())
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_mouse_restore_writes_mouse_speed_and_calls_spi_setmouse(snapshot, tmp_path):
    """
    Property 2 (Bug Condition): For any snapshot where enhance_precision is
    non-null, mouse_display.restore MUST:
      1. Write 'MouseSpeed' to the registry with value == enhance_precision
      2. Call SystemParametersInfoW with SPI_SETMOUSE (0x0004) and
         speed = int(enhance_precision)

    On UNFIXED code this test FAILS — proving the bug exists.

    **Validates: Requirements 1.2, 2.2**
    """
    # Set up fakes
    fake_reg = FakeWinReg()
    fake_u32 = FakeUser32()

    # Build a mock ctypes module that routes windll.user32 to our fake
    mock_ctypes = MagicMock()
    mock_ctypes.windll.user32.SendMessageTimeoutW = fake_u32.SendMessageTimeoutW
    mock_ctypes.windll.user32.SystemParametersInfoW = fake_u32.SystemParametersInfoW

    # Patch winreg and ctypes in the mouse_display module
    import modules.mouse_display as md

    fake_winreg_mod = _build_winreg_module(fake_reg)

    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir(exist_ok=True)

    with patch.object(md, "winreg", fake_winreg_mod), \
         patch.object(md, "ctypes", mock_ctypes):
        md.restore(snapshot, snapshot_dir)

    # --- Assertions (expected correct behavior) ---
    enhance_value = snapshot["mouse"]["enhance_precision"]

    # 1. MouseSpeed MUST be written to the registry
    mouse_speed_writes = fake_reg.get_writes_for("MouseSpeed")
    assert len(mouse_speed_writes) >= 1, (
        f"Expected MouseSpeed to be written to registry for "
        f"enhance_precision={enhance_value!r}, but no MouseSpeed write found. "
        f"All writes: {fake_reg.writes}"
    )
    # The written value must equal the snapshot's enhance_precision
    written_value = mouse_speed_writes[0][5]  # index 5 is the value
    assert written_value == enhance_value, (
        f"MouseSpeed written as {written_value!r}, expected {enhance_value!r}"
    )

    # 2. SPI_SETMOUSE (0x0004) MUST be called
    spi_setmouse_calls = fake_u32.get_spi_calls_for(SPI_SETMOUSE)
    assert len(spi_setmouse_calls) >= 1, (
        f"Expected SPI_SETMOUSE (0x0004) to be called for "
        f"enhance_precision={enhance_value!r}, but no such call found. "
        f"All SPI calls: {fake_u32.spi_calls}"
    )
