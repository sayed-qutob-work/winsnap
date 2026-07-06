"""
test_restore_hygiene.py — Tests for restore.py's archive hygiene and CLI
orchestration.

Feature: backend-roundtrip-hardening, Task 12 (rewrite restore.py
orchestration: safe extraction, ordering, verify, reporting, exit codes).

Covers:
  - zip-slip refusal (safe_extract), listing every offending member
  - find_snapshot_dir: flat / nested / missing snapshot.json layouts
  - well-formed archive regression (no behavior change for legitimate
    snapshots)
  - the exit-code matrix (all matched -> 0, any failed -> 1, newer major
    -> 2), including a verify-phase-only failure that a naive
    dict-merge-based exit code would mask
  - --report-json structure
  - exactly-once Explorer restart, after the last restore and before the
    first verify call

**Validates: Requirements 13.1, 13.2, 13.4, 14.3, 7.2, 7.3, 7.5, 1.3, 2.2**
"""

import json
import sys
import types
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from tests.conftest import make_winsnap_zip, stage_snapshot_json

import restore as restore_module
from modules import report as report_module


# ---------------------------------------------------------------------------
# Stub module helper
# ---------------------------------------------------------------------------

def _make_stub_module(restore_status: str, verify_status: str | None = None,
                       explorer_restart: bool = False, order_log: list | None = None,
                       name: str = "stub"):
    """
    Build a minimal (restore[, verify]) module object -- a plain namespace,
    not a real modules/* module -- so exit-code / ordering tests don't
    depend on real registry access. `restore_status`/`verify_status` are
    one of "matched", "failed", "partial", "skipped", or None (verify not
    implemented). `order_log`, if given, records f"restore:{name}" /
    f"verify:{name}" / call markers so tests can assert call ordering.
    """
    mod = types.SimpleNamespace()

    def _build(phase, status):
        rpt = report_module.Report(name, phase)
        if status == "matched":
            rpt.add_matched("thing")
        elif status == "failed":
            rpt.add_failed("thing", "boom")
        elif status == "partial":
            rpt.add_matched("a")
            rpt.add_failed("b", "boom")
        elif status == "skipped":
            return rpt.skip_all("nothing to do")
        if phase == "restore" and explorer_restart:
            rpt.require_explorer_restart()
        return rpt.finalize()

    def restore(data, snapshot_dir):
        if order_log is not None:
            order_log.append(f"restore:{name}")
        return _build("restore", restore_status)
    mod.restore = restore

    if verify_status is not None:
        def verify(data, snapshot_dir):
            if order_log is not None:
                order_log.append(f"verify:{name}")
            return _build("verify", verify_status)
        mod.verify = verify

    return mod


def _run_main_expect_exit(monkeypatch, argv) -> int:
    """Run restore_module.main() with the given argv, returning the exit code
    passed to sys.exit (SystemExit.code)."""
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc_info:
        restore_module.main()
    return exc_info.value.code


# ---------------------------------------------------------------------------
# safe_extract: zip-slip refusal
# ---------------------------------------------------------------------------

class TestSafeExtractZipSlip:
    @pytest.mark.parametrize("bad_member", [
        "../evil.txt",
        "winsnap_test/../../escaped.txt",
        "..\\evil_backslash.txt",
    ])
    def test_relative_traversal_member_rejected(self, tmp_path, bad_member):
        zip_path = make_winsnap_zip(tmp_path, member_names=[bad_member])
        dest = tmp_path / "extract_dest"
        dest.mkdir()

        with zipfile.ZipFile(zip_path, "r") as zf:
            with pytest.raises(restore_module.ZipSlipError) as exc_info:
                restore_module.safe_extract(zf, dest)

        assert bad_member.replace("\\", "/") in [
            m.replace("\\", "/") for m in exc_info.value.members
        ]
        # Nothing should have been extracted from a rejected archive.
        assert not any(dest.rglob("*")), \
            "safe_extract must not partially extract a rejected archive"

    def test_multiple_offenders_all_listed(self, tmp_path):
        bad = ["../evil1.txt", "../../evil2.txt"]
        zip_path = make_winsnap_zip(tmp_path, member_names=bad)
        dest = tmp_path / "extract_dest"
        dest.mkdir()

        with zipfile.ZipFile(zip_path, "r") as zf:
            with pytest.raises(restore_module.ZipSlipError) as exc_info:
                restore_module.safe_extract(zf, dest)

        assert len(exc_info.value.members) == len(bad)

    def test_absolute_path_member_rejected(self, tmp_path):
        # An absolute path member (drive-letter escape on Windows).
        zip_path = tmp_path / "abs_evil.winsnap"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("snapshot.json", json.dumps({"modules": {}}))
            zi = zipfile.ZipInfo(str(Path(tmp_path.drive + "\\") / "evil.txt")
                                  if tmp_path.drive else "C:\\evil.txt")
            zf.writestr(zi, "hostile")

        dest = tmp_path / "extract_dest"
        dest.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            with pytest.raises(restore_module.ZipSlipError):
                restore_module.safe_extract(zf, dest)

    def test_main_prints_offenders_and_exits_1(self, tmp_path, monkeypatch, capsys):
        zip_path = make_winsnap_zip(tmp_path, member_names=["../evil.txt"])
        code = _run_main_expect_exit(monkeypatch, ["restore.py", str(zip_path)])
        assert code == 1
        captured = capsys.readouterr()
        assert "evil.txt" in captured.out


