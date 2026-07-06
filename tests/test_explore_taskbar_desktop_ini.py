"""
Bug Condition Exploration Test — Taskbar restore aborts on uncopyable desktop.ini

**Validates: Requirements 1.4, 2.4**

Property 4: Bug Condition — Taskbar restore tolerates uncopyable files

For any pins backup folder that contains a file which cannot be copied due to
permissions (e.g. a hidden/system desktop.ini raising Errno 13), taskbar.restore
SHALL skip that file, complete the copy of the remaining pinned .lnk shortcuts,
and continue to theme writes and Explorer restart without aborting.

Post-hardening note (backend-roundtrip-hardening, Task 9): the original
unfixed code copied the whole pins folder via `shutil.copytree`, so a single
uncopyable member (like a hidden/system desktop.ini) raised PermissionError
and aborted the entire restore before theme/Explorer-restart ran. The current
`taskbar._copy_pins_tolerant` instead walks the backup directory and copies
only `.lnk` files one at a time via `shutil.copy2`; desktop.ini (and anything
else that isn't a `.lnk`) is skipped by extension check and is never even
attempted, so simulating a `copytree`-level PermissionError no longer
reflects how the module operates. This test now drives the real
`_copy_pins_tolerant` code path directly and asserts the same durable
property: a non-essential file present in the pins backup never aborts the
restore, every `.lnk` still lands in the target directory, and theme write +
Explorer restart still run to completion (Req 1.4, 2.4).
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
        would raise PermissionError if ever copied, assert:
          1. The restore completes without raising (no abort)
          2. All .lnk pins are restored to the target directory
          3. desktop.ini is never copied into the target (skipped as
             non-essential, never even attempted)
          4. _write_theme_settings is called with the theme data
          5. winutil.restart_explorer is called

        Drives the real `_copy_pins_tolerant` code path (no copytree/copy2
        patching needed): desktop.ini is filtered out by extension before any
        copy is attempted, so the restore never even risks the
        PermissionError this test used to have to simulate.
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

        # Create desktop.ini (the non-essential file that must be skipped)
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

        # Track whether _write_theme_settings and restart_explorer were called
        theme_written = []
        explorer_restarted = []

        def mock_write_theme(t, rpt):
            theme_written.append(t)

        def mock_restart_explorer():
            explorer_restarted.append(True)

        # --- Run the restore under patches ---
        import modules.taskbar as taskbar_module
        from modules import winutil

        with patch.object(taskbar_module, "TASKBAR_PINS_DIR", fake_pins_target):
            with patch.object(taskbar_module, "_write_theme_settings", side_effect=mock_write_theme):
                with patch.object(winutil, "restart_explorer", side_effect=mock_restart_explorer):
                    # This should NOT raise — the restore tolerates the
                    # non-essential desktop.ini and continues.
                    taskbar_module.restore(snapshot, snapshot_dir)

        # --- Assertions (Property 4 / Expected Behavior 2.4) ---

        # 1. All .lnk pins should be restored to the target directory
        restored_lnks = {f.name for f in fake_pins_target.glob("*.lnk")}
        expected_lnks = set(lnk_names)
        assert expected_lnks.issubset(restored_lnks), (
            f"Not all .lnk pins were restored. "
            f"Expected: {expected_lnks}, Got: {restored_lnks}"
        )

        # 2. desktop.ini must never be copied into the target
        assert not (fake_pins_target / "desktop.ini").exists(), (
            "desktop.ini should be skipped, never copied"
        )

        # 3. Theme settings should have been written
        assert len(theme_written) == 1, (
            f"_write_theme_settings should have been called once, "
            f"but was called {len(theme_written)} times"
        )
        assert theme_written[0] == theme, (
            f"Theme data mismatch. Expected: {theme}, Got: {theme_written[0]}"
        )

        # 4. Explorer should have been restarted
        assert len(explorer_restarted) == 1, (
            f"restart_explorer should have been called once, "
            f"but was called {len(explorer_restarted)} times"
        )
