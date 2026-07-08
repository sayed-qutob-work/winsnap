"""
test_run_dry_run_golden.py — Golden-output test for restore.run_dry_run.

Feature: gui-backend-alignment, Task 1.4 (Req 2.6, 8.8, 11.1)

Before extracting main()'s --dry-run loop into run_dry_run, the exact stdout
of the CURRENT (pre-refactor) inline loop was captured for a representative
set of inputs -- a found module, a not-found module, and an export-error
module (plus a few extra shapes for robustness: multiple found modules of
different _summarize branches, an empty run, and an all-skipped run). The
capture replicated main()'s inline loop body verbatim:

    for key, mod in modules_to_run:
        if key not in modules_data:
            print(f"[{key}] Not found in snapshot. Skipping.")
            continue
        data = modules_data[key]
        if isinstance(data, dict) and "error" in data:
            print(f"[{key}] Was not captured (export error). Skipping.")
            continue
        print(f"[{key}] {_summarize(key, data)}")

against restore._summarize (unmodified by this task), BEFORE run_dry_run
existed. The captured strings below are that baseline. This test then runs
the new run_dry_run against the same inputs and asserts both the printed
stdout and the returned dict match the baseline -- demonstrating Req 11.1's
byte-identical CLI behavior for this specific extraction, plus checking the
new structured return value run_dry_run adds for the GUI.
"""

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from restore import run_dry_run

# Each entry: (case_name, modules_to_run, modules_data, expected_stdout, expected_result)
# expected_stdout captured verbatim from the pre-refactor inline dry-run loop
# in main() (see module docstring); expected_result is the new structured
# return value run_dry_run additionally provides.
GOLDEN_CASES = [
    (
        "mixed_found_notfound_error",
        [("apps", None), ("wallpaper", None), ("fonts", None)],
        {
            "apps": {"winget": ["pkg1", "pkg2"], "manual": ["appX"]},
            "fonts": {"error": "network unreachable"},
            # wallpaper intentionally absent -> not found in snapshot
        },
        "[apps] would install 2 winget app(s), report 1 manual app(s)\n"
        "[wallpaper] Not found in snapshot. Skipping.\n"
        "[fonts] Was not captured (export error). Skipping.\n",
        {
            "apps": {
                "would_restore": True,
                "summary": "would install 2 winget app(s), report 1 manual app(s)",
                "skip_reason": None,
            },
            "wallpaper": {
                "would_restore": False,
                "summary": None,
                "skip_reason": "not_found_in_snapshot",
            },
            "fonts": {
                "would_restore": False,
                "summary": None,
                "skip_reason": "export_error",
            },
        },
    ),
    (
        "all_found_various_types",
        [("startup", None), ("sound_scheme", None), ("taskbar", None)],
        {
            "startup": {"registry": {"HKCU": ["a", "b"]}, "shortcuts": ["s1"]},
            "sound_scheme": {"scheme": "Default", "event_sounds": {"SystemAsterisk": "x.wav"}},
            "taskbar": ["pinned1", "pinned2", "pinned3"],
        },
        "[startup] would restore 2 registry entry(ies), 1 shortcut(s)\n"
        "[sound_scheme] would set scheme 'Default' with 1 event sound(s)\n"
        "[taskbar] would restore 3 item(s)\n",
        {
            "startup": {
                "would_restore": True,
                "summary": "would restore 2 registry entry(ies), 1 shortcut(s)",
                "skip_reason": None,
            },
            "sound_scheme": {
                "would_restore": True,
                "summary": "would set scheme 'Default' with 1 event sound(s)",
                "skip_reason": None,
            },
            "taskbar": {
                "would_restore": True,
                "summary": "would restore 3 item(s)",
                "skip_reason": None,
            },
        },
    ),
    (
        "empty_modules_to_run",
        [],
        {},
        "",
        {},
    ),
    (
        "all_skipped",
        [("apps", None), ("fonts", None)],
        {"fonts": {"error": "boom"}},
        "[apps] Not found in snapshot. Skipping.\n"
        "[fonts] Was not captured (export error). Skipping.\n",
        {
            "apps": {
                "would_restore": False,
                "summary": None,
                "skip_reason": "not_found_in_snapshot",
            },
            "fonts": {
                "would_restore": False,
                "summary": None,
                "skip_reason": "export_error",
            },
        },
    ),
]


@pytest.mark.parametrize(
    "case_name,modules_to_run,modules_data,expected_stdout,expected_result",
    GOLDEN_CASES,
    ids=[c[0] for c in GOLDEN_CASES],
)
def test_run_dry_run_golden(case_name, modules_to_run, modules_data,
                             expected_stdout, expected_result):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = run_dry_run(modules_to_run, modules_data)

    assert buf.getvalue() == expected_stdout, (
        f"{case_name}: printed output changed (got {buf.getvalue()!r}, "
        f"expected {expected_stdout!r})"
    )
    assert result == expected_result, (
        f"{case_name}: returned dict changed (got {result!r}, "
        f"expected {expected_result!r})"
    )