# ---------------------------------------------------------------------------
# find_snapshot_dir: flat / nested / missing
# ---------------------------------------------------------------------------

class TestFindSnapshotDir:
    def test_flat_layout(self, tmp_path):
        stage_snapshot_json(tmp_path, version="0.3.0", modules={})
        result = restore_module.find_snapshot_dir(tmp_path)
        assert result == tmp_path

    def test_nested_layout(self, tmp_path):
        sub = tmp_path / "winsnap_20240101_120000"
        sub.mkdir()
        stage_snapshot_json(sub, version="0.3.0", modules={})
        result = restore_module.find_snapshot_dir(tmp_path)
        assert result == sub

    def test_nested_layout_with_sibling_dirs(self, tmp_path):
        # An unrelated empty sibling directory must not confuse selection.
        (tmp_path / "unrelated_empty_dir").mkdir()
        sub = tmp_path / "winsnap_real"
        sub.mkdir()
        stage_snapshot_json(sub, version="0.3.0", modules={})
        result = restore_module.find_snapshot_dir(tmp_path)
        assert result == sub

    def test_missing_snapshot_json_raises(self, tmp_path):
        (tmp_path / "some_dir").mkdir()
        (tmp_path / "some_dir" / "other_file.txt").write_text("nope")
        with pytest.raises(restore_module.SnapshotLayoutError):
            restore_module.find_snapshot_dir(tmp_path)

    def test_empty_archive_raises(self, tmp_path):
        with pytest.raises(restore_module.SnapshotLayoutError):
            restore_module.find_snapshot_dir(tmp_path)


# ---------------------------------------------------------------------------
# Well-formed archive regression (Req 13.4)
# ---------------------------------------------------------------------------

class TestWellFormedArchiveRegression:
    def test_well_formed_archive_extracts_and_locates_identically(self, tmp_path):
        zip_path = make_winsnap_zip(
            tmp_path, folder_name="winsnap_regress",
            modules={"env_vars": {"vars": {}}},
        )
        dest = tmp_path / "extract_dest"
        dest.mkdir()

        with zipfile.ZipFile(zip_path, "r") as zf:
            expected_names = set(zf.namelist())
            restore_module.safe_extract(zf, dest)

        extracted_names = {
            str(p.relative_to(dest)).replace("\\", "/")
            for p in dest.rglob("*") if p.is_file()
        }
        assert extracted_names == {n.replace("\\", "/") for n in expected_names}

        snapshot_dir = restore_module.find_snapshot_dir(dest)
        assert snapshot_dir == dest / "winsnap_regress"
        assert (snapshot_dir / "snapshot.json").exists()


# ---------------------------------------------------------------------------
# Exit-code matrix
# ---------------------------------------------------------------------------

class TestExitCodeMatrix:
    def test_all_matched_exits_zero(self, tmp_path, monkeypatch):
        stub = _make_stub_module(restore_status="matched")
        monkeypatch.setattr(restore_module, "ALL_MODULES", [("stub_mod", stub)])

        zip_path = make_winsnap_zip(
            tmp_path, modules={"stub_mod": {"anything": True}}
        )
        code = _run_main_expect_exit(monkeypatch, ["restore.py", str(zip_path)])
        assert code == 0

    def test_restore_failure_exits_one(self, tmp_path, monkeypatch):
        stub = _make_stub_module(restore_status="failed")
        monkeypatch.setattr(restore_module, "ALL_MODULES", [("stub_mod", stub)])

        zip_path = make_winsnap_zip(
            tmp_path, modules={"stub_mod": {"anything": True}}
        )
        code = _run_main_expect_exit(monkeypatch, ["restore.py", str(zip_path)])
        assert code == 1

    def test_newer_major_version_exits_two(self, tmp_path, monkeypatch):
        zip_path = make_winsnap_zip(tmp_path, version="1.0.0", modules={})
        code = _run_main_expect_exit(monkeypatch, ["restore.py", str(zip_path)])
        assert code == 2

    def test_verify_only_failure_exits_one_not_masked(self, tmp_path, monkeypatch):
        """
        A module whose restore is skipped (nothing to compare) but whose
        verify fails must still produce a non-zero exit code. A naive
        implementation that merges {**restore_reports, **verify_reports}
        into one dict before calling worst_exit_code would also mask the
        opposite case -- a restore failure "overwritten" by a later verify
        success for the same key -- so this test also covers a
        restore-failed/verify-matched module, asserting exit 1 either way.
        """
        verify_only_fail = _make_stub_module(
            restore_status="skipped", verify_status="failed")
        restore_fail_verify_ok = _make_stub_module(
            restore_status="failed", verify_status="matched", name="other")

        monkeypatch.setattr(restore_module, "ALL_MODULES", [
            ("verify_only_fail", verify_only_fail),
            ("restore_fail_verify_ok", restore_fail_verify_ok),
        ])
        zip_path = make_winsnap_zip(tmp_path, modules={
            "verify_only_fail": {"anything": True},
            "restore_fail_verify_ok": {"anything": True},
        })
        code = _run_main_expect_exit(
            monkeypatch, ["restore.py", str(zip_path), "--verify"]
        )
        assert code == 1


