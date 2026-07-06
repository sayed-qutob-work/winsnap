"""
tests/test_env_rewrite.py — Tests for the hardened modules/env_vars.py.

Feature: backend-roundtrip-hardening, Task 4 (Req 4.1-4.5, 14.2, 14.4, 15.1;
Design D5, Process 3).

Covers:
  - rewrite_profile_paths(): hypothesis properties (idempotent, same-profile
    no-op, path-boundary safety) plus concrete boundary cases.
  - RESTORE_DENYLIST / _is_denylisted(): every denylisted name (and OneDrive
    variants) is skipped on restore with *no* registry write.
  - REG_SZ -> REG_EXPAND_SZ promotion when a value is actually rewritten.
  - PATH merge: existing target entries are preserved untouched; incoming
    entries are rewritten and entries whose target directory doesn't exist
    are dropped and recorded as skipped items.
  - 0.2.0 backward compatibility: a bare (unwrapped) vars map restores
    without KeyError/exception.
  - verify(): matched/failed/skipped classification and read-only behavior.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 14.2, 14.4, 15.1**
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import FakeWinReg, _build_winreg_module

from modules import env_vars
from modules.env_vars import (
    RESTORE_DENYLIST,
    _is_denylisted,
    rewrite_profile_paths,
)


# ===========================================================================
# rewrite_profile_paths(): concrete boundary cases
# ===========================================================================

def test_rewrite_replaces_prefix_followed_by_backslash():
    value, changed = rewrite_profile_paths(
        r"C:\Users\alice\AppData\Local\Temp", r"C:\Users\alice", r"C:\Users\bob")
    assert changed is True
    assert value == r"%USERPROFILE%\AppData\Local\Temp"


def test_rewrite_replaces_exact_match_end_of_string():
    value, changed = rewrite_profile_paths(
        r"C:\Users\alice", r"C:\Users\alice", r"C:\Users\bob")
    assert changed is True
    assert value == "%USERPROFILE%"


def test_rewrite_replaces_prefix_followed_by_semicolon():
    value, changed = rewrite_profile_paths(
        r"C:\Users\alice;C:\Windows", r"C:\Users\alice", r"C:\Users\bob")
    assert changed is True
    assert value.startswith("%USERPROFILE%;")


def test_rewrite_replaces_prefix_followed_by_quote():
    value, changed = rewrite_profile_paths(
        r'"C:\Users\alice"', r"C:\Users\alice", r"C:\Users\bob")
    assert changed is True
    assert value == '"%USERPROFILE%"'


def test_rewrite_is_boundary_safe_does_not_touch_similar_prefix():
    """C:\\Users\\alice2 must be untouched when the source profile is
    C:\\Users\\alice (Req 4.2 -- prefix match must respect path boundaries)."""
    value, changed = rewrite_profile_paths(
        r"C:\Users\alice2\Foo", r"C:\Users\alice", r"C:\Users\bob")
    assert changed is False
    assert value == r"C:\Users\alice2\Foo"


def test_rewrite_is_case_insensitive():
    value, changed = rewrite_profile_paths(
        r"c:\USERS\Alice\Docs", r"C:\Users\alice", r"C:\Users\bob")
    assert changed is True
    assert value == r"%USERPROFILE%\Docs"


def test_rewrite_no_op_when_source_equals_target():
    """Same-machine round trip: source and target profile are identical, so
    the value must come back byte-for-byte unchanged."""
    value, changed = rewrite_profile_paths(
        r"C:\Users\alice\Temp", r"C:\Users\alice", r"C:\Users\alice")
    assert changed is False
    assert value == r"C:\Users\alice\Temp"


def test_rewrite_no_op_when_source_equals_target_trailing_slash():
    value, changed = rewrite_profile_paths(
        r"C:\Users\alice\Temp", r"C:\Users\alice\\", r"C:\Users\alice")
    assert changed is False
    assert value == r"C:\Users\alice\Temp"


def test_rewrite_no_match_leaves_value_untouched():
    value, changed = rewrite_profile_paths(
        r"C:\Windows\System32", r"C:\Users\alice", r"C:\Users\bob")
    assert changed is False
    assert value == r"C:\Windows\System32"


def test_rewrite_empty_value_is_noop():
    value, changed = rewrite_profile_paths("", r"C:\Users\alice", r"C:\Users\bob")
    assert changed is False
    assert value == ""


# ===========================================================================
# rewrite_profile_paths(): hypothesis properties
# ===========================================================================

_name_segment = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"),
                           min_codepoint=48, max_codepoint=122),
    min_size=3, max_size=10,
)

_suffix_segment = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"),
                           min_codepoint=48, max_codepoint=122),
    min_size=0, max_size=15,
)


@st.composite
def _profile_and_value(draw):
    """Builds (source_profile, target_profile, value) where value is the
    source profile plus some subpath, guaranteed to actually match."""
    source_user = draw(_name_segment)
    target_user = draw(_name_segment)
    suffix = draw(_suffix_segment)

    source_profile = f"C:\\Users\\{source_user}"
    target_profile = f"C:\\Users\\{target_user}"
    value = source_profile + (f"\\{suffix}" if suffix else "")
    return source_profile, target_profile, value


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=_profile_and_value())
def test_rewrite_idempotent(data):
    """Applying the rewrite a second time to its own output must be a no-op:
    the rewritten value no longer contains the source profile text."""
    source_profile, target_profile, value = data
    once, _ = rewrite_profile_paths(value, source_profile, target_profile)
    twice, changed_again = rewrite_profile_paths(once, source_profile, target_profile)
    assert twice == once
    assert changed_again is False


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=_profile_and_value())
def test_rewrite_same_profile_is_always_noop(data):
    source_profile, _target_profile, value = data
    result, changed = rewrite_profile_paths(value, source_profile, source_profile)
    assert changed is False
    assert result == value


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=_profile_and_value())
def test_rewrite_removes_source_profile_when_changed(data):
    """Whenever a rewrite is reported as changed, the source profile text
    must no longer appear literally in the output."""
    source_profile, target_profile, value = data
    result, changed = rewrite_profile_paths(value, source_profile, target_profile)
    if changed:
        assert source_profile.lower() not in result.lower()


# ===========================================================================
# Fixtures / helpers for restore()/verify() tests
# ===========================================================================

def _restore_with_fake_reg(monkeypatch, snapshot, target_profile, tmp_path):
    """Patch env_vars.winreg with a fresh FakeWinReg and env_vars' os.environ
    USERPROFILE with `target_profile`, then call restore(). Returns
    (report, fake_reg)."""
    fake_reg = FakeWinReg()
    mock_winreg = _build_winreg_module(fake_reg)
    monkeypatch.setattr(env_vars, "winreg", mock_winreg)
    monkeypatch.setenv("USERPROFILE", str(target_profile))
    monkeypatch.setattr(env_vars.ctypes, "windll", _NullWindll())

    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir(exist_ok=True)
    report = env_vars.restore(snapshot, snapshot_dir)
    return report, fake_reg


class _NullWindll:
    """Swallows ctypes.windll.user32.SendMessageTimeoutW calls so restore()'s
    broadcast doesn't touch the real user32 during tests."""
    class user32:
        @staticmethod
        def SendMessageTimeoutW(*args, **kwargs):
            return 1


