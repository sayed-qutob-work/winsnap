"""
test_headless_export.py — Unit tests for headless app-selection plumbing.

Feature: backend-roundtrip-hardening, Task 5.2 (Design D8: headless export).

Module-level tests only, covering:
  - modules.apps.export(selection="all") / selection="file" never import or
    call modules.checklist.run (Req 8.1, 8.2).
  - modules.apps.export() with no selection flag (interactive default)
    still calls checklist.run, preserving current behavior (Req 8.3).
  - modules.checklist.run raises RuntimeError immediately when stdin is not
    a TTY, before touching msvcrt (Req 8.5).
  - A replaced checklist.run (simulating the GUI's runtime monkey-patch at
    gui.py:1228-1230) bypasses the TTY guard entirely, since the guard lives
    inside the original implementation only (Req 8.4, 15.6).

Task 13 (Phase C, export.py CLI flags) appends CLI-level tests to this file
later -- these tests exercise modules.apps / modules.checklist directly, not
export.py's argument parsing.

**Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 15.1, 15.6**
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from modules import apps, checklist


# ---------------------------------------------------------------------------
# Shared no-op stand-ins so export()'s discovery steps don't touch the real
# registry / winget on the test machine.
# ---------------------------------------------------------------------------

def _stub_discovery(monkeypatch, winget_apps=None, manual_apps=None):
    winget_apps = winget_apps if winget_apps is not None else [
        {"PackageIdentifier": "Git.Git"},
        {"PackageIdentifier": "Discord.Discord"},
    ]
    manual_apps = manual_apps if manual_apps is not None else [
        {"name": "Some Manual App", "urlinfoabout": "https://example.com"},
    ]
    monkeypatch.setattr(apps, "_export_winget", lambda snapshot_dir: (winget_apps, None))
    monkeypatch.setattr(apps, "_scan_registry_apps", lambda show_all=False: manual_apps)
    return winget_apps, manual_apps


def _raise_if_called(*args, **kwargs):
    raise AssertionError("checklist.run must not be invoked for headless selection")


# ---------------------------------------------------------------------------
# selection="all" / "file" never touch the checklist (Req 8.1, 8.2)
# ---------------------------------------------------------------------------

def test_selection_all_never_touches_checklist(monkeypatch, snapshot_dir):
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(checklist, "run", _raise_if_called)

    result = apps.export(snapshot_dir, selection="all")

    assert result["winget"] == [
        {"PackageIdentifier": "Git.Git"},
        {"PackageIdentifier": "Discord.Discord"},
    ]
    assert result["manual"] == [{"name": "Some Manual App", "urlinfoabout": "https://example.com"}]


def test_selection_file_never_touches_checklist(monkeypatch, snapshot_dir):
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(checklist, "run", _raise_if_called)

    selection_file = snapshot_dir / "selection.json"
    selection_file.write_text(json.dumps({
        "winget": ["Git.Git"],
        "manual": ["Some Manual App"],
    }), encoding="utf-8")

    result = apps.export(snapshot_dir, selection="file", selection_file=selection_file)

    assert result["winget"] == [{"PackageIdentifier": "Git.Git"}]
    assert result["manual"] == [{"name": "Some Manual App", "urlinfoabout": "https://example.com"}]


def test_selection_file_records_unmatched_entries_as_warnings(monkeypatch, snapshot_dir):
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(checklist, "run", _raise_if_called)

    selection_file = snapshot_dir / "selection.json"
    selection_file.write_text(json.dumps({
        "winget": ["Git.Git", "Nonexistent.Package"],
        "manual": ["Nonexistent App"],
    }), encoding="utf-8")

    result = apps.export(snapshot_dir, selection="file", selection_file=selection_file)

    assert result["winget"] == [{"PackageIdentifier": "Git.Git"}]
    assert result["manual"] == []
    assert "selection_warnings" in result
    warnings_text = " ".join(result["selection_warnings"])
    assert "Nonexistent.Package" in warnings_text
    assert "Nonexistent App" in warnings_text


def test_selection_all_writes_filtered_winget_export(monkeypatch, snapshot_dir):
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(checklist, "run", _raise_if_called)

    apps.export(snapshot_dir, selection="all")

    out_file = snapshot_dir / "winget_export.json"
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    packages = data["Sources"][0]["Packages"]
    assert packages == [{"PackageIdentifier": "Git.Git"}, {"PackageIdentifier": "Discord.Discord"}]


# ---------------------------------------------------------------------------
# interactive default still calls checklist.run (Req 8.3)
# ---------------------------------------------------------------------------

def test_interactive_default_calls_checklist_run(monkeypatch, snapshot_dir):
    winget_apps, manual_apps = _stub_discovery(monkeypatch)

    calls = []

    def fake_run(winget_list, manual_list):
        calls.append((winget_list, manual_list))
        return ([{"PackageIdentifier": "Git.Git"}], [])

    monkeypatch.setattr(checklist, "run", fake_run)

    result = apps.export(snapshot_dir)  # no selection kwarg -> "interactive" default

    assert len(calls) == 1
    assert calls[0] == (winget_apps, manual_apps)
    assert result["winget"] == [{"PackageIdentifier": "Git.Git"}]


def test_interactive_cancelled_returns_empty_result(monkeypatch, snapshot_dir):
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(checklist, "run", lambda winget_list, manual_list: None)

    result = apps.export(snapshot_dir, selection="interactive")

    assert result["winget"] == []
    assert result["manual"] == []


# ---------------------------------------------------------------------------
# checklist.run TTY guard (Req 8.5)
# ---------------------------------------------------------------------------

def test_checklist_run_raises_off_tty(monkeypatch):
    monkeypatch.setattr(checklist.sys.stdin, "isatty", lambda: False)

    with pytest.raises(RuntimeError, match="terminal"):
        checklist.run([], [])


def test_checklist_run_does_not_raise_on_tty(monkeypatch):
    """With a TTY, the guard passes through -- verified by confirming the
    guard's RuntimeError is not what stops execution (the loop's first
    _get_terminal_size()/_read_key() call will be reached instead)."""
    monkeypatch.setattr(checklist.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(checklist, "_enable_ansi", lambda: None)

    # Immediately raise from _get_terminal_size to prove we got past the
    # guard without needing to drive the full interactive render/input loop.
    class _Sentinel(Exception):
        pass

    def boom():
        raise _Sentinel("reached past the TTY guard")

    monkeypatch.setattr(checklist, "_get_terminal_size", boom)

    with pytest.raises(_Sentinel):
        checklist.run([], [])


# ---------------------------------------------------------------------------
# A replaced checklist.run (GUI monkey-patch simulation) bypasses the guard
# entirely (Req 8.4, 15.6) -- the guard lives inside the *original*
# implementation, not in apps.py, so a full replacement of the attribute
# never executes it.
# ---------------------------------------------------------------------------

def test_gui_style_monkeypatch_bypasses_tty_guard(monkeypatch, snapshot_dir):
    """Simulates gui.py:1228-1230's `checklist_module.run = <bridge method>`
    replacement: the attribute is swapped for an entirely different callable
    that never touches sys.stdin.isatty(), so it works with no TTY at all."""
    _stub_discovery(monkeypatch)

    monkeypatch.setattr(checklist.sys.stdin, "isatty", lambda: False)

    def gui_bridge_run(winget_list, manual_list):
        # A real GUI bridge would forward the request to a Qt dialog; here we
        # just prove it runs without raising despite no TTY.
        return (winget_list, manual_list)

    monkeypatch.setattr(checklist, "run", gui_bridge_run)

    result = apps.export(snapshot_dir, selection="interactive")

    assert result["winget"] == [
        {"PackageIdentifier": "Git.Git"},
        {"PackageIdentifier": "Discord.Discord"},
    ]