# ---------------------------------------------------------------------------
# --report-json structure
# ---------------------------------------------------------------------------

class TestReportJson:
    def test_report_json_structure(self, tmp_path, monkeypatch):
        stub = _make_stub_module(restore_status="matched", verify_status="matched")
        monkeypatch.setattr(restore_module, "ALL_MODULES", [("stub_mod", stub)])

        zip_path = make_winsnap_zip(
            tmp_path, version="0.3.0", modules={"stub_mod": {"anything": True}}
        )
        report_path = tmp_path / "report.json"
        code = _run_main_expect_exit(monkeypatch, [
            "restore.py", str(zip_path), "--verify",
            "--report-json", str(report_path),
        ])
        assert code == 0
        assert report_path.exists()

        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert set(payload.keys()) == {
            "snapshot_format", "restore", "verify", "exit_code"
        }
        assert payload["snapshot_format"] == "0.3.0"
        assert payload["exit_code"] == 0
        assert payload["restore"]["stub_mod"]["status"] == "matched"
        assert payload["verify"]["stub_mod"]["status"] == "matched"


# ---------------------------------------------------------------------------
# Exactly-once Explorer restart, after all restores, before any verify
# ---------------------------------------------------------------------------

class TestExplorerRestartOrdering:
    def test_restart_happens_once_after_restores_before_verify(
            self, tmp_path, monkeypatch
    ):
        order_log: list = []
        restart_calls: list = []

        def fake_restart_explorer():
            restart_calls.append(True)
            order_log.append("restart")
            return True

        monkeypatch.setattr(restore_module.winutil, "restart_explorer",
                             fake_restart_explorer)

        mod_a = _make_stub_module(
            restore_status="matched", verify_status="matched",
            explorer_restart=True, order_log=order_log, name="mod_a")
        mod_b = _make_stub_module(
            restore_status="matched", verify_status="matched",
            explorer_restart=False, order_log=order_log, name="mod_b")

        monkeypatch.setattr(restore_module, "ALL_MODULES", [
            ("mod_a", mod_a), ("mod_b", mod_b),
        ])

        zip_path = make_winsnap_zip(tmp_path, modules={
            "mod_a": {"anything": True},
            "mod_b": {"anything": True},
        })
        code = _run_main_expect_exit(
            monkeypatch, ["restore.py", str(zip_path), "--verify"]
        )
        assert code == 0

        assert len(restart_calls) == 1, \
            "Explorer must be restarted exactly once"

        restart_index = order_log.index("restart")
        restore_indices = [i for i, e in enumerate(order_log)
                            if e.startswith("restore:")]
        verify_indices = [i for i, e in enumerate(order_log)
                           if e.startswith("verify:")]
        assert restore_indices, "expected restore calls to be logged"
        assert verify_indices, "expected verify calls to be logged"
        assert max(restore_indices) < restart_index < min(verify_indices), \
            f"restart must occur after all restores and before any verify: {order_log}"

    def test_no_restart_when_not_required(self, tmp_path, monkeypatch):
        restart_calls: list = []
        monkeypatch.setattr(restore_module.winutil, "restart_explorer",
                             lambda: restart_calls.append(True))

        stub = _make_stub_module(restore_status="matched", explorer_restart=False)
        monkeypatch.setattr(restore_module, "ALL_MODULES", [("stub_mod", stub)])

        zip_path = make_winsnap_zip(tmp_path, modules={"stub_mod": {"a": True}})
        code = _run_main_expect_exit(monkeypatch, ["restore.py", str(zip_path)])
        assert code == 0
        assert restart_calls == []