# ===========================================================================
# Denylist: no write ever occurs for denylisted names
# ===========================================================================

_ONEDRIVE_VARIANTS = ["OneDrive", "OneDriveConsumer", "OneDriveCommercial", "ONEDRIVE"]


@pytest.mark.parametrize("name", sorted(RESTORE_DENYLIST) + _ONEDRIVE_VARIANTS)
def test_denylisted_name_is_recognized(name):
    assert _is_denylisted(name) is True


def test_non_denylisted_name_is_not_denylisted():
    assert _is_denylisted("MY_CUSTOM_VAR") is False
    assert _is_denylisted("PATH") is False


@pytest.mark.parametrize("name", sorted(RESTORE_DENYLIST) + _ONEDRIVE_VARIANTS)
def test_denylisted_variable_restore_writes_nothing(monkeypatch, tmp_path, name):
    snapshot = {
        "source_profile": r"C:\Users\alice",
        "vars": {name: {"value": r"C:\Users\alice\SomePath", "type": 1}},
    }
    report, fake_reg = _restore_with_fake_reg(
        monkeypatch, snapshot, r"C:\Users\bob", tmp_path)

    assert fake_reg.writes == [], f"expected no write for denylisted {name!r}"
    assert report["status"] == "skipped"
    assert "denylist" in report["reason"]
    assert report["items"][0]["status"] == "skipped"
    assert "denylist" in report["items"][0]["detail"]


