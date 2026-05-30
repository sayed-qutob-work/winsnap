"""
test_snapshot_naming.py — Unit tests for validate_snapshot_name and default_snapshot_name.

Validates: Requirements 2.3, 2.4, 2.7
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import validate_snapshot_name, default_snapshot_name


# ===========================================================================
# validate_snapshot_name
# ===========================================================================


class TestValidateSnapshotName:
    """Unit tests for validate_snapshot_name."""

    # --- Valid names (should return None) ---

    def test_simple_valid_name(self):
        assert validate_snapshot_name("my_snapshot") is None

    def test_valid_name_with_numbers(self):
        assert validate_snapshot_name("backup_2024") is None

    def test_valid_name_with_hyphens_and_underscores(self):
        assert validate_snapshot_name("my-snapshot_v2") is None

    def test_valid_name_max_length(self):
        name = "a" * 255
        assert validate_snapshot_name(name) is None

    def test_valid_name_with_spaces_in_middle(self):
        assert validate_snapshot_name("my snapshot") is None

    def test_valid_name_with_dot_in_middle(self):
        assert validate_snapshot_name("backup.v2") is None

    # --- Empty / whitespace-only (should reject) ---

    def test_empty_string_rejected(self):
        result = validate_snapshot_name("")
        assert result is not None
        assert "empty" in result.lower()

    def test_whitespace_only_rejected(self):
        result = validate_snapshot_name("   ")
        assert result is not None
        assert "empty" in result.lower()

    def test_tab_only_rejected(self):
        result = validate_snapshot_name("\t\t")
        assert result is not None
        assert "empty" in result.lower()

    # --- Length > 255 (should reject) ---

    def test_256_chars_rejected(self):
        name = "a" * 256
        result = validate_snapshot_name(name)
        assert result is not None
        assert "255" in result

    def test_very_long_name_rejected(self):
        name = "x" * 1000
        result = validate_snapshot_name(name)
        assert result is not None
        assert "255" in result

    # --- Windows-forbidden characters (should reject) ---

    @pytest.mark.parametrize("char", ["<", ">", ":", '"', "/", "\\", "|", "?", "*"])
    def test_forbidden_char_rejected(self, char):
        result = validate_snapshot_name(f"snap{char}shot")
        assert result is not None
        assert "forbidden" in result.lower()

    def test_control_char_rejected(self):
        result = validate_snapshot_name("snap\x01shot")
        assert result is not None
        assert "control character" in result.lower()

    def test_null_char_rejected(self):
        result = validate_snapshot_name("snap\x00shot")
        assert result is not None
        assert "control character" in result.lower()

    # --- Reserved device names (should reject) ---

    @pytest.mark.parametrize("name", [
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM9",
        "LPT1", "LPT2", "LPT9",
    ])
    def test_reserved_name_rejected(self, name):
        result = validate_snapshot_name(name)
        assert result is not None
        assert "reserved" in result.lower()

    @pytest.mark.parametrize("name", [
        "con", "prn", "aux", "nul",
        "com1", "lpt1",
        "Con", "Prn", "Aux", "Nul",
    ])
    def test_reserved_name_case_insensitive(self, name):
        result = validate_snapshot_name(name)
        assert result is not None
        assert "reserved" in result.lower()

    @pytest.mark.parametrize("name", [
        "CON.txt", "PRN.log", "NUL.dat",
        "COM1.sys", "LPT1.prn",
    ])
    def test_reserved_name_with_extension_rejected(self, name):
        result = validate_snapshot_name(name)
        assert result is not None
        assert "reserved" in result.lower()

    def test_reserved_name_as_prefix_is_valid(self):
        # "CONQUER" is not a reserved name
        assert validate_snapshot_name("CONQUER") is None

    def test_reserved_name_as_suffix_is_valid(self):
        # "myCON" is not a reserved name
        assert validate_snapshot_name("myCON") is None

    # --- Trailing dot or space (should reject) ---

    def test_trailing_dot_rejected(self):
        result = validate_snapshot_name("snapshot.")
        assert result is not None
        assert "dot" in result.lower()

    def test_trailing_space_rejected(self):
        result = validate_snapshot_name("snapshot ")
        assert result is not None
        assert "space" in result.lower()

    def test_multiple_trailing_dots_rejected(self):
        result = validate_snapshot_name("snapshot...")
        assert result is not None
        assert "dot" in result.lower()


# ===========================================================================
# default_snapshot_name
# ===========================================================================


class TestDefaultSnapshotName:
    """Unit tests for default_snapshot_name."""

    def test_basic_format(self):
        dt = datetime(2024, 1, 15, 9, 30, 45)
        result = default_snapshot_name(dt)
        assert result == "winsnap_20240115_093045"

    def test_midnight(self):
        dt = datetime(2024, 12, 31, 0, 0, 0)
        result = default_snapshot_name(dt)
        assert result == "winsnap_20241231_000000"

    def test_end_of_day(self):
        dt = datetime(2024, 6, 15, 23, 59, 59)
        result = default_snapshot_name(dt)
        assert result == "winsnap_20240615_235959"

    def test_prefix(self):
        dt = datetime(2024, 1, 1, 12, 0, 0)
        result = default_snapshot_name(dt)
        assert result.startswith("winsnap_")

    def test_format_pattern(self):
        import re
        dt = datetime(2024, 3, 5, 8, 7, 6)
        result = default_snapshot_name(dt)
        assert re.match(r"^winsnap_\d{8}_\d{6}$", result)
