"""
test_integration_restore.py — Integration tests against restore.py.

These tests exercise the full restore.py flow (or as much as possible) with
mocked OS boundaries. They verify end-to-end behavior including module
ordering and correct interaction between restore.py's orchestration and
individual module restore() calls.

Note: this file previously also covered winget-batch-import behavior, the
multi-monitor COM wallpaper path, and taskbar's inline Explorer restart --
all of which were removed by the backend-roundtrip-hardening feature (per-
package winget install loop, Task 5; COM path deletion, Task 6; the
INLINE_EXPLORER_RESTART flag replacing the inline restart, Task 9). Those
scenarios are now covered by tests/test_apps_winget.py,
tests/test_wallpaper_multimon_bug.py, and tests/test_taskband.py
respectively, so the corresponding test classes here were removed rather
than rewritten (their replacement coverage already exists) instead of
duplicating it.

Validates: Requirements 2.1, 2.5, 7.4, 7.5
"""

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import restore as restore_module
from modules import manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_winsnap_archive(tmp_path, snapshot_data: dict, extra_files: dict = None):
    """
    Create a .winsnap zip archive with a snapshot.json and optional extra files.

    Args:
        tmp_path: pytest tmp_path fixture
        snapshot_data: dict to serialize as snapshot.json
        extra_files: dict of {relative_path: bytes_content} for additional files

    Returns:
        Path to the created .winsnap file
    """
    # Create the snapshot directory structure inside a subfolder
    snap_folder_name = "winsnap_20240101_120000"
    snap_content_dir = tmp_path / "archive_content" / snap_folder_name
    snap_content_dir.mkdir(parents=True)

    # Write snapshot.json
    json_path = snap_content_dir / "snapshot.json"
    json_path.write_text(json.dumps(snapshot_data, indent=2), encoding="utf-8")

    # Write extra files
    if extra_files:
        for rel_path, content in extra_files.items():
            file_path = snap_content_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                file_path.write_bytes(content)
            else:
                file_path.write_text(content, encoding="utf-8")

    # Create the zip
    archive_path = tmp_path / "test.winsnap"
    with zipfile.ZipFile(archive_path, "w") as zf:
        for file in snap_content_dir.rglob("*"):
            if file.is_file():
                arcname = str(file.relative_to(tmp_path / "archive_content"))
                zf.write(file, arcname)

    return archive_path


# ---------------------------------------------------------------------------
# Module run order and per-module exception handling
# ---------------------------------------------------------------------------

class TestModuleOrderingAndErrorHandling:
    """
    Integration tests confirming the module run order in restore.py
    (ALL_MODULES) matches modules/manifest.py's canonical order (Req 2.1,
    2.5), and that a per-module exception during restore is caught and
    synthesized into a failed report rather than aborting the remaining
    modules or the overall restore (Req 7.4, 7.5).
    """

    def test_all_modules_order_unchanged(self):
        """
        ALL_MODULES is derived from manifest.MODULE_NAMES (Req 2.5): the
        key order must match exactly, and apps -- which startup and
        taskbar depend on for installed binaries/shortcuts -- must run
        before both (Req 2.1).
        """
        actual_order = [key for key, mod in restore_module.ALL_MODULES]
        assert actual_order == manifest.MODULE_NAMES, \
            f"ALL_MODULES must be derived from manifest.MODULE_NAMES. Got: {actual_order}"

        assert actual_order.index("apps") < actual_order.index("startup"), \
            "apps must run before startup"
        assert actual_order.index("apps") < actual_order.index("taskbar"), \
            "apps must run before taskbar"

    def test_per_module_exception_caught_and_surfaced(self, tmp_path, monkeypatch):
        """
        When a module raises an exception during restore, restore.py
        synthesizes a failed report for that module instead of letting the
        exception propagate -- other modules still run, and the failure is
        visible both in the overall exit code and in the structured report.
        """
        from modules import wallpaper, apps
        from modules.report import Report

        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.3.0",
            "snapshot_format_version": "0.3.0",
            "modules": {
                "wallpaper": {"enabled": True, "filename": "wallpaper.jpg"},
                "apps": {"winget": [], "manual": []},
            },
        }
        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        def exploding_restore(snapshot, snapshot_dir):
            raise RuntimeError("Simulated wallpaper failure")
        monkeypatch.setattr(wallpaper, "restore", exploding_restore)

        apps_called = []

        def mock_apps_restore(snapshot, snapshot_dir):
            apps_called.append(True)
            return Report("apps", "restore").skip_all("nothing to install")
        monkeypatch.setattr(apps, "restore", mock_apps_restore)

        report_path = tmp_path / "report.json"
        monkeypatch.setattr(sys, "argv", [
            "restore.py", str(archive_path), "--only", "wallpaper", "apps",
            "--report-json", str(report_path),
        ])

        with pytest.raises(SystemExit) as exc_info:
            restore_module.main()

        assert exc_info.value.code != 0, \
            "A per-module exception must make the overall exit code non-zero"
        assert len(apps_called) == 1, \
            "Apps module should still run after wallpaper failure"

        payload = json.loads(report_path.read_text(encoding="utf-8"))
        wallpaper_report = payload["restore"]["wallpaper"]
        assert wallpaper_report["status"] == "failed"
        assert "Simulated wallpaper failure" in (wallpaper_report.get("reason") or "")
        assert payload["restore"]["apps"]["status"] == "skipped"

    def test_multiple_module_errors_all_surfaced(self, tmp_path, monkeypatch):
        """
        When multiple modules raise exceptions, each is recorded as its own
        failed report and the run continues to completion for all modules.
        """
        from modules import wallpaper, apps

        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.3.0",
            "snapshot_format_version": "0.3.0",
            "modules": {
                "wallpaper": {"enabled": True, "filename": "wallpaper.jpg"},
                "apps": {"winget": [{"PackageIdentifier": "X"}], "manual": []},
            },
        }
        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        def exploding_wallpaper(snapshot, snapshot_dir):
            raise RuntimeError("Wallpaper boom")

        def exploding_apps(snapshot, snapshot_dir):
            raise ValueError("Apps boom")

        monkeypatch.setattr(wallpaper, "restore", exploding_wallpaper)
        monkeypatch.setattr(apps, "restore", exploding_apps)

        report_path = tmp_path / "report.json"
        monkeypatch.setattr(sys, "argv", [
            "restore.py", str(archive_path), "--only", "wallpaper", "apps",
            "--report-json", str(report_path),
        ])

        with pytest.raises(SystemExit) as exc_info:
            restore_module.main()

        assert exc_info.value.code != 0

        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert payload["restore"]["wallpaper"]["status"] == "failed"
        assert "Wallpaper boom" in payload["restore"]["wallpaper"]["reason"]
        assert payload["restore"]["apps"]["status"] == "failed"
        assert "Apps boom" in payload["restore"]["apps"]["reason"]
