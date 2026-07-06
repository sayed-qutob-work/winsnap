"""
test_power_flow.py — Unit tests for modules/power.py's restore/verify flow.

Feature: backend-roundtrip-hardening, Task 7 (Req 6, design "power restore
flow").

Covers:
  - restore() refuses to touch powercfg at all when not elevated (Req 6.1).
  - import-ok: `powercfg /import <file> <guid>` succeeds -> that GUID is
    activated directly.
  - guid-exists: the import fails, but the GUID is already present in
    `powercfg /list` -> treated as success, existing plan activated
    (Req 6.3).
  - reimport-new-guid: the import fails and the GUID is not present in
    `/list` -> retried without a destination GUID; the new GUID is parsed
    from the retry's successful stdout and activated.
  - all-fail: both import attempts fail -> failed report with both
    commands' stdout/stderr captured, and `/setactive` is never called
    (Req 6.4).
  - Regression: a failed import whose stdout happens to contain a
    GUID-looking string must NOT be activated -- this was the dead
    "parse GUID from failed import" branch that Req 6.4 requires removed.
  - verify(): non-elevated -> skipped; elevated -> `/getactivescheme`
    compared against the snapshot's plan, matched/failed; verify never
    writes to the registry or invokes /import|/setactive (read-only,
    Req 6.5).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from modules import power
from tests.conftest import FakeSubprocessResult


SNAPSHOT = {
    "enabled": True,
    "guid": "11111111-1111-1111-1111-111111111111",
    "name": "My Custom Plan",
    "filename": "power_plan.pow",
}


def _stage_pow_file(snapshot_dir: Path, filename: str = "power_plan.pow") -> Path:
    pow_file = snapshot_dir / filename
    pow_file.write_bytes(b"fake .pow contents")
    return pow_file


def _install_admin(monkeypatch, admin: bool):
    monkeypatch.setattr(power.winutil, "is_admin", lambda: admin)


def _install_subprocess(monkeypatch, fake_subprocess):
    monkeypatch.setattr(power, "subprocess", fake_subprocess)


# ---------------------------------------------------------------------------
# restore() — elevation gate
# ---------------------------------------------------------------------------

def test_restore_non_admin_skips_before_any_powercfg_call(
        monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, False)
    _install_subprocess(monkeypatch, fake_subprocess)
    _stage_pow_file(snapshot_dir)

    result = power.restore(SNAPSHOT, snapshot_dir)

    assert result["status"] == "skipped"
    assert "elevation" in result["reason"].lower()
    # No powercfg (or any) subprocess call was made.
    assert fake_subprocess.run_calls == []


def test_restore_nothing_to_restore_when_not_enabled(
        monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, True)
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.restore({"enabled": False}, snapshot_dir)

    assert result["status"] == "skipped"
    assert fake_subprocess.run_calls == []


# ---------------------------------------------------------------------------
# restore() — import-ok path
# ---------------------------------------------------------------------------

def test_restore_import_ok_activates_original_guid(
        monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, True)
    _stage_pow_file(snapshot_dir)

    def matcher_import(args):
        return args[:2] == ["powercfg", "/import"] and SNAPSHOT["guid"] in args

    def matcher_setactive(args):
        return args[:2] == ["powercfg", "/setactive"]

    fake_subprocess.script(matcher_import, FakeSubprocessResult(returncode=0, stdout="Import successful."))
    fake_subprocess.script(matcher_setactive, FakeSubprocessResult(returncode=0))
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.restore(SNAPSHOT, snapshot_dir)

    assert result["status"] == "matched"
    setactive_calls = [a for a, kw in fake_subprocess.run_calls if a[:2] == ["powercfg", "/setactive"]]
    assert setactive_calls == [["powercfg", "/setactive", SNAPSHOT["guid"]]]
    # /list must never have been consulted -- the first import succeeded.
    list_calls = [a for a, kw in fake_subprocess.run_calls if a[:2] == ["powercfg", "/list"]]
    assert list_calls == []


# ---------------------------------------------------------------------------
# restore() — guid-already-exists path
# ---------------------------------------------------------------------------

def test_restore_guid_already_present_activates_existing(
        monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, True)
    _stage_pow_file(snapshot_dir)
    guid = SNAPSHOT["guid"]

    def matcher_import_with_guid(args):
        return args[:2] == ["powercfg", "/import"] and guid in args

    def matcher_list(args):
        return args[:2] == ["powercfg", "/list"]

    def matcher_setactive(args):
        return args[:2] == ["powercfg", "/setactive"]

    fake_subprocess.script(
        matcher_import_with_guid,
        FakeSubprocessResult(returncode=1, stderr="Element not found."))
    fake_subprocess.script(
        matcher_list,
        FakeSubprocessResult(returncode=0, stdout=f"Power Scheme GUID: {guid}  (My Custom Plan)\n"))
    fake_subprocess.script(matcher_setactive, FakeSubprocessResult(returncode=0))
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.restore(SNAPSHOT, snapshot_dir)

    assert result["status"] == "matched"
    import_item = next(i for i in result["items"] if i["name"] == "import")
    assert "plan already present, activating existing" == import_item["detail"]
    setactive_calls = [a for a, kw in fake_subprocess.run_calls if a[:2] == ["powercfg", "/setactive"]]
    assert setactive_calls == [["powercfg", "/setactive", guid]]
    # The retry-without-guid import must never have been attempted.
    bare_import_calls = [
        a for a, kw in fake_subprocess.run_calls
        if a[:2] == ["powercfg", "/import"] and len(a) == 3
    ]
    assert bare_import_calls == []


# ---------------------------------------------------------------------------
# restore() — reimport-assigns-new-guid path
# ---------------------------------------------------------------------------

def test_restore_reimport_without_guid_activates_new_guid(
        monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, True)
    _stage_pow_file(snapshot_dir)
    original_guid = SNAPSHOT["guid"]
    new_guid = "22222222-2222-2222-2222-222222222222"

    def matcher_import_with_guid(args):
        return args[:2] == ["powercfg", "/import"] and original_guid in args

    def matcher_list(args):
        return args[:2] == ["powercfg", "/list"]

    def matcher_bare_import(args):
        return args[:2] == ["powercfg", "/import"] and original_guid not in args

    def matcher_setactive(args):
        return args[:2] == ["powercfg", "/setactive"]

    fake_subprocess.script(
        matcher_import_with_guid,
        FakeSubprocessResult(returncode=1, stderr="Element not found."))
    fake_subprocess.script(
        matcher_list,
        FakeSubprocessResult(returncode=0, stdout="Power Scheme GUID: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa  (Balanced)\n"))
    fake_subprocess.script(
        matcher_bare_import,
        FakeSubprocessResult(returncode=0, stdout=f"Power Scheme GUID: {new_guid}\n"))
    fake_subprocess.script(matcher_setactive, FakeSubprocessResult(returncode=0))
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.restore(SNAPSHOT, snapshot_dir)

    assert result["status"] == "matched"
    setactive_calls = [a for a, kw in fake_subprocess.run_calls if a[:2] == ["powercfg", "/setactive"]]
    assert setactive_calls == [["powercfg", "/setactive", new_guid]]


# ---------------------------------------------------------------------------
# restore() — all imports fail
# ---------------------------------------------------------------------------

def test_restore_all_imports_fail_captures_both_outputs_and_never_activates(
        monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, True)
    _stage_pow_file(snapshot_dir)
    original_guid = SNAPSHOT["guid"]

    def matcher_import_with_guid(args):
        return args[:2] == ["powercfg", "/import"] and original_guid in args

    def matcher_list(args):
        return args[:2] == ["powercfg", "/list"]

    def matcher_bare_import(args):
        return args[:2] == ["powercfg", "/import"] and original_guid not in args

    fake_subprocess.script(
        matcher_import_with_guid,
        FakeSubprocessResult(returncode=1, stdout="", stderr="Import failed: access denied."))
    fake_subprocess.script(
        matcher_list,
        FakeSubprocessResult(returncode=0, stdout="Power Scheme GUID: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa  (Balanced)\n"))
    fake_subprocess.script(
        matcher_bare_import,
        FakeSubprocessResult(returncode=1, stdout="", stderr="Import failed entirely."))
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.restore(SNAPSHOT, snapshot_dir)

    assert result["status"] == "failed"
    import_item = next(i for i in result["items"] if i["name"] == "import")
    assert "access denied" in import_item["detail"]
    assert "Import failed entirely" in import_item["detail"]
    # /setactive must never be called when every import attempt failed.
    setactive_calls = [a for a, kw in fake_subprocess.run_calls if a[:2] == ["powercfg", "/setactive"]]
    assert setactive_calls == []


def test_restore_failed_import_with_guid_looking_stdout_is_not_activated(
        monkeypatch, snapshot_dir, fake_subprocess):
    """Regression test for the deleted dead-logic branch: a FAILED import's
    stdout that happens to contain a GUID-looking string must never be
    treated as a real assigned GUID and activated. The GUID is only ever
    parsed from a *successful* import's output."""
    _install_admin(monkeypatch, True)
    _stage_pow_file(snapshot_dir)
    original_guid = SNAPSHOT["guid"]
    decoy_guid = "99999999-9999-9999-9999-999999999999"

    def matcher_import_with_guid(args):
        return args[:2] == ["powercfg", "/import"] and original_guid in args

    def matcher_list(args):
        return args[:2] == ["powercfg", "/list"]

    def matcher_bare_import(args):
        return args[:2] == ["powercfg", "/import"] and original_guid not in args

    # The failed import's stdout contains something that LOOKS like a GUID
    # line -- the old buggy code would have parsed and activated this.
    fake_subprocess.script(
        matcher_import_with_guid,
        FakeSubprocessResult(
            returncode=1,
            stdout=f"Error importing scheme. GUID: {decoy_guid}  (Some Stale Plan)\n",
            stderr="Import failed."))
    fake_subprocess.script(
        matcher_list,
        FakeSubprocessResult(returncode=0, stdout="Power Scheme GUID: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa  (Balanced)\n"))
    fake_subprocess.script(
        matcher_bare_import,
        FakeSubprocessResult(returncode=1, stdout="", stderr="Retry also failed."))
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.restore(SNAPSHOT, snapshot_dir)

    assert result["status"] == "failed"
    setactive_calls = [a for a, kw in fake_subprocess.run_calls if a[:2] == ["powercfg", "/setactive"]]
    # Critically: the decoy GUID scraped from the failed import's stdout
    # must never appear in a /setactive call.
    assert setactive_calls == []
    assert not any(decoy_guid in a for a, kw in fake_subprocess.run_calls if a[:2] == ["powercfg", "/setactive"])