def test_multiple_denylisted_vars_all_skipped_no_writes(monkeypatch, tmp_path):
    snapshot = {
        "source_profile": r"C:\Users\alice",
        "vars": {
            "TEMP": {"value": r"C:\Users\alice\AppData\Local\Temp", "type": 1},
            "USERPROFILE": {"value": r"C:\Users\alice", "type": 1},
            "OneDriveConsumer": {"value": r"C:\Users\alice\OneDrive", "type": 1},
        },
    }
    report, fake_reg = _restore_with_fake_reg(
        monkeypatch, snapshot, r"C:\Users\bob", tmp_path)

    assert fake_reg.writes == []
    assert report["status"] == "skipped"


# ===========================================================================
# Profile rewrite + REG_SZ -> REG_EXPAND_SZ promotion
# ===========================================================================

def test_non_denylisted_var_is_rewritten_and_promoted_to_expand_sz(monkeypatch, tmp_path):
    snapshot = {
        "source_profile": r"C:\Users\alice",
        "vars": {
            "MYAPP_HOME": {"value": r"C:\Users\alice\Documents\MyApp", "type": 1},  # REG_SZ
        },
    }
    report, fake_reg = _restore_with_fake_reg(
        monkeypatch, snapshot, r"C:\Users\bob", tmp_path)

    writes = fake_reg.get_writes_for("MYAPP_HOME")
    assert len(writes) == 1
    _, _, _, _, reg_type, value = writes[0]
    assert reg_type == fake_reg.REG_EXPAND_SZ
    assert value == r"%USERPROFILE%\Documents\MyApp"
    assert report["status"] == "matched"


def test_var_already_expand_sz_stays_expand_sz_when_rewritten(monkeypatch, tmp_path):
    snapshot = {
        "source_profile": r"C:\Users\alice",
        "vars": {
            "MYAPP_HOME": {"value": r"C:\Users\alice\Documents\MyApp",
                           "type": 2},  # already REG_EXPAND_SZ
        },
    }
    report, fake_reg = _restore_with_fake_reg(
        monkeypatch, snapshot, r"C:\Users\bob", tmp_path)

    writes = fake_reg.get_writes_for("MYAPP_HOME")
    assert len(writes) == 1
    _, _, _, _, reg_type, value = writes[0]
    assert reg_type == fake_reg.REG_EXPAND_SZ
    assert value == r"%USERPROFILE%\Documents\MyApp"


def test_var_unaffected_by_source_profile_written_verbatim_unpromoted(monkeypatch, tmp_path):
    """A value with no source-profile prefix at all is written as-is, and its
    original REG_SZ type is left alone (no promotion when nothing changed)."""
    snapshot = {
        "source_profile": r"C:\Users\alice",
        "vars": {
            "MY_CONST": {"value": "some-constant-value", "type": 1},
        },
    }
    report, fake_reg = _restore_with_fake_reg(
        monkeypatch, snapshot, r"C:\Users\bob", tmp_path)

    writes = fake_reg.get_writes_for("MY_CONST")
    assert len(writes) == 1
    _, _, _, _, reg_type, value = writes[0]
    assert reg_type == fake_reg.REG_SZ
    assert value == "some-constant-value"


def test_same_machine_round_trip_is_byte_identical(monkeypatch, tmp_path):
    """When source_profile == target USERPROFILE, values must be written
    byte-identical (no rewrite, no promotion)."""
    snapshot = {
        "source_profile": r"C:\Users\alice",
        "vars": {
            "MYAPP_HOME": {"value": r"C:\Users\alice\Documents\MyApp", "type": 1},
        },
    }
    report, fake_reg = _restore_with_fake_reg(
        monkeypatch, snapshot, r"C:\Users\alice", tmp_path)

    writes = fake_reg.get_writes_for("MYAPP_HOME")
    assert len(writes) == 1
    _, _, _, _, reg_type, value = writes[0]
    assert reg_type == fake_reg.REG_SZ
    assert value == r"C:\Users\alice\Documents\MyApp"


# ===========================================================================
# PATH merge: preserve existing entries, rewrite/drop incoming entries
# ===========================================================================

