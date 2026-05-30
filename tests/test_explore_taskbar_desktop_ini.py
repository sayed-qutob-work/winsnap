"""
Bug Condition Exploration Test — Taskbar restore aborts on uncopyable desktop.ini

**Validates: Requirements 1.4, 2.4**

Property 4: Bug Condition — Taskbar restore tolerates uncopyable files

For any pins backup folder that contains a file which cannot be copied due to
permissions (e.g. a hidden/system desktop.ini raising Errno 13), the fixed
taskbar.restore SHALL skip that file, complete the copy of the remaining pinned
.lnk shortcuts, and continue to theme writes and Explorer restart without aborting.

EXPECTED OUTCOME on UNFIXED code: Test FAILS because the PermissionError on
desktop.ini propagates and aborts the restore before theme writes / Explorer restart.
"""

import sys
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate 1-5 .lnk shortcut filenames (non-empty, the bug condition requires
# at least one pin to restore)
lnk_names_strategy = st.lists(
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), min_codepoint=65, max_codepoint=122),
        min_size=1,
        max_size=20,
    ).map(lambda s: s + ".lnk"),
    min_size=1,
    max_size=5,
    unique=True,
)

# Theme data strategy — random theme settings that would be written to registry
theme_strategy = st.fixed_dictionaries({
    "apps_light_theme": st.one_of(st.just(0), st.just(1), st.none()),
    "system_light_theme": st.one_of(st.just(0), st.just(1), st.none()),
    "accent_color": st.one_of(st.integers(min_value=0, max_value=0xFFFFFFFF), st.none()),
    "colorization_color": st.one_of(st.integers(min_value=0, max_value=0xFFFFFFFF), st.none()),
    "color_on_taskbar": st.one_of(st.just(0), st.just(1), st.none()),
    "transparency": st.one_of(st.just(0), st.just(1), st.none()),
})


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestTaskbarDesktopIniBugCondition:
    """
    Property 4: Bug Condition — Taskbar restore aborts on uncopyable file

    **Validates: Requirements 1.4, 2.4**

    Bug Condition: containsUncopyableFile(X) — the pins backup contains a
    desktop.ini that raises PermissionError (Errno 13) on copy.

    Expected behavior (Property 4): restore completes without abort, all .lnk
    pins are restored, theme is written, and Explorer is restarted.
    """

    @given(lnk_names=lnk_names_strategy, theme=theme_strategy)
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_restore_completes_despite_uncopyable_desktop_ini(
        self, lnk_names, theme, tmp_path_factory
    ):
        """
        For any pins backup containing .lnk shortcuts plus a desktop.ini that
        raises PermissionError on copy, assert:
          1. The restore completes without raising (no abort)
          2. All .lnk pins are restored to the target directory
          3. _write_theme_settings is called with the theme data
          4. _restart_explorer is called

        On UNFIXED code this MUST FAIL because shutil.copytree raises
        PermissionError on desktop.ini and the exception propagates, aborting
        before theme writes and Explorer restart.
        """
        # Create a unique temp dir for this example
        tmp_path = tmp_path_factory.mktemp("taskbar")

        # --- Stage the pins backup in snapshot_dir ---
        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()
        pins_backup = snapshot_dir / "taskbar_pins"
        pins_backup.mkdir()

        # Create .lnk files in the backup
        for name in lnk_names:
            (pins_backup / name).write_bytes(b"\x4c\x00\x00\x00" + b"\x00" * 50)

        # Create desktop.ini (the uncopyable file)
        desktop_ini = pins_backup / "desktop.ini"
        desktop_ini.write_text(
            "[.ShellClassInfo]\nIconResource=imageres.dll,-1023\n",
            encoding="utf-8",
        )

        # --- Build the snapshot dict ---
        snapshot = {
            "pins_backup": "taskbar_pins",
            "theme": theme,
        }

        # --- Set up a fake target TASKBAR_PINS_DIR ---
        fake_pins_target = tmp_path / "target_pins"
        fake_pins_target.mkdir(parents=True)

        # --- Patch shutil.copytree to raise PermissionError on desktop.ini ---
        original_copytree = shutil.copytree

        def patched_copytree(src, dst, **kwargs):
            """
            Wrap copytree so that copying desktop.ini raises PermissionError.
            The real unfixed code calls shutil.copytree which internally uses
            copy2; we simulate the PermissionError that occurs on the
            hidden/system desktop.ini.
            """
            src_path = Path(src)
            # Check if the source contains desktop.ini — if so, the real
            # copytree would fail. We simulate this by raising PermissionError.
            if (src_path / "desktop.ini").exists():
                raise PermissionError(
                    13, "Permission denied", str(src_path / "desktop.ini")
                )
            return original_copytree(src, dst, **kwargs)

        # Track whether _write_theme_settings and _restart_explorer were called
        theme_written = []
        explorer_restarted = []

        def mock_write_theme(t):
            theme_written.append(t)

        def mock_restart_explorer():
            explorer_restarted.append(True)

        # --- Run the restore under patches ---
        import modules.taskbar as taskbar_module

        with patch.object(taskbar_module, "TASKBAR_PINS_DIR", fake_pins_target):
            with patch.object(taskbar_module.shutil, "copytree", side_effect=patched_copytree):
                with patch.object(taskbar_module, "_write_theme_settings", side_effect=mock_write_theme):
                    with patch.object(taskbar_module, "_restart_explorer", side_effect=mock_restart_explorer):
                        # This should NOT raise — the restore should tolerate
                        # the PermissionError and continue
                        taskbar_module.restore(snapshot, snapshot_dir)

        # --- Assertions (Property 4 / Expected Behavior 2.4) ---

        # 1. All .lnk pins should be restored to the target directory
        restored_lnks = {f.name for f in fake_pins_target.glob("*.lnk")}
        expected_lnks = set(lnk_names)
        assert expected_lnks.issubset(restored_lnks), (
            f"Not all .lnk pins were restored. "
            f"Expected: {expected_lnks}, Got: {restored_lnks}"
        )

        # 2. Theme settings should have been written
        assert len(theme_written) == 1, (
            f"_write_theme_settings should have been called once, "
            f"but was called {len(theme_written)} times"
        )
        assert theme_written[0] == theme, (
            f"Theme data mismatch. Expected: {theme}, Got: {theme_written[0]}"
        )

        # 3. Explorer should have been restarted
        assert len(explorer_restarted) == 1, (
            f"_restart_explorer should have been called once, "
            f"but was called {len(explorer_restarted)} times"
        )