def test_restore_setactive_failure_is_reported_failed(
        monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, True)
    _stage_pow_file(snapshot_dir)
    guid = SNAPSHOT["guid"]

    def matcher_import(args):
        return args[:2] == ["powercfg", "/import"]

    def matcher_setactive(args):
        return args[:2] == ["powercfg", "/setactive"]

    fake_subprocess.script(matcher_import, FakeSubprocessResult(returncode=0))
    fake_subprocess.script(
        matcher_setactive,
        FakeSubprocessResult(returncode=1, stderr="Invalid GUID."))
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.restore(SNAPSHOT, snapshot_dir)

    # Import matched, activation failed -> partial (Req 7: failed + matched
    # aggregates to partial, never a silent overall success).
    assert result["status"] == "partial"
    activate_item = next(i for i in result["items"] if i["name"] == "activate")
    assert activate_item["status"] == "failed"
    assert "Invalid GUID" in activate_item["detail"]
    assert guid in [a[2] for a, kw in fake_subprocess.run_calls if a[:2] == ["powercfg", "/setactive"]]


def test_restore_missing_pow_file_is_failed(monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, True)
    _install_subprocess(monkeypatch, fake_subprocess)
    # No .pow file staged.

    result = power.restore(SNAPSHOT, snapshot_dir)

    assert result["status"] == "failed"
    assert fake_subprocess.run_calls == []


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------