def test_path_merge_preserves_existing_and_drops_missing_incoming(monkeypatch, tmp_path):
    target_profile_dir = tmp_path / "TargetUser"
    target_profile_dir.mkdir()
    (target_profile_dir / "Tools").mkdir()

    other_existing_dir = tmp_path / "OtherTool"
    other_existing_dir.mkdir()

    fake_reg = FakeWinReg()
    mock_winreg = _build_winreg_module(fake_reg)
    monkeypatch.setattr(env_vars, "winreg", mock_winreg)
    monkeypatch.setenv("USERPROFILE", str(target_profile_dir))
    monkeypatch.setattr(env_vars.ctypes, "windll", _NullWindll())

    # Seed the "existing" live PATH the target machine already has.
    existing_path_value = r"C:\Windows\System32;" + str(target_profile_dir / "AlreadyThere")
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, "Environment", "PATH")] = (
        existing_path_value, fake_reg.REG_SZ)

    incoming_path_value = ";".join([
        r"C:\Users\alice\Tools",              # rewrites + exists -> kept
        r"C:\Users\alice\Missing",             # rewrites but dir absent -> dropped
        str(other_existing_dir),               # untouched by rewrite, exists -> kept
    ])

    snapshot = {
        "source_profile": r"C:\Users\alice",
        "vars": {
            "PATH": {"value": incoming_path_value, "type": 1},
        },
    }

    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    report = env_vars.restore(snapshot, snapshot_dir)

    writes = fake_reg.get_writes_for("PATH")
    assert len(writes) == 1
    _, _, _, _, _reg_type, written_value = writes[0]
    entries = written_value.split(";")

    # Existing entries preserved, in original order, untouched.
    assert r"C:\Windows\System32" in entries
    assert str(target_profile_dir / "AlreadyThere") in entries

    # Kept incoming entries present (rewritten form for the profile-relative one).
    assert str(target_profile_dir / "Tools") in entries or \
        r"%USERPROFILE%\Tools" in entries
    assert str(other_existing_dir) in entries

    # Dropped entry must not appear anywhere in the merged value.
    assert "Missing" not in written_value

    # Dropped entry recorded as a skipped item with reason.
    skipped_details = [item["detail"] for item in report["items"]
                        if item["status"] == "skipped" and item["detail"]]
    assert any("directory missing" in d for d in skipped_details)
    assert any(r"C:\Users\alice\Missing" in d for d in skipped_details)

    assert report["status"] == "matched"


def test_path_merge_never_rewrites_or_drops_existing_target_entries(monkeypatch, tmp_path):
    """Existing target PATH entries must be preserved verbatim even if they
    look like they contain a "profile path" and even if their directory
    does not exist -- only *incoming* entries are rewritten/validated."""
    target_profile_dir = tmp_path / "TargetUser"
    target_profile_dir.mkdir()

    fake_reg = FakeWinReg()
    mock_winreg = _build_winreg_module(fake_reg)
    monkeypatch.setattr(env_vars, "winreg", mock_winreg)
    monkeypatch.setenv("USERPROFILE", str(target_profile_dir))
    monkeypatch.setattr(env_vars.ctypes, "windll", _NullWindll())

    # Existing entry that does not exist on disk and would match the source
    # profile prefix -- must survive unchanged because it's an existing
    # target entry, never touched by the rewrite/drop pre-passes.
    nonexistent_existing_entry = r"C:\Users\alice\NonExistentButExisting"
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, "Environment", "PATH")] = (
        nonexistent_existing_entry, fake_reg.REG_SZ)

    snapshot = {
        "source_profile": r"C:\Users\alice",
        "vars": {"PATH": {"value": "", "type": 1}},
    }

    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    env_vars.restore(snapshot, snapshot_dir)

    writes = fake_reg.get_writes_for("PATH")
    assert len(writes) == 1
    written_value = writes[0][5]
    assert nonexistent_existing_entry in written_value.split(";")


# ===========================================================================
# 0.2.0 backward compatibility (bare vars map, no wrapper)
# ===========================================================================

def test_020_flat_shape_restores_without_exception(monkeypatch, tmp_path):
    """A 0.2.0 snapshot has no 'source_profile'/'vars' wrapper -- the dict
    *is* the vars map. Restore must not raise (no KeyError) and must still
    write non-denylisted variables (Req 14.2)."""
    snapshot = {
        "TEMP": {"value": r"C:\Users\alice\AppData\Local\Temp", "type": 1},
        "MY_CUSTOM_VAR": {"value": "hello", "type": 1},
    }
    report, fake_reg = _restore_with_fake_reg(
        monkeypatch, snapshot, r"C:\Users\bob", tmp_path)

    # TEMP is denylisted regardless of shape.
    assert fake_reg.get_writes_for("TEMP") == []
    # MY_CUSTOM_VAR still gets written (verbatim -- no USERPROFILE captured
    # in this 0.2.0 fixture, so rewrite is underivable).
    writes = fake_reg.get_writes_for("MY_CUSTOM_VAR")
    assert len(writes) == 1
    assert writes[0][5] == "hello"
    # No exception was raised getting here; status reflects mixed skip/write.
    assert report["status"] in ("matched", "partial")


