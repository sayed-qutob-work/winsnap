"""
manifest.py
Canonical, single source of truth for module execution order.

Both export.py (module selection) and restore.py (ALL_MODULES) derive their
module lists from MODULE_NAMES so the export and restore module *sets* can
never drift apart, and so the restore order is expressed in exactly one
place (Req 2.5).

Ordering rationale (Req 2):
  - `apps` runs early (winget install) so `startup` and `taskbar`, which
    depend on binaries/shortcuts that installing apps provides, run
    afterward and can find them (Req 2.1).
  - `taskbar` runs last: it (and the other Explorer-managed-state modules)
    determine when the single end-of-restore Explorer restart happens.
"""

MODULE_NAMES: list[str] = [
    "env_vars", "region_lang", "apps",              # apps early: startup/taskbar depend on it
    "wallpaper", "mouse_display", "cursors",
    "sound_scheme", "power", "fonts",
    "explorer", "desktop_icons",
    "startup", "taskbar",                            # consumers of installed apps run last
]