def test_verify_non_admin_is_skipped(monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, False)
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.verify(SNAPSHOT, snapshot_dir)

    assert result["status"] == "skipped"
    assert "elevation" in result["reason"].lower()
    assert fake_subprocess.run_calls == []


def test_verify_matched_when_active_scheme_matches_guid(
        monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, True)
    guid = SNAPSHOT["guid"]

    def matcher_getactive(args):
        return args[:2] == ["powercfg", "/getactivescheme"]

    fake_subprocess.script(
        matcher_getactive,
        FakeSubprocessResult(returncode=0, stdout=f"Power Scheme GUID: {guid}  (My Custom Plan)\n"))
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.verify(SNAPSHOT, snapshot_dir)

    assert result["status"] == "matched"
    # Read-only: verify must never call /import or /setactive.
    mutating_calls = [
        a for a, kw in fake_subprocess.run_calls
        if a[:2] in (["powercfg", "/import"], ["powercfg", "/setactive"])
    ]
    assert mutating_calls == []


def test_verify_failed_when_active_scheme_differs(
        monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, True)

    def matcher_getactive(args):
        return args[:2] == ["powercfg", "/getactivescheme"]

    fake_subprocess.script(
        matcher_getactive,
        FakeSubprocessResult(
            returncode=0,
            stdout="Power Scheme GUID: bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb  (Balanced)\n"))
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.verify(SNAPSHOT, snapshot_dir)

    assert result["status"] == "failed"
    item = next(i for i in result["items"] if i["name"] == "active_scheme")
    assert item["expected"] == SNAPSHOT["guid"]
    assert "bbbbbbbb" in item["actual"]


def test_verify_nothing_to_verify_when_not_enabled(
        monkeypatch, snapshot_dir, fake_subprocess):
    _install_admin(monkeypatch, True)
    _install_subprocess(monkeypatch, fake_subprocess)

    result = power.verify({"enabled": False}, snapshot_dir)

    assert result["status"] == "skipped"
    assert fake_subprocess.run_calls == []