def test_020_flat_shape_with_userprofile_enables_rewrite(monkeypatch, tmp_path):
    """When a 0.2.0 snapshot's own USERPROFILE var is present, restore should
    recover source_profile from it and still perform the rewrite."""
    snapshot = {
        "USERPROFILE": {"value": r"C:\Users\alice", "type": 1},  # denylisted itself
        "MYAPP_HOME": {"value": r"C:\Users\alice\Documents\MyApp", "type": 1},
    }
    report, fake_reg = _restore_with_fake_reg(
        monkeypatch, snapshot, r"C:\Users\bob", tmp_path)

    writes = fake_reg.get_writes_for("MYAPP_HOME")
    assert len(writes) == 1
    _, _, _, _, reg_type, value = writes[0]
    assert value == r"%USERPROFILE%\Documents\MyApp"
    assert reg_type == fake_reg.REG_EXPAND_SZ


def test_empty_snapshot_restores_as_skipped(monkeypatch, tmp_path):
    report, fake_reg = _restore_with_fake_reg(monkeypatch, {}, r"C:\Users\bob", tmp_path)
    assert report["status"] == "skipped"
    assert fake_reg.writes == []


# ===========================================================================
# verify(): matched/failed/skipped + read-only invariant
# ===========================================================================

def test_verify_matched_when_live_value_equals_rewritten_expected(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    mock_winreg = _build_winreg_module(fake_reg)
    monkeypatch.setattr(env_vars, "winreg", mock_winreg)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\bob")

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, "Environment", "MYAPP_HOME")] = (
        r"%USERPROFILE%\Documents\MyApp", fake_reg.REG_EXPAND_SZ)

    data = {
        "source_profile": r"C:\Users\alice",
        "vars": {"MYAPP_HOME": {"value": r"C:\Users\alice\Documents\MyApp", "type": 1}},
    }
    report = env_vars.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []  # verify is read-only


def test_verify_failed_on_mismatch(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    mock_winreg = _build_winreg_module(fake_reg)
    monkeypatch.setattr(env_vars, "winreg", mock_winreg)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\bob")

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, "Environment", "MYAPP_HOME")] = (
        r"C:\Something\Else", fake_reg.REG_SZ)

    data = {
        "source_profile": r"C:\Users\alice",
        "vars": {"MYAPP_HOME": {"value": r"C:\Users\alice\Documents\MyApp", "type": 1}},
    }
    report = env_vars.verify(data, tmp_path)

    assert report["status"] == "failed"
    item = report["items"][0]
    assert item["status"] == "failed"
    assert item["expected"] == r"%USERPROFILE%\Documents\MyApp"
    assert item["actual"] == r"C:\Something\Else"
    assert fake_reg.writes == []


def test_verify_denylisted_var_is_skipped_not_mismatch(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    mock_winreg = _build_winreg_module(fake_reg)
    monkeypatch.setattr(env_vars, "winreg", mock_winreg)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\bob")

    # No live TEMP value populated at all -- would be a "missing" failure if
    # verify treated it as a normal variable, but denylisted vars must be
    # reported skipped regardless (Req 4.5).
    data = {
        "source_profile": r"C:\Users\alice",
        "vars": {"TEMP": {"value": r"C:\Users\alice\AppData\Local\Temp", "type": 1}},
    }
    report = env_vars.verify(data, tmp_path)

    assert report["status"] == "skipped"
    assert report["items"][0]["status"] == "skipped"
    assert fake_reg.writes == []


def test_verify_path_is_superset_check(monkeypatch, tmp_path):
    """Verify's PATH comparison passes as long as every kept incoming entry
    is present in live PATH -- the target may have additional entries of
    its own (not a strict equality check)."""
    tools_dir = tmp_path / "Tools"
    tools_dir.mkdir()

    fake_reg = FakeWinReg()
    mock_winreg = _build_winreg_module(fake_reg)
    monkeypatch.setattr(env_vars, "winreg", mock_winreg)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    live_path = f"C:\\Windows\\System32;{tools_dir};C:\\Target\\OwnEntry"
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, "Environment", "PATH")] = (
        live_path, fake_reg.REG_SZ)

    data = {
        "source_profile": r"C:\Users\alice",
        "vars": {"PATH": {"value": str(tools_dir), "type": 1}},
    }
    report = env_vars.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []
