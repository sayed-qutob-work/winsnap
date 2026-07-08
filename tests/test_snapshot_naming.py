"""
test_snapshot_naming.py — Unit tests for validate_snapshot_name and for the
snapshot-directory default naming that used to live in gui.default_snapshot_name.

gui.py's default_snapshot_name(start: datetime) -> str was removed by
gui-backend-alignment Task 3.6: its sole responsibility -- computing the
"winsnap_<timestamp>" folder name for an unnamed export -- now belongs
exclusively to export.create_snapshot_dir, which export.resolve_snapshot_dir
delegates to whenever no --name is given. Repointed by Task 7.5 to exercise
that replacement directly instead of importing the now-deleted function.
validate_snapshot_name (gui.py's snapshot-name input-field validator) is
unrelated to this refactor and its tests below are unchanged.

Validates: Requirements 2.3, 2.4, 2.7 (validate_snapshot_name);
gui-backend-alignment Requirements 9.4, 11.6 (snapshot directory naming)
"""

import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import export
from gui import validate_snapshot_name


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
# create_snapshot_dir / resolve_snapshot_dir (replaces default_snapshot_name)
# ===========================================================================


def _fixed_datetime(dt: datetime):
    """Build a datetime subclass whose .now() always returns `dt`, so
    export.create_snapshot_dir's timestamp can be pinned to a known value for
    exact-string assertions (mirrors tests/test_export_worker_adapters.py's
    _FixedDatetime, generalized to take the fixed value as a parameter)."""

    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt

    return _Fixed


class TestCreateSnapshotDir:
    """Unit tests for export.create_snapshot_dir, which now owns the
    "winsnap_<timestamp>" default-name format that gui.default_snapshot_name
    used to duplicate."""

    def test_basic_format(self, tmp_path, monkeypatch):
        monkeypatch.setattr(export, "datetime", _fixed_datetime(datetime(2024, 1, 15, 9, 30, 45)))
        result = export.create_snapshot_dir(tmp_path)
        assert result.name == "winsnap_20240115_093045"

    def test_midnight(self, tmp_path, monkeypatch):
        monkeypatch.setattr(export, "datetime", _fixed_datetime(datetime(2024, 12, 31, 0, 0, 0)))
        result = export.create_snapshot_dir(tmp_path)
        assert result.name == "winsnap_20241231_000000"

    def test_end_of_day(self, tmp_path, monkeypatch):
        monkeypatch.setattr(export, "datetime", _fixed_datetime(datetime(2024, 6, 15, 23, 59, 59)))
        result = export.create_snapshot_dir(tmp_path)
        assert result.name == "winsnap_20240615_235959"

    def test_prefix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(export, "datetime", _fixed_datetime(datetime(2024, 1, 1, 12, 0, 0)))
        result = export.create_snapshot_dir(tmp_path)
        assert result.name.startswith("winsnap_")

    def test_format_pattern(self, tmp_path, monkeypatch):
        monkeypatch.setattr(export, "datetime", _fixed_datetime(datetime(2024, 3, 5, 8, 7, 6)))
        result = export.create_snapshot_dir(tmp_path)
        assert re.match(r"^winsnap_\d{8}_\d{6}$", result.name)

    def test_directory_actually_created(self, tmp_path):
        # default_snapshot_name only ever returned a string; create_snapshot_dir
        # additionally creates the directory on disk (mkdir(parents=True,
        # exist_ok=True)), which callers now rely on directly.
        result = export.create_snapshot_dir(tmp_path)
        assert result.exists()
        assert result.is_dir()
        assert result.parent == tmp_path


class TestResolveSnapshotDirUnnamedMatchesCreateSnapshotDir:
    """resolve_snapshot_dir(output, name, force) is the single place a GUI or
    CLI export decides where an unnamed export writes to (Req 9.4); for a
    falsy `name` it must delegate to create_snapshot_dir and therefore
    produce the exact same "winsnap_<timestamp>" naming."""

    def test_none_name_uses_create_snapshot_dir_format(self, tmp_path, monkeypatch):
        monkeypatch.setattr(export, "datetime", _fixed_datetime(datetime(2024, 3, 5, 8, 7, 6)))
        result = export.resolve_snapshot_dir(tmp_path, None, force=False)
        assert result.name == "winsnap_20240305_080706"
        assert result.exists()

    def test_empty_string_name_uses_create_snapshot_dir_format(self, tmp_path, monkeypatch):
        # Empty string is falsy, same as None -- matches resolve_snapshot_dir's
        # `if name:` truthiness check.
        monkeypatch.setattr(export, "datetime", _fixed_datetime(datetime(2024, 3, 5, 8, 7, 6)))
        result = export.resolve_snapshot_dir(tmp_path, "", force=False)
        assert result.name == "winsnap_20240305_080706"
        assert result.exists()
