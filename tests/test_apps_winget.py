"""
test_apps_winget.py — Unit tests for modules/apps.py's hardened winget
restore/export/verify flow.

Feature: backend-roundtrip-hardening, Task 5.1 (Design D3: per-package
`winget install` loop, export honesty, real CreationDate).

Covers:
  - restore(): winget-absent presence check (skip, no exception);
    per-package classification table (installed / already installed /
    unavailable / failed) via FakeSubprocess.script; no `timeout` kwarg is
    ever passed to a `winget install` subprocess.run call; the loop
    continues past an unavailable/failed package instead of aborting;
    manual apps are recorded as skipped items, never failures.
  - _export_winget(): returns (packages, error_msg) with timeout=120;
    surfaces an explicit error on TimeoutExpired / FileNotFoundError /
    nonzero exit / invalid JSON instead of a silent empty list.
  - _write_filtered_winget_export(): CreationDate is a real, parseable,
    approximately-now timestamp (not the old hardcoded 2024-01-01).
  - verify(): per-package `winget list` classification; manual apps skipped
    as "not verifiable programmatically"; winget-absent -> skipped.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 7.1**
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from modules import apps
from modules.apps import WINGET_ALREADY_INSTALLED, WINGET_NO_PACKAGE_FOUND

from tests.conftest import FakeSubprocess, FakeSubprocessResult


# ---------------------------------------------------------------------------
# restore(): winget presence check (Req 3.1)
# ---------------------------------------------------------------------------

def test_restore_winget_absent_is_skipped_not_raised(monkeypatch, snapshot_dir):
    """Absent winget -> whole category skipped, no exception, no subprocess call."""
    fake_sub = FakeSubprocess()
    monkeypatch.setattr(apps, "subprocess", fake_sub)
    monkeypatch.setattr(apps.shutil, "which", lambda name: None)

    data = {"winget": [{"PackageIdentifier": "Git.Git"}], "manual": []}

    result = apps.restore(data, snapshot_dir)

    assert result["status"] == "skipped"
    assert "winget not found" in result["reason"]
    assert fake_sub.run_calls == []


# ---------------------------------------------------------------------------
# restore(): per-package classification table (Req 3.3, 3.4)
# ---------------------------------------------------------------------------

def _make_result(returncode=0, stdout="", stderr=""):
    return FakeSubprocessResult(returncode=returncode, stdout=stdout, stderr=stderr)


def _is_install_for(pkg_id):
    def matcher(args):
        return args[:2] == ["winget", "install"] and "--id" in args and pkg_id in args
    return matcher


@pytest.mark.parametrize(
    "returncode, stdout, expected_status, expected_detail_substr",
    [
        (0, "Successfully installed", "matched", "installed"),
        (WINGET_ALREADY_INSTALLED, "", "matched", "already installed"),
        (1, "Found an existing package already installed.", "matched", "already installed"),
        (WINGET_NO_PACKAGE_FOUND, "", "skipped", "unavailable"),
        (1, "No package found matching input criteria.", "skipped", "unavailable"),
        (1, "some other winget failure", "failed", "returncode=1"),
        # winget writes its real error explanation to stdout, not stderr --
        # the failure detail must carry it (0x8A15003B == 2316632123).
        (2316632123, "0x8a15003b : Rest API internal error", "failed",
         "Rest API internal error"),
        (2316632123, "", "failed", "(0x8A15003B)"),
    ],
    ids=["installed", "already-installed-code", "already-installed-stdout",
         "unavailable-code", "unavailable-stdout", "other-failure",
         "failure-stdout-captured", "failure-hex-code"],
)
def test_restore_classifies_each_outcome(monkeypatch, snapshot_dir,
                                          returncode, stdout, expected_status,
                                          expected_detail_substr):
    fake_sub = FakeSubprocess()
    fake_sub.script(_is_install_for("Some.Package"),
                    _make_result(returncode=returncode, stdout=stdout, stderr="err tail"))
    monkeypatch.setattr(apps, "subprocess", fake_sub)
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    data = {"winget": [{"PackageIdentifier": "Some.Package"}], "manual": []}

    result = apps.restore(data, snapshot_dir)

    items = {item["name"]: item for item in result["items"]}
    assert "Some.Package" in items
    item = items["Some.Package"]
    assert item["status"] == expected_status
    assert expected_detail_substr in item["detail"]


def test_restore_no_timeout_kwarg_passed_to_install(monkeypatch, snapshot_dir):
    """A WinSnap-imposed timeout could kill a legitimate slow install (Req 3.2)."""
    fake_sub = FakeSubprocess()
    monkeypatch.setattr(apps, "subprocess", fake_sub)
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    data = {"winget": [{"PackageIdentifier": "Big.SlowInstaller"}], "manual": []}
    apps.restore(data, snapshot_dir)

    install_calls = [(a, kw) for a, kw in fake_sub.run_calls if a[:2] == ["winget", "install"]]
    assert len(install_calls) == 1
    _, kwargs = install_calls[0]
    assert "timeout" not in kwargs


def test_restore_continues_past_unavailable_and_failed_packages(monkeypatch, snapshot_dir):
    """One bad/unavailable package must not abort the remaining packages (Req 3.4)."""
    fake_sub = FakeSubprocess()
    fake_sub.script(_is_install_for("Unavailable.Package"),
                     _make_result(returncode=WINGET_NO_PACKAGE_FOUND))
    fake_sub.script(_is_install_for("Broken.Package"),
                     _make_result(returncode=1, stderr="catastrophic failure"))
    fake_sub.script(_is_install_for("Good.Package"),
                     _make_result(returncode=0))
    monkeypatch.setattr(apps, "subprocess", fake_sub)
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    data = {
        "winget": [
            {"PackageIdentifier": "Unavailable.Package"},
            {"PackageIdentifier": "Broken.Package"},
            {"PackageIdentifier": "Good.Package"},
        ],
        "manual": [],
    }

    result = apps.restore(data, snapshot_dir)

    # All three packages got attempted -- the loop never stopped early.
    names = [item["name"] for item in result["items"]]
    assert names == ["Unavailable.Package", "Broken.Package", "Good.Package"]

    install_calls = [a for a, kw in fake_sub.run_calls if a[:2] == ["winget", "install"]]
    assert len(install_calls) == 3

    statuses = {item["name"]: item["status"] for item in result["items"]}
    assert statuses["Unavailable.Package"] == "skipped"
    assert statuses["Broken.Package"] == "failed"
    assert statuses["Good.Package"] == "matched"
    # A failed item alongside a matched item aggregates to "partial", not a
    # false-success "matched" (Req 7 aggregation rule).
    assert result["status"] == "partial"


def test_restore_manual_apps_are_skipped_never_failed(monkeypatch, snapshot_dir):
    fake_sub = FakeSubprocess()
    monkeypatch.setattr(apps, "subprocess", fake_sub)
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    data = {
        "winget": [],
        "manual": [{"name": "Some Manual App", "urlinfoabout": "https://example.com"}],
    }

    result = apps.restore(data, snapshot_dir)

    assert result["status"] == "skipped"
    item = result["items"][0]
    assert item["name"] == "Some Manual App"
    assert item["status"] == "skipped"
    assert "manual install required" in item["detail"]
    assert "https://example.com" in item["detail"]


# ---------------------------------------------------------------------------
# _export_winget(): export honesty (Req 3.5)
# ---------------------------------------------------------------------------

def test_export_winget_returns_packages_and_none_error_on_success(monkeypatch, snapshot_dir):
    fake_sub = FakeSubprocess()

    def run(args, **kwargs):
        fake_sub.run_calls.append((args, kwargs))
        # Simulate `winget export` writing the file itself.
        out_index = args.index("-o") + 1
        out_path = Path(args[out_index])
        out_path.write_text(json.dumps({
            "Sources": [{"Packages": [{"PackageIdentifier": "Git.Git"}]}]
        }), encoding="utf-8")
        return FakeSubprocessResult(returncode=0)

    monkeypatch.setattr(apps.subprocess, "run", run)

    packages, error = apps._export_winget(snapshot_dir)

    assert error is None
    assert packages == [{"PackageIdentifier": "Git.Git"}]

    # timeout=120 was used for the export call (a generous metadata-dump
    # timeout, distinct from the no-timeout install loop).
    _, kwargs = fake_sub.run_calls[0]
    assert kwargs.get("timeout") == 120


def test_export_winget_timeout_surfaces_error_not_silent_empty_list(monkeypatch, snapshot_dir):
    def run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=120)

    monkeypatch.setattr(apps.subprocess, "run", run)

    packages, error = apps._export_winget(snapshot_dir)

    assert packages == []
    assert error is not None
    assert "timed out" in error.lower()


def test_export_winget_missing_binary_surfaces_error(monkeypatch, snapshot_dir):
    def run(args, **kwargs):
        raise FileNotFoundError("winget not found")

    monkeypatch.setattr(apps.subprocess, "run", run)

    packages, error = apps._export_winget(snapshot_dir)

    assert packages == []
    assert error is not None
    assert "winget not available" in error.lower()


def test_export_winget_invalid_json_surfaces_error(monkeypatch, snapshot_dir):
    out_file = snapshot_dir / "winget_export.json"

    def run(args, **kwargs):
        out_file.write_text("{not valid json", encoding="utf-8")
        return FakeSubprocessResult(returncode=0)

    monkeypatch.setattr(apps.subprocess, "run", run)

    packages, error = apps._export_winget(snapshot_dir)

    assert packages == []
    assert error is not None


def test_export_winget_nonzero_exit_with_file_returns_error_alongside_packages(monkeypatch, snapshot_dir):
    """A nonzero exit that still produced a (possibly partial) file is
    reported as an error, not silently treated as a clean export."""
    out_file = snapshot_dir / "winget_export.json"

    def run(args, **kwargs):
        out_file.write_text(json.dumps({"Sources": []}), encoding="utf-8")
        return FakeSubprocessResult(returncode=1, stderr="partial failure")

    monkeypatch.setattr(apps.subprocess, "run", run)

    packages, error = apps._export_winget(snapshot_dir)

    assert packages == []
    assert error is not None
    assert "1" in error


# ---------------------------------------------------------------------------
# _write_filtered_winget_export(): real CreationDate (Req 3.6)
# ---------------------------------------------------------------------------

def test_write_filtered_winget_export_creation_date_is_real_and_recent(snapshot_dir):
    before = datetime.now().astimezone()

    apps._write_filtered_winget_export(snapshot_dir, [{"PackageIdentifier": "Git.Git"}])

    out_file = snapshot_dir / "winget_export.json"
    data = json.loads(out_file.read_text(encoding="utf-8"))

    creation_date_str = data["CreationDate"]
    # Must not be the old hardcoded stub.
    assert not creation_date_str.startswith("2024-01-01T00:00:00.000-00:00")

    parsed = datetime.fromisoformat(creation_date_str)
    after = datetime.now().astimezone()

    # Parsed timestamp is timezone-aware (has a UTC offset) and falls within
    # the window this test ran in.
    assert parsed.tzinfo is not None
    assert before - timedelta(seconds=5) <= parsed <= after + timedelta(seconds=5)


# ---------------------------------------------------------------------------
# verify(): per-package winget list classification (design D3 Verify)
# ---------------------------------------------------------------------------

def test_verify_winget_absent_is_skipped(monkeypatch, snapshot_dir):
    fake_sub = FakeSubprocess()
    monkeypatch.setattr(apps, "subprocess", fake_sub)
    monkeypatch.setattr(apps.shutil, "which", lambda name: None)

    data = {"winget": [{"PackageIdentifier": "Git.Git"}], "manual": []}
    result = apps.verify(data, snapshot_dir)

    assert result["status"] == "skipped"
    assert fake_sub.run_calls == []


def test_verify_matches_installed_package_and_fails_missing_one(monkeypatch, snapshot_dir):
    fake_sub = FakeSubprocess()

    def matcher_for(pkg_id, ok):
        def matcher(args):
            return args[:2] == ["winget", "list"] and pkg_id in args
        return matcher

    fake_sub.script(matcher_for("Present.App", True), FakeSubprocessResult(returncode=0))
    fake_sub.script(matcher_for("Missing.App", False), FakeSubprocessResult(returncode=1))
    monkeypatch.setattr(apps, "subprocess", fake_sub)
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    data = {
        "winget": [{"PackageIdentifier": "Present.App"}, {"PackageIdentifier": "Missing.App"}],
        "manual": [{"name": "Manual Thing"}],
    }
    result = apps.verify(data, snapshot_dir)

    items = {item["name"]: item for item in result["items"]}
    assert items["Present.App"]["status"] == "matched"
    assert items["Missing.App"]["status"] == "failed"
    assert items["Manual Thing"]["status"] == "skipped"
    assert result["status"] == "partial"


def test_verify_never_writes_registry(monkeypatch, snapshot_dir):
    """verify() must be read-only (design D10 invariant)."""
    fake_sub = FakeSubprocess()
    monkeypatch.setattr(apps, "subprocess", fake_sub)
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    data = {"winget": [{"PackageIdentifier": "Git.Git"}], "manual": []}
    apps.verify(data, snapshot_dir)

    # No install/import command ever issued during verify.
    for call_args, _ in fake_sub.run_calls:
        assert call_args[1] != "install"
        assert call_args[1] != "import"
