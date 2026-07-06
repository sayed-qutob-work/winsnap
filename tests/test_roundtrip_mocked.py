"""
tests/test_roundtrip_mocked.py — CI-safe round-trip harness (Task 16).

Feature: backend-roundtrip-hardening, Req 7.5, 7.7, 15.5 -- the feature's
"definition of done": a scripted same-machine export -> restore -> verify
round trip reports every category as matched or explicitly, honestly
skipped (with reason), never as a false success.

Drives the REAL export.main() and restore.main() entry points end to end.
Every OS boundary the 13 settings modules cross -- registry, subprocess,
user32/shell32/gdi32, and the real per-user directories a few modules read
or write outside the snapshot dir -- is replaced with the deterministic,
side-effect-free fakes already established across this test suite
(tests/conftest.py's FakeWinReg/FakeSubprocess, and the per-module
"patch module.winreg / module.ctypes.windll" convention used throughout
tests/test_*.py), so this test never touches the real registry, never
spawns a real winget/powercfg process, never restarts the real Explorer,
and never writes into the real user profile.

Also unit-tests scripts/roundtrip_check.py's pure verdict function,
evaluate_report() -- Task 16's second artifact, the real-machine
executable script -- without touching a real machine.

**Validates: Requirements 7.5, 7.7, 15.5**
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import FakeWinReg, FakeSubprocess, _build_winreg_module

import export as export_module
import restore as restore_module
from modules import (
    apps, cursors, desktop_icons, env_vars, explorer, fonts, mouse_display,
    power, region_lang, sound_scheme, startup, taskbar, wallpaper, winutil,
)

from scripts.roundtrip_check import evaluate_report


# ---------------------------------------------------------------------------
# OS-boundary mocking for a full export -> restore -> verify cycle
# ---------------------------------------------------------------------------

# Every module that touches winreg directly (module-level `import winreg`),
# so its `winreg` name can be replaced with the shared fake. winutil is
# included because wallpaper's style/tile writes go through
# winutil.write_reg_value/read_reg_value rather than wallpaper's own winreg.
_WINREG_MODULES = (
    env_vars, region_lang, wallpaper, mouse_display, cursors, sound_scheme,
    startup, fonts, explorer, desktop_icons, taskbar, apps, winutil,
)

# Modules that shell out via subprocess: apps (winget export/install/list),
# winutil (restart_explorer's taskkill/explorer.exe). power is patched too,
# defensively -- it should be structurally unreachable here since
# winutil.is_admin() is forced False below, but patching it means a real
# powercfg invocation is impossible even if that assumption ever breaks.
_SUBPROCESS_MODULES = (apps, winutil, power)


class _FakeUser32:
    def SystemParametersInfoW(self, *a):
        return 1

    def SendMessageTimeoutW(self, *a):
        return 1

    def GetSystemMetrics(self, *a):
        return 1


class _FakeShell32:
    def IsUserAnAdmin(self):
        # Deterministic non-admin regardless of the account actually
        # running the test suite: forces power.restore()/verify() down
        # their already-tested "requires elevation" skip path instead of
        # depending on whatever privileges happen to be available wherever
        # this test runs.
        return 0


class _FakeGdi32:
    def AddFontResourceW(self, *a):
        return 1


class _FakeWindll:
    def __init__(self):
        self.user32 = _FakeUser32()
        self.shell32 = _FakeShell32()
        self.gdi32 = _FakeGdi32()


def _build_full_fake_winreg_module(fake_reg: FakeWinReg):
    """
    conftest's _build_winreg_module() covers every winreg entry point this
    feature's modules use except EnumKey (subkey enumeration), needed by
    apps.py's registry app scan and sound_scheme.py's event-sound walk.
    EnumKey always raises OSError immediately here, modeling an empty
    subkey enumeration -- deterministic, and sufficient for this test's
    purpose (no exception, no false success). Without this, an unset
    EnumKey on the MagicMock built by _build_winreg_module would return
    another MagicMock forever and spin the `while True` enumeration loops
    in apps.py/sound_scheme.py into an infinite loop instead of raising.
    """
    mod = _build_winreg_module(fake_reg)
    mod.EnumKey = MagicMock(side_effect=OSError("no more subkeys (fake registry)"))
    return mod


def _mock_os_boundaries(monkeypatch, tmp_path) -> FakeWinReg:
    """
    Replaces every OS boundary this feature's 13 modules cross with
    deterministic, side-effect-free fakes, so a full export -> restore ->
    verify cycle can run against the real export.py/restore.py entry points
    without touching the real registry, spawning winget/powercfg, restarting
    the real Explorer, or writing into the real user profile.

    Starting from a completely empty FakeWinReg means every module's
    export() captures "nothing set" for its registry-backed fields; the
    corresponding restore()/verify() calls then take each module's own
    already-unit-tested "absent from snapshot" skip path -- exactly the
    "matched or honestly skipped" outcome this test asserts on, without
    this test having to hand-author realistic per-module fixture data for
    all 13 categories.
    """
    fake_reg = FakeWinReg()
    fake_winreg_module = _build_full_fake_winreg_module(fake_reg)
    for mod in _WINREG_MODULES:
        monkeypatch.setattr(mod, "winreg", fake_winreg_module)

    # ctypes is a single shared module object across the whole interpreter
    # (every module here did a plain `import ctypes`), so patching `windll`
    # once reaches every module's ctypes.windll.user32/shell32/gdi32 calls
    # -- including export.py's own admin check.
    import ctypes
    monkeypatch.setattr(ctypes, "windll", _FakeWindll())

    fake_subprocess = FakeSubprocess()
    for mod in _SUBPROCESS_MODULES:
        monkeypatch.setattr(mod, "subprocess", fake_subprocess)

    # winget presence check: force "not found" so apps.restore()/verify()
    # take their own already-unit-tested skip_all path deterministically,
    # regardless of whether winget happens to be installed on whatever
    # machine runs this test.
    monkeypatch.setattr(apps.shutil, "which", lambda name: None)

    # Redirect every real per-user directory this feature's modules read or
    # write outside the snapshot dir, so nothing lands in the real user
    # profile: the Startup folder and user Fonts dir (via APPDATA/
    # LOCALAPPDATA) and the taskbar pins folder (a module-level constant,
    # computed at import time, so it must be patched directly rather than
    # via the environment).
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setattr(taskbar, "TASKBAR_PINS_DIR", tmp_path / "fake_taskbar_pins")

    return fake_reg


# ---------------------------------------------------------------------------
# The round trip itself
# ---------------------------------------------------------------------------

def test_export_restore_verify_round_trip_all_matched_or_skipped(
        monkeypatch, tmp_path, capsys):
    """
    Drives the real export.main() -> restore.main() --verify --report-json
    cycle end to end. Every category in both the "restore" and "verify"
    sections of the structured report must be "matched" or "skipped" with a
    non-empty reason -- never "failed" -- and the process must exit 0
    (Req 7.5, 7.7, 15.5).
    """
    _mock_os_boundaries(monkeypatch, tmp_path)

    # --- Export (headless, --all-apps so the interactive checklist/TTY is
    # never touched) ---
    monkeypatch.setattr(sys, "argv", [
        "export.py", "--all-apps",
        "--output", str(tmp_path), "--name", "rt", "--force",
    ])
    export_module.main()

    winsnap_path = tmp_path / "rt.winsnap"
    assert winsnap_path.exists(), \
        "export.py did not produce the expected .winsnap archive"

    # --- Restore + verify ---
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "restore.py", str(winsnap_path),
        "--verify", "--report-json", str(report_path),
    ])
    with pytest.raises(SystemExit) as exc_info:
        restore_module.main()

    if exc_info.value.code != 0:
        # Surface restore.py's own printed summary to make a failure here
        # actionable instead of just "exit code != 0".
        pytest.fail(
            f"restore.py exited {exc_info.value.code}; captured output:\n"
            f"{capsys.readouterr().out}"
        )

    assert report_path.exists(), "restore.py did not write --report-json output"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert set(report.keys()) == {"snapshot_format", "restore", "verify", "exit_code"}
    assert report["exit_code"] == 0

    _assert_all_matched_or_honestly_skipped(report)


def _assert_all_matched_or_honestly_skipped(report: dict) -> None:
    """Shared assertion: every category in both phases is matched or
    skipped-with-reason, never failed/partial, and both phases actually ran
    (nothing silently empty)."""
    for phase in ("restore", "verify"):
        phase_reports = report[phase]
        assert phase_reports, f"{phase} section is empty -- no modules ran"
        for category, cat_report in phase_reports.items():
            status = cat_report["status"]
            assert status in ("matched", "skipped"), (
                f"{phase}.{category} reported {status!r} "
                f"(reason={cat_report.get('reason')!r}, "
                f"items={cat_report.get('items')!r})"
            )
            if status == "skipped":
                assert cat_report.get("reason"), (
                    f"{phase}.{category} is skipped without a reason"
                )


# ---------------------------------------------------------------------------
# evaluate_report(): scripts/roundtrip_check.py's pure verdict function
# ---------------------------------------------------------------------------

class TestEvaluateReport:
    """
    Unit tests for scripts/roundtrip_check.py's evaluate_report(), the pure
    function that turns a --report-json payload into a PASS/FAIL verdict.
    Kept independent of any real export/restore/subprocess so the script's
    verdict logic is testable without a real machine (Task 16).
    """

    @staticmethod
    def _report(restore=None, verify=None, exit_code=0):
        return {
            "snapshot_format": "0.3.0",
            "restore": restore or {},
            "verify": verify or {},
            "exit_code": exit_code,
        }

    def test_all_matched_passes(self):
        report = self._report(
            restore={"a": {"status": "matched"}},
            verify={"a": {"status": "matched"}},
        )
        passed, message = evaluate_report(report)
        assert passed
        assert "PASS" in message

    def test_skipped_with_reason_passes(self):
        report = self._report(
            restore={"a": {"status": "skipped", "reason": "nothing to restore"}},
            verify={"a": {"status": "skipped", "reason": "nothing to restore"}},
        )
        passed, message = evaluate_report(report)
        assert passed

    def test_failed_category_fails(self):
        report = self._report(restore={"a": {"status": "failed", "reason": None}})
        passed, message = evaluate_report(report)
        assert not passed
        assert "restore.a" in message
        assert "FAIL" in message

    def test_partial_category_fails(self):
        report = self._report(verify={"a": {"status": "partial"}})
        passed, message = evaluate_report(report)
        assert not passed
        assert "verify.a" in message

    def test_skipped_without_reason_fails(self):
        report = self._report(restore={"a": {"status": "skipped", "reason": None}})
        passed, message = evaluate_report(report)
        assert not passed
        assert "no reason" in message

    def test_skipped_without_reason_key_fails(self):
        """A category missing the "reason" key entirely (not just None) is
        just as dishonest as an empty one."""
        report = self._report(restore={"a": {"status": "skipped"}})
        passed, message = evaluate_report(report)
        assert not passed

    def test_nonzero_exit_code_fails_even_if_all_matched(self):
        report = self._report(restore={"a": {"status": "matched"}}, exit_code=1)
        passed, message = evaluate_report(report)
        assert not passed
        assert "exit_code" in message

    def test_empty_reports_pass_trivially(self):
        """No categories in either phase -- vacuously fine as far as
        evaluate_report() is concerned; the round-trip test itself
        separately asserts at least one module actually ran."""
        passed, message = evaluate_report(self._report())
        assert passed

    def test_multiple_problems_all_listed(self):
        report = self._report(
            restore={
                "a": {"status": "failed"},
                "b": {"status": "skipped", "reason": None},
            },
            exit_code=1,
        )
        passed, message = evaluate_report(report)
        assert not passed
        assert "restore.a" in message
        assert "restore.b" in message
        assert "exit_code" in message
