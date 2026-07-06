"""
checklist.py
Interactive terminal checklist for selecting apps before export.
Uses msvcrt (Windows built-in) — no curses, no extra dependencies.

Controls:
  UP / DOWN      move cursor
  SPACE          toggle selection
  A              select all in current section
  N              deselect all in current section
  TAB            jump to next section
  ENTER          confirm and proceed
  Q / ESC        quit without saving
"""

import os
import sys
import msvcrt
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# ANSI helpers (Windows 10+ supports ANSI in cmd/PowerShell/Terminal)
# ---------------------------------------------------------------------------

def _enable_ansi():
    """Enable ANSI escape processing on Windows."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

ESC   = "\033["
RESET = "\033[0m"
CLEAR_SCREEN  = "\033[2J\033[H"
HIDE_CURSOR   = "\033[?25l"
SHOW_CURSOR   = "\033[?25h"

def _color(text, fg=None, bg=None, bold=False):
    codes = []
    if bold:  codes.append("1")
    if fg:    codes.append(str(fg))
    if bg:    codes.append(str(bg))
    return f"\033[{';'.join(codes)}m{text}{RESET}" if codes else text

# Foreground colors
FG_CYAN    = 96
FG_GREEN   = 92
FG_YELLOW  = 93
FG_WHITE   = 97
FG_GRAY    = 90
FG_BLACK   = 30
# Background
BG_CYAN    = 46

def _move(row, col=0):
    print(f"\033[{row};{col}H", end="")

def _clear():
    print(CLEAR_SCREEN, end="")

def _get_terminal_size():
    try:
        size = os.get_terminal_size()
        return size.lines, size.columns
    except OSError:
        return 40, 120

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AppEntry:
    name: str
    kind: Literal["winget", "manual"]
    selected: bool = True
    package_id: str = ""
    publisher: str = ""
    url: str = ""

SECTION_LABELS = {
    "winget": "WINGET APPS  (auto-installable via winget)",
    "manual": "MANUAL APPS  (saved as reference list)",
}
SECTION_ORDER = ["winget", "manual"]

# ---------------------------------------------------------------------------
# Build entries
# ---------------------------------------------------------------------------

def build_entries(winget_list: list, manual_list: list) -> list[AppEntry]:
    entries = []
    for pkg in winget_list:
        pid = pkg.get("PackageIdentifier", "")
        entries.append(AppEntry(name=pid, kind="winget", package_id=pid))
    for app in manual_list:
        entries.append(AppEntry(
            name=app.get("name", "Unknown"),
            kind="manual",
            publisher=app.get("publisher", ""),
            url=app.get("urlinfoabout", ""),
        ))
    return entries

# ---------------------------------------------------------------------------
# Keyboard reading
# ---------------------------------------------------------------------------

KEY_UP    = "UP"
KEY_DOWN  = "DOWN"
KEY_PGUP  = "PGUP"
KEY_PGDN  = "PGDN"
KEY_HOME  = "HOME"
KEY_END   = "END"
KEY_ENTER = "ENTER"
KEY_SPACE = "SPACE"
KEY_TAB   = "TAB"
KEY_ESC   = "ESC"

def _read_key():
    ch = msvcrt.getch()
    if ch == b'\r':   return KEY_ENTER
    if ch == b' ':    return KEY_SPACE
    if ch == b'\t':   return KEY_TAB
    if ch == b'\x1b': return KEY_ESC
    if ch in (b'q', b'Q'): return KEY_ESC
    if ch in (b'a', b'A'): return 'A'
    if ch in (b'n', b'N'): return 'N'
    # Arrow keys / special: msvcrt returns b'\xe0' or b'\x00' then a code byte
    if ch in (b'\xe0', b'\x00'):
        ch2 = msvcrt.getch()
        if ch2 == b'H': return KEY_UP
        if ch2 == b'P': return KEY_DOWN
        if ch2 == b'I': return KEY_PGUP
        if ch2 == b'Q': return KEY_PGDN
        if ch2 == b'G': return KEY_HOME
        if ch2 == b'O': return KEY_END
    return None

# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def _render(rows, entry_indices, cursor_pos, scroll_top, entries, h, w):
    visible_rows = h - 7  # top bar (3 lines) + bottom bar (2 lines) + padding

    lines = []

    # --- Header ---
    title = " WinSnap — App Selection "
    lines.append(_color(title.center(w - 1), FG_BLACK, BG_CYAN, bold=True))

    wg_sel   = sum(1 for e in entries if e.kind == "winget" and e.selected)
    wg_total = sum(1 for e in entries if e.kind == "winget")
    mn_sel   = sum(1 for e in entries if e.kind == "manual" and e.selected)
    mn_total = sum(1 for e in entries if e.kind == "manual")
    total_sel = wg_sel + mn_sel

    summary = (f"  winget: {wg_sel}/{wg_total}   "
               f"manual: {mn_sel}/{mn_total}   "
               f"total selected: {total_sel}/{wg_total + mn_total}")
    lines.append(_color(summary, FG_YELLOW))
    lines.append(_color("─" * (w - 1), FG_GRAY))

    # --- App rows ---
    visible_slice = range(scroll_top, min(scroll_top + visible_rows, len(rows)))
    for row_idx in visible_slice:
        kind, data = rows[row_idx]

        if kind == "header":
            label = f"  ── {SECTION_LABELS[data]}"
            lines.append(_color(label[:w-1], FG_CYAN, bold=True))

        elif kind == "entry":
            entry: AppEntry = data
            is_cursor = (entry_indices[cursor_pos] == row_idx)
            check = "[x]" if entry.selected else "[ ]"

            if entry.kind == "winget":
                text = f"  {check}  {entry.package_id}"
            else:
                pub = f"  ({entry.publisher})" if entry.publisher else ""
                text = f"  {check}  {entry.name}{pub}"

            text = text[:w-2].ljust(w - 2)

            if is_cursor:
                lines.append(_color(f" {text}", FG_BLACK, BG_CYAN, bold=True))
            elif entry.selected:
                lines.append(_color(f" {text}", FG_GREEN))
            else:
                lines.append(_color(f" {text}", FG_GRAY))

    # Pad remaining visible area
    rendered_app_rows = len(list(visible_slice))
    for _ in range(visible_rows - rendered_app_rows):
        lines.append("")

    # --- Footer ---
    lines.append(_color("─" * (w - 1), FG_GRAY))
    controls = " ↑↓ move   SPACE toggle   A all   N none   TAB section   ENTER confirm   Q quit"
    lines.append(_color(controls[:w-1], FG_YELLOW))

    # Draw all at once
    print(HIDE_CURSOR + CLEAR_SCREEN, end="")
    print("\n".join(lines), end="", flush=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(winget_list: list, manual_list: list):
    """
    Shows the interactive checklist.
    Returns (selected_winget, selected_manual) or None if cancelled.

    Raises RuntimeError immediately, before touching msvcrt, if stdin is not
    a real terminal (Req 8.5). A headless export (CI, scheduled task, remote
    session with no TTY) would otherwise hang forever on msvcrt.getch(); this
    guard turns that hang into a clear, immediate failure that export.py's
    per-module try/except records as the apps module's error.

    This guard lives here rather than in apps.py so that a caller who
    replaces `checklist.run` entirely (the GUI monkey-patches
    `modules.checklist.run` at gui.py:1228-1230) never hits it — the GUI
    runs without a console and must not be affected by this check.
    """
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Interactive app selection requires a terminal. "
            "Use --all-apps or --apps-from FILE for headless export."
        )

    _enable_ansi()
    entries = build_entries(winget_list, manual_list)

    # Build flat row list: ("header", section) | ("entry", AppEntry)
    rows = []
    for section in SECTION_ORDER:
        section_entries = [e for e in entries if e.kind == section]
        if not section_entries:
            continue
        rows.append(("header", section))
        for e in section_entries:
            rows.append(("entry", e))

    entry_indices = [i for i, r in enumerate(rows) if r[0] == "entry"]

    cursor_pos = 0
    scroll_top = 0

    try:
        while True:
            h, w = _get_terminal_size()
            visible_rows = h - 7

            # Keep cursor in view
            cursor_row = entry_indices[cursor_pos]
            if cursor_row < scroll_top:
                scroll_top = cursor_row
            elif cursor_row >= scroll_top + visible_rows:
                scroll_top = cursor_row - visible_rows + 1

            _render(rows, entry_indices, cursor_pos, scroll_top, entries, h, w)

            key = _read_key()

            if key == KEY_UP:
                if cursor_pos > 0:
                    cursor_pos -= 1

            elif key == KEY_DOWN:
                if cursor_pos < len(entry_indices) - 1:
                    cursor_pos += 1

            elif key == KEY_PGUP:
                cursor_pos = max(0, cursor_pos - (visible_rows - 2))

            elif key == KEY_PGDN:
                cursor_pos = min(len(entry_indices) - 1, cursor_pos + (visible_rows - 2))

            elif key == KEY_HOME:
                cursor_pos = 0

            elif key == KEY_END:
                cursor_pos = len(entry_indices) - 1

            elif key == KEY_SPACE:
                rows[entry_indices[cursor_pos]][1].selected = not rows[entry_indices[cursor_pos]][1].selected

            elif key == 'A':
                section = rows[entry_indices[cursor_pos]][1].kind
                for i in entry_indices:
                    if rows[i][1].kind == section:
                        rows[i][1].selected = True

            elif key == 'N':
                section = rows[entry_indices[cursor_pos]][1].kind
                for i in entry_indices:
                    if rows[i][1].kind == section:
                        rows[i][1].selected = False

            elif key == KEY_TAB:
                current_section = rows[entry_indices[cursor_pos]][1].kind
                sections = [s for s in SECTION_ORDER]
                idx = sections.index(current_section)
                next_section = sections[(idx + 1) % len(sections)]
                for i, ei in enumerate(entry_indices):
                    if rows[ei][1].kind == next_section:
                        cursor_pos = i
                        break

            elif key == KEY_ENTER:
                break

            elif key == KEY_ESC:
                print(SHOW_CURSOR + CLEAR_SCREEN)
                return None

    finally:
        print(SHOW_CURSOR, end="")

    print(CLEAR_SCREEN, end="")

    # Collect results — pull AppEntry objects directly from rows (they were mutated in place)
    all_entries = [row[1] for row in rows if row[0] == "entry"]
    selected_winget = [
        {"PackageIdentifier": e.package_id}
        for e in all_entries if e.kind == "winget" and e.selected
    ]
    manual_by_name = {a.get("name"): a for a in manual_list}
    selected_manual = [
        manual_by_name[e.name]
        for e in all_entries
        if e.kind == "manual" and e.selected and e.name in manual_by_name
    ]

    return selected_winget, selected_manual

