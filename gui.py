"""
gui.py  —  WinSnap GUI (PyQt6)
A desktop application wrapping WinSnap's export and restore CLIs.

This module defines the core value types, pure functions, Qt widgets,
and background workers for the WinSnap graphical interface.
"""

from __future__ import annotations

import contextlib
import enum
import json
import re
import shutil
import sys
import tempfile
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from modules import manifest
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Core enums
# ---------------------------------------------------------------------------


class Severity(enum.Enum):
    """Classification of a log entry's importance."""
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class ModuleStatus(enum.Enum):
    """Outcome status for a module row in the results summary.

    Values are literally the modules/report.py status vocabulary
    (``{"status": "matched"|"partial"|"failed"|"skipped"}``), so a report
    dict's status can be turned into a ModuleStatus via a direct
    ``ModuleStatus(report["status"])`` lookup with no translation table.
    """
    MATCHED = "matched"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class VersionVerdict(enum.Enum):
    """Result of evaluating a snapshot's format version."""
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNPARSEABLE = "unparseable"


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogEntry:
    """A single timestamped log message with severity."""
    timestamp: str
    message: str
    severity: Severity


@dataclass(frozen=True)
class ModuleOutcome:
    """The result of running, verifying, or skipping a single module.

    ``items`` carries a report dict's per-item list verbatim (plain dicts,
    per modules/report.py's own "no dataclass" design) so the results view
    can render per-item detail for partial/failed modules.
    """
    name: str
    status: ModuleStatus
    detail: str | None
    items: tuple[dict, ...] = ()


@dataclass
class ResultsSummary:
    """Accumulates restore/export outcomes and, separately, verify
    outcomes, so a module can carry both a restore status and a verify
    status without conflating the two."""
    outcomes: list[ModuleOutcome] = field(default_factory=list)
    verify_outcomes: list[ModuleOutcome] = field(default_factory=list)

    def add(self, outcome: ModuleOutcome) -> None:
        """Append a restore/export outcome to the summary."""
        self.outcomes.append(outcome)

    def add_verify(self, outcome: ModuleOutcome) -> None:
        """Append a verify outcome to the summary, separate from
        ``outcomes`` so a module's restore and verify results can be
        rendered side by side."""
        self.verify_outcomes.append(outcome)

    def matched(self) -> list[ModuleOutcome]:
        """Return all outcomes with MATCHED status."""
        return [o for o in self.outcomes if o.status == ModuleStatus.MATCHED]

    def partial(self) -> list[ModuleOutcome]:
        """Return all outcomes with PARTIAL status."""
        return [o for o in self.outcomes if o.status == ModuleStatus.PARTIAL]

    def failed(self) -> list[ModuleOutcome]:
        """Return all outcomes with FAILED status."""
        return [o for o in self.outcomes if o.status == ModuleStatus.FAILED]

    def skipped(self) -> list[ModuleOutcome]:
        """Return all outcomes with SKIPPED status."""
        return [o for o in self.outcomes if o.status == ModuleStatus.SKIPPED]

    def counts(self) -> tuple[int, int, int, int]:
        """Return (matched_count, partial_count, failed_count, skipped_count)."""
        return (
            len(self.matched()),
            len(self.partial()),
            len(self.failed()),
            len(self.skipped()),
        )

    def verify_for(self, name: str) -> ModuleOutcome | None:
        """Return the verify outcome for ``name``, or None if verification
        did not run (or did not cover) that module."""
        for outcome in self.verify_outcomes:
            if outcome.name == name:
                return outcome
        return None


@dataclass
class ExportConfig:
    """Configuration for an export operation, built from the Export view."""
    output_dir: Path
    name: str | None
    show_all: bool
    selected_modules: set[str]
    force: bool = False        # set by MainWindow on overwrite confirmation


@dataclass
class RestoreConfig:
    """Configuration for a restore operation, built from the Restore view."""
    snapshot_path: Path
    dry_run: bool
    selected_modules: set[str]
    verify: bool = False       # defaults to off, matching the CLI's --verify default


# ---------------------------------------------------------------------------
# Pure functions — severity classification and log formatting
# ---------------------------------------------------------------------------

# Markers used by modules in their print() output (case-insensitive matching)
_ERROR_MARKERS: tuple[str, ...] = ("error", "exception", "traceback", "failed")
_WARNING_MARKERS: tuple[str, ...] = ("warning", "advisory", "skipped")


def classify_severity(line: str) -> Severity:
    """Classify a log line's severity based on marker keywords.

    Error markers take priority over warning markers.
    Lines without any marker default to SUCCESS.
    """
    lower = line.lower()
    for marker in _ERROR_MARKERS:
        if marker in lower:
            return Severity.ERROR
    for marker in _WARNING_MARKERS:
        if marker in lower:
            return Severity.WARNING
    return Severity.SUCCESS


def format_log_line(entry: LogEntry) -> str:
    """Format a LogEntry for display, prefixed with its HH:MM:SS timestamp."""
    return f"{entry.timestamp}  {entry.message}"


# ---------------------------------------------------------------------------
# Pure functions — version evaluation
# ---------------------------------------------------------------------------

# Mirrors restore.VersionEvaluation.verdict's three string values, so
# to_version_verdict is a direct lookup rather than a hand-maintained
# translation table.
_VERSION_VERDICT_MAP: dict[str, VersionVerdict] = {
    "compatible": VersionVerdict.COMPATIBLE,
    "incompatible": VersionVerdict.INCOMPATIBLE,
    "unparseable": VersionVerdict.UNPARSEABLE,
}


def to_version_verdict(evaluation) -> VersionVerdict:
    """Pure mapping from a ``restore.VersionEvaluation`` to the GUI's
    presentation-only ``VersionVerdict`` enum.

    ``evaluation`` is (and is only ever, in practice) a
    ``restore.VersionEvaluation`` -- the single source of truth for the
    version-acceptance decision (Req 7.1, 7.2, 7.3). This function accepts
    any object exposing a ``.verdict`` attribute rather than importing
    ``restore`` at module scope, consistent with gui.py's existing pattern
    of importing ``restore``/``export``/``modules.checklist`` lazily inside
    worker methods rather than at import time.
    """
    return _VERSION_VERDICT_MAP[evaluation.verdict]


# ---------------------------------------------------------------------------
# Outcome classification functions
# ---------------------------------------------------------------------------


def classify_export_outcome(
    name: str, *, raised: Exception | None, result: dict | None
) -> ModuleOutcome:
    """Classify the outcome of running a module's export function.

    Classification rules (evaluated in order):
    - If the module raised an exception → FAILED with the exception text.
    - If the result dict has skip_reason == "not_admin" → FAILED with a
      message about Administrator privileges being required.
    - If the result dict has an "error" key or any other skip_reason →
      FAILED with the reported message.
    - Otherwise (including {"enabled": false} or empty data) → MATCHED.
    """
    if raised is not None:
        return ModuleOutcome(name=name, status=ModuleStatus.FAILED, detail=str(raised))

    if result is not None:
        skip_reason = result.get("skip_reason")
        if skip_reason == "not_admin":
            return ModuleOutcome(
                name=name,
                status=ModuleStatus.FAILED,
                detail="Administrator privileges required to capture the active power plan",
            )
        if skip_reason is not None:
            return ModuleOutcome(
                name=name, status=ModuleStatus.FAILED, detail=str(skip_reason)
            )
        if "error" in result:
            return ModuleOutcome(
                name=name, status=ModuleStatus.FAILED, detail=str(result["error"])
            )

    return ModuleOutcome(name=name, status=ModuleStatus.MATCHED, detail=None)


def report_to_outcome(name: str, report: dict) -> ModuleOutcome:
    """Pure mapping from a restore/verify report dict (modules/report.py's
    locked ``{status, reason, items, ...}`` contract) to a ModuleOutcome.

    ModuleStatus's values are literally the report's status strings
    (``"matched"``/``"partial"``/``"failed"``/``"skipped"``), so this is a
    direct lookup, not a hand-maintained translation table -- it cannot
    raise for any status string ``modules.report.aggregate_status`` can
    produce.
    """
    return ModuleOutcome(
        name=name,
        status=ModuleStatus(report["status"]),
        detail=report.get("reason"),
        items=tuple(report.get("items", [])),
    )


# Wording mirrors restore.py's own skip messages for these two reason codes
# (see restore.py's run_modules loop and _DRY_RUN_SKIP_MESSAGES), so a
# module skipped by the GUI reads the same as a module skipped by the CLI.
_REPORT_SKIP_REASON_TEXT: dict[str, str] = {
    "not_found_in_snapshot": "Not found in snapshot",
    "export_error": "Was not captured (export error)",
}


def skip_outcome(name: str, reason_code: str) -> ModuleOutcome:
    """Pure mapping from a skip reason code to a ModuleOutcome.

    ``reason_code`` is either ``"deselected"`` (a GUI-only concept -- the
    user did not select this module to run) or one of
    ``restore.partition_modules``'s two codes (``"not_found_in_snapshot"``,
    ``"export_error"``). All three map to ``ModuleStatus.SKIPPED``, with
    wording matching the CLI's printed skip messages for the latter two.
    """
    if reason_code == "deselected":
        return ModuleOutcome(name=name, status=ModuleStatus.SKIPPED, detail="Deselected by user")
    return ModuleOutcome(
        name=name, status=ModuleStatus.SKIPPED, detail=_REPORT_SKIP_REASON_TEXT[reason_code]
    )


# ---------------------------------------------------------------------------
# Pure functions — module run resolution
# ---------------------------------------------------------------------------


def resolve_run_modules(selected: set[str], order: list[str]) -> list[str]:
    """Return selected modules in canonical order, no duplicates, no unselected.

    Args:
        selected: Set of module names the user has chosen to include.
        order: The canonical execution order (export or restore).

    Returns:
        A list containing only modules present in *selected*, in the
        sequence they appear in *order*.
    """
    return [m for m in order if m in selected]


# ---------------------------------------------------------------------------
# Pure functions — snapshot naming
# ---------------------------------------------------------------------------

# Characters forbidden in Windows file names
_WINDOWS_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Reserved device names (case-insensitive), with or without extension
_WINDOWS_RESERVED_NAMES = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..+)?$",
    re.IGNORECASE,
)


def validate_snapshot_name(name: str) -> str | None:
    """Validate a user-supplied snapshot name for Windows file-system safety.

    Returns None if the name is valid, otherwise returns an error message
    describing why the name is invalid.
    """
    # Reject empty or whitespace-only names
    if not name or not name.strip():
        return "Snapshot name must not be empty"

    # Reject names exceeding 255 characters
    if len(name) > 255:
        return "Snapshot name must not exceed 255 characters"

    # Reject names containing Windows-forbidden characters
    match = _WINDOWS_FORBIDDEN_CHARS.search(name)
    if match:
        char = match.group()
        if ord(char) < 0x20:
            return f"Snapshot name contains a forbidden control character (0x{ord(char):02x})"
        return f"Snapshot name contains a forbidden character: {char}"

    # Reject reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    if _WINDOWS_RESERVED_NAMES.match(name):
        return f"Snapshot name must not be a reserved device name: {name}"

    # Reject names ending with a dot or space
    if name.endswith("."):
        return "Snapshot name must not end with a dot"
    if name.endswith(" "):
        return "Snapshot name must not end with a space"

    return None


# ---------------------------------------------------------------------------
# Pure functions — app-selection recording
# ---------------------------------------------------------------------------


def format_version_info_message(snapshot: dict) -> str:
    """Produce the version-info log message for the start of a restore.

    Reads "exported_at" and "snapshot_format_version" from the snapshot dict.
    If either key is absent or its value is None, substitutes "unknown".

    Returns a human-readable message containing both values.
    """
    exported_at = snapshot.get("exported_at")
    format_version = snapshot.get("snapshot_format_version")

    date_str = exported_at if exported_at is not None else "unknown"
    version_str = format_version if format_version is not None else "unknown"

    return f"Snapshot exported at {date_str}, format version {version_str}"


# ---------------------------------------------------------------------------
# Pure functions — app-selection recording
# ---------------------------------------------------------------------------


def record_app_selection(
    winget_states: list[bool],
    manual_states: list[bool],
    winget: list[dict],
    manual: list[dict],
    confirmed: bool,
) -> tuple[list[dict], list[dict]]:
    """Record the user's app selection from the App_Selector dialog.

    When confirmed is True, returns the entries whose corresponding mask
    value is True within each group. When confirmed is False (cancelled),
    returns ([], []) regardless of the masks.

    Args:
        winget_states: Boolean mask for winget apps (same length as winget).
        manual_states: Boolean mask for manual apps (same length as manual).
        winget: List of discovered winget app dicts.
        manual: List of discovered manual app dicts.
        confirmed: Whether the user confirmed (True) or cancelled (False).

    Returns:
        A tuple of (selected_winget, selected_manual) lists.
    """
    if not confirmed:
        return ([], [])

    selected_winget = [
        app for app, selected in zip(winget, winget_states) if selected
    ]
    selected_manual = [
        app for app, selected in zip(manual, manual_states) if selected
    ]
    return (selected_winget, selected_manual)


# ---------------------------------------------------------------------------
# Qt Widgets — ModuleSelector
# ---------------------------------------------------------------------------


class ModuleSelector(QWidget):
    """A widget with 13 labeled checkboxes (one per module) and bulk controls.

    All checkboxes are checked by default. Provides "Select all" and
    "Deselect all" buttons, plus methods to query and set the selection state.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._checkboxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)

        # Create a checkbox for each module, all checked by default
        for module_name in manifest.MODULE_NAMES:
            cb = QCheckBox(module_name, self)
            cb.setChecked(True)
            self._checkboxes[module_name] = cb
            layout.addWidget(cb)

        # Button row: Select all / Deselect all
        button_layout = QHBoxLayout()
        self._select_all_btn = QPushButton("Select all", self)
        self._deselect_all_btn = QPushButton("Deselect all", self)
        button_layout.addWidget(self._select_all_btn)
        button_layout.addWidget(self._deselect_all_btn)
        layout.addLayout(button_layout)

        # Connect buttons
        self._select_all_btn.clicked.connect(lambda: self.set_all(True))
        self._deselect_all_btn.clicked.connect(lambda: self.set_all(False))

    def selected(self) -> set[str]:
        """Return the set of module names whose checkboxes are checked."""
        return {
            name for name, cb in self._checkboxes.items() if cb.isChecked()
        }

    def set_all(self, state: bool) -> None:
        """Set all checkboxes to the given state (True=checked, False=unchecked)."""
        for cb in self._checkboxes.values():
            cb.setChecked(state)


# ---------------------------------------------------------------------------
# Qt Widgets — ExportView
# ---------------------------------------------------------------------------


class ExportView(QWidget):
    """Export configuration panel.

    Provides controls for:
    - Output directory selection (Browse button + read-only path label, default = Desktop)
    - Snapshot name entry (QLineEdit, maxLength=255)
    - Module selection (ModuleSelector instance)
    - Show_All checkbox (default unchecked)

    The ``build_config()`` method assembles the current widget state into an
    ``ExportConfig`` for use by the export worker.

    Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 3.1, 3.4, 4.1, 4.2
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # --- Output directory section ---
        dir_label = QLabel("Output directory:", self)
        layout.addWidget(dir_label)

        dir_row = QHBoxLayout()
        self._output_dir = Path.home() / "Desktop"
        self._path_label = QLabel(str(self._output_dir), self)
        self._path_label.setWordWrap(True)
        dir_row.addWidget(self._path_label, stretch=1)

        self._browse_btn = QPushButton("Browse...", self)
        self._browse_btn.clicked.connect(self._choose_directory)
        dir_row.addWidget(self._browse_btn)

        layout.addLayout(dir_row)

        # --- Snapshot name section ---
        name_label = QLabel("Snapshot name:", self)
        layout.addWidget(name_label)

        self._name_edit = QLineEdit(self)
        self._name_edit.setMaxLength(255)
        self._name_edit.setPlaceholderText("Leave empty for auto-generated name")
        layout.addWidget(self._name_edit)

        # --- Module selector ---
        self._module_selector = ModuleSelector(self)
        layout.addWidget(self._module_selector)

        # --- Show all checkbox ---
        self._show_all_cb = QCheckBox("Show all apps", self)
        self._show_all_cb.setChecked(False)
        layout.addWidget(self._show_all_cb)

    def _choose_directory(self) -> None:
        """Open a directory chooser dialog. If cancelled, path stays unchanged (Req 2.6)."""
        chosen = QFileDialog.getExistingDirectory(
            self, "Select output directory", str(self._output_dir)
        )
        if chosen:
            self._output_dir = Path(chosen)
            self._path_label.setText(str(self._output_dir))

    def build_config(self) -> ExportConfig:
        """Assemble the current widget state into an ExportConfig."""
        name_text = self._name_edit.text().strip()
        return ExportConfig(
            output_dir=self._output_dir,
            name=name_text if name_text else None,
            show_all=self._show_all_cb.isChecked(),
            selected_modules=self._module_selector.selected(),
        )


# ---------------------------------------------------------------------------
# Qt Widgets — RestoreView
# ---------------------------------------------------------------------------


class RestoreView(QWidget):
    """Restore configuration panel.

    Provides controls for:
    - Snapshot file selection (Browse button + path label, filter *.winsnap)
    - Module selection (ModuleSelector instance)
    - Dry_Run checkbox (default unchecked)
    - Verify after restore checkbox (default unchecked; disabled and
      unchecked while Dry run is checked, matching the CLI's
      dry-run-bypasses-verify semantics)

    The ``build_config()`` method assembles the current widget state into a
    ``RestoreConfig`` for use by the restore worker.

    Requirements: 3.1, 3.5, 8.1, 8.5, 8.6, 8.7, 9.1, 9.2, 9.4, 9.5
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._snapshot_path: Path | None = None

        layout = QVBoxLayout(self)

        # --- Snapshot file section ---
        file_label = QLabel("Snapshot file:", self)
        layout.addWidget(file_label)

        file_row = QHBoxLayout()
        self._path_label = QLabel("No snapshot file selected", self)
        self._path_label.setWordWrap(True)
        file_row.addWidget(self._path_label, stretch=1)

        self._browse_btn = QPushButton("Browse...", self)
        self._browse_btn.clicked.connect(self._choose_snapshot)
        file_row.addWidget(self._browse_btn)

        layout.addLayout(file_row)

        # --- Module selector ---
        self._module_selector = ModuleSelector(self)
        layout.addWidget(self._module_selector)

        # --- Dry run checkbox ---
        self._dry_run_cb = QCheckBox("Dry run", self)
        self._dry_run_cb.setChecked(False)
        layout.addWidget(self._dry_run_cb)

        # --- Verify after restore checkbox ---
        self._verify_cb = QCheckBox("Verify after restore", self)
        self._verify_cb.setChecked(False)
        layout.addWidget(self._verify_cb)

        # Dry run bypasses verify, matching the CLI's dry-run-bypasses-verify
        # semantics: checking "Dry run" disables and unchecks "Verify";
        # unchecking "Dry run" re-enables "Verify" without forcing it back on.
        self._dry_run_cb.toggled.connect(self._on_dry_run_toggled)

    def _on_dry_run_toggled(self, checked: bool) -> None:
        """Disable and uncheck Verify while Dry run is checked (Req 3.5)."""
        if checked:
            self._verify_cb.setChecked(False)
        self._verify_cb.setEnabled(not checked)

    def _choose_snapshot(self) -> None:
        """Open a file chooser for *.winsnap files. If cancelled, path stays unchanged (Req 8.7)."""
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select snapshot file",
            "",
            "WinSnap files (*.winsnap)",
        )
        if chosen:
            self._snapshot_path = Path(chosen)
            self._path_label.setText(str(self._snapshot_path))

    def build_config(self) -> RestoreConfig:
        """Assemble the current widget state into a RestoreConfig."""
        return RestoreConfig(
            snapshot_path=self._snapshot_path if self._snapshot_path is not None else Path(""),
            dry_run=self._dry_run_cb.isChecked(),
            selected_modules=self._module_selector.selected(),
            verify=self._verify_cb.isChecked(),
        )


# ---------------------------------------------------------------------------
# Qt widgets — RunningIndicator
# ---------------------------------------------------------------------------


class RunningIndicator(QWidget):
    """An indeterminate progress bar shown while an Operation runs.

    The widget is hidden by default. Call ``start()`` to show the pulsing
    indicator and ``stop()`` to hide it again.

    Requirement 15.5: display a running indicator while an Operation runs.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._progress_bar = QProgressBar(self)
        # min == max == 0 makes the bar indeterminate (pulsing)
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(0)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._progress_bar)

        # Hidden by default until an operation starts
        self.setVisible(False)

    def start(self) -> None:
        """Show the running indicator (operation started)."""
        self.setVisible(True)

    def stop(self) -> None:
        """Hide the running indicator (operation completed)."""
        self.setVisible(False)


# ---------------------------------------------------------------------------
# Qt Widgets — AppSelectorDialog
# ---------------------------------------------------------------------------


class AppSelectorDialog(QDialog):
    """A dialog for selecting which winget and manual apps to include in export.

    Displays two groups ("Winget apps" and "Manual apps"), each with a scrollable
    list of checkable entries and per-group "Select all" / "Deselect all" buttons.
    All entries are preselected on open. Empty groups render with no entries but
    the dialog is still confirmable/cancelable.

    Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.7
    """

    def __init__(
        self,
        winget: list[dict],
        manual: list[dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("App Selection")

        self._winget = winget
        self._manual = manual
        self._winget_checkboxes: list[QCheckBox] = []
        self._manual_checkboxes: list[QCheckBox] = []

        main_layout = QVBoxLayout(self)

        # --- Winget apps group ---
        winget_group = QGroupBox("Winget apps", self)
        winget_layout = QVBoxLayout(winget_group)

        # Scroll area for winget checkboxes
        winget_scroll = QScrollArea(self)
        winget_scroll.setWidgetResizable(True)
        winget_scroll_content = QWidget()
        winget_scroll_layout = QVBoxLayout(winget_scroll_content)

        for app in winget:
            label = app.get("name") or app.get("PackageIdentifier", "")
            cb = QCheckBox(label, self)
            cb.setChecked(True)
            self._winget_checkboxes.append(cb)
            winget_scroll_layout.addWidget(cb)

        winget_scroll_layout.addStretch()
        winget_scroll.setWidget(winget_scroll_content)
        winget_layout.addWidget(winget_scroll)

        # Per-group buttons for winget
        winget_btn_layout = QHBoxLayout()
        winget_select_all = QPushButton("Select all", self)
        winget_deselect_all = QPushButton("Deselect all", self)
        winget_btn_layout.addWidget(winget_select_all)
        winget_btn_layout.addWidget(winget_deselect_all)
        winget_layout.addLayout(winget_btn_layout)

        winget_select_all.clicked.connect(
            lambda: self._set_group(self._winget_checkboxes, True)
        )
        winget_deselect_all.clicked.connect(
            lambda: self._set_group(self._winget_checkboxes, False)
        )

        main_layout.addWidget(winget_group)

        # --- Manual apps group ---
        manual_group = QGroupBox("Manual apps", self)
        manual_layout = QVBoxLayout(manual_group)

        # Scroll area for manual checkboxes
        manual_scroll = QScrollArea(self)
        manual_scroll.setWidgetResizable(True)
        manual_scroll_content = QWidget()
        manual_scroll_layout = QVBoxLayout(manual_scroll_content)

        for app in manual:
            cb = QCheckBox(app.get("name", ""), self)
            cb.setChecked(True)
            self._manual_checkboxes.append(cb)
            manual_scroll_layout.addWidget(cb)

        manual_scroll_layout.addStretch()
        manual_scroll.setWidget(manual_scroll_content)
        manual_layout.addWidget(manual_scroll)

        # Per-group buttons for manual
        manual_btn_layout = QHBoxLayout()
        manual_select_all = QPushButton("Select all", self)
        manual_deselect_all = QPushButton("Deselect all", self)
        manual_btn_layout.addWidget(manual_select_all)
        manual_btn_layout.addWidget(manual_deselect_all)
        manual_layout.addLayout(manual_btn_layout)

        manual_select_all.clicked.connect(
            lambda: self._set_group(self._manual_checkboxes, True)
        )
        manual_deselect_all.clicked.connect(
            lambda: self._set_group(self._manual_checkboxes, False)
        )

        main_layout.addWidget(manual_group)

        # --- OK / Cancel buttons ---
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def _set_group(self, checkboxes: list[QCheckBox], state: bool) -> None:
        """Set all checkboxes in a group to the given state."""
        for cb in checkboxes:
            cb.setChecked(state)

    def result_selection(self) -> tuple[list[dict], list[dict]] | None:
        """Return the selection result after the dialog has closed.

        If the dialog was accepted (OK): returns a tuple of
        (selected_winget, selected_manual) using record_app_selection.
        If the dialog was rejected (Cancel): returns None.
        """
        if self.result() != QDialog.DialogCode.Accepted:
            return None

        winget_states = [cb.isChecked() for cb in self._winget_checkboxes]
        manual_states = [cb.isChecked() for cb in self._manual_checkboxes]

        return record_app_selection(
            winget_states=winget_states,
            manual_states=manual_states,
            winget=self._winget,
            manual=self._manual,
            confirmed=True,
        )


# ---------------------------------------------------------------------------
# Qt Widgets — ResultsView
# ---------------------------------------------------------------------------


class ResultsView(QWidget):
    """Displays a per-run results summary grouped by Passed/Failed/Skipped.

    Contains:
    - A counts header label showing "Passed: X | Failed: Y | Skipped: Z"
    - Three QGroupBox sections: "Passed", "Failed", "Skipped"
    - Each group contains a QVBoxLayout with QLabel rows for each module
    - Failed rows: "module_name \u2014 error message"
    - Skipped rows: "module_name \u2014 reason"
    - Passed rows: just "module_name"

    Initially hidden until the first summary is shown via ``show_summary()``.

    Requirements: 14.1, 14.2, 14.3, 14.7
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._layout = QVBoxLayout(self)

        # Counts header
        self._counts_label = QLabel("", self)
        self._layout.addWidget(self._counts_label)

        # Passed group
        self._passed_group = QGroupBox("Passed", self)
        self._passed_layout = QVBoxLayout(self._passed_group)
        self._layout.addWidget(self._passed_group)

        # Failed group
        self._failed_group = QGroupBox("Failed", self)
        self._failed_layout = QVBoxLayout(self._failed_group)
        self._layout.addWidget(self._failed_group)

        # Skipped group
        self._skipped_group = QGroupBox("Skipped", self)
        self._skipped_layout = QVBoxLayout(self._skipped_group)
        self._layout.addWidget(self._skipped_group)

        # Initially hidden until first summary is shown
        self.setVisible(False)

    def show_summary(self, summary: ResultsSummary) -> None:
        """Clear previous content and populate groups from the summary.

        Updates the counts header and adds per-module rows to each group.
        Makes the widget visible.
        """
        # Clear previous rows from all groups
        self._clear_layout(self._passed_layout)
        self._clear_layout(self._failed_layout)
        self._clear_layout(self._skipped_layout)

        # Update counts header
        passed_count, failed_count, skipped_count = summary.counts()
        self._counts_label.setText(
            f"Passed: {passed_count} | Failed: {failed_count} | Skipped: {skipped_count}"
        )

        # Populate Passed group
        for outcome in summary.passed():
            label = QLabel(outcome.name, self)
            self._passed_layout.addWidget(label)

        # Populate Failed group — show "module_name — error message"
        for outcome in summary.failed():
            text = f"{outcome.name} \u2014 {outcome.detail}" if outcome.detail else outcome.name
            label = QLabel(text, self)
            self._failed_layout.addWidget(label)

        # Populate Skipped group — show "module_name — reason"
        for outcome in summary.skipped():
            text = f"{outcome.name} \u2014 {outcome.detail}" if outcome.detail else outcome.name
            label = QLabel(text, self)
            self._skipped_layout.addWidget(label)

        # Make visible
        self.setVisible(True)

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        """Remove all widgets from a layout."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


# ---------------------------------------------------------------------------
# Qt Widgets — LogPanel
# ---------------------------------------------------------------------------

# Color mapping for log entry severity (Requirements 12.1, 12.2, 12.3)
_SEVERITY_COLORS: dict[Severity, str] = {
    Severity.SUCCESS: "green",
    Severity.WARNING: "#FFC107",  # amber
    Severity.ERROR: "red",
}


class LogPanel(QWidget):
    """A persistent, color-coded log panel with clear and copy controls.

    Displays timestamped log entries in a read-only QTextEdit with per-line
    color based on severity. Provides "Clear" and "Copy" buttons.

    Requirements: 11.1, 11.2, 11.3, 11.4, 12.1, 12.2, 12.3, 13.1, 13.2, 13.3, 13.4
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._entries: list[LogEntry] = []

        layout = QVBoxLayout(self)

        # Read-only rich-text display
        self._text_edit = QTextEdit(self)
        self._text_edit.setReadOnly(True)
        layout.addWidget(self._text_edit)

        # Button row: Clear / Copy
        button_layout = QHBoxLayout()
        self._clear_btn = QPushButton("Clear", self)
        self._copy_btn = QPushButton("Copy", self)
        button_layout.addWidget(self._clear_btn)
        button_layout.addWidget(self._copy_btn)
        layout.addLayout(button_layout)

        # Connect buttons
        self._clear_btn.clicked.connect(self.clear)
        self._copy_btn.clicked.connect(self.copy)

    def append(self, entry: LogEntry) -> None:
        """Append a log entry with severity-based color and auto-scroll.

        Formats the entry using format_log_line, wraps it in an HTML span
        with the appropriate color, appends to the QTextEdit, and scrolls
        to the bottom to keep the newest entry visible (Requirement 11.4).
        """
        self._entries.append(entry)

        color = _SEVERITY_COLORS[entry.severity]
        text = format_log_line(entry)
        # Escape HTML special characters in the message text
        escaped = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        html = f'<span style="color: {color};">{escaped}</span>'
        self._text_edit.append(html)

        # Auto-scroll to the bottom
        scrollbar = self._text_edit.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def clear(self) -> None:
        """Remove all log entries from the panel (Requirement 13.2)."""
        self._entries.clear()
        self._text_edit.clear()

    def copy(self) -> None:
        """Copy the plain text of all log entries to the system clipboard (Requirement 13.3, 13.4)."""
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.plain_text())

    def plain_text(self) -> str:
        """Return the newline-joined formatted text of all entries.

        Returns an empty string if no entries are present (Requirement 13.4).
        """
        if not self._entries:
            return ""
        return "\n".join(format_log_line(entry) for entry in self._entries)


# ---------------------------------------------------------------------------
# Workers and threading
# ---------------------------------------------------------------------------


class LogStream(QObject):
    """A file-like object that splits incoming text into lines and emits a signal per complete line.

    Used with contextlib.redirect_stdout to capture module print() output
    during an Operation and route it through classify_severity into the LogPanel.

    Each complete line (terminated by a newline) triggers the log_line signal
    with the line text and its classified severity. Partial lines are buffered
    until a newline arrives or flush() is called.

    Requirements: 11.2, 12.4, 12.5, 12.6
    """

    log_line = pyqtSignal(str, Severity)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._buffer: str = ""

    def write(self, text: str) -> int:
        """Buffer incoming text, emit log_line signal for each complete line.

        Splits text on newlines. Complete lines (those followed by a newline)
        are emitted immediately via the log_line signal with their classified
        severity. Any trailing partial line (no terminating newline) remains
        in the buffer until more text arrives or flush() is called.

        Returns len(text) for file-like interface compatibility.
        """
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.log_line.emit(line, classify_severity(line))
        return len(text)

    def flush(self) -> None:
        """Emit any remaining buffered text as a final line and clear the buffer.

        If the buffer is non-empty (text without a trailing newline), it is
        emitted as a complete line via the log_line signal. If the buffer is
        empty, this is a no-op.
        """
        if self._buffer:
            self.log_line.emit(self._buffer, classify_severity(self._buffer))
            self._buffer = ""


# ---------------------------------------------------------------------------
# Workers and threading — AppSelectionBridge
# ---------------------------------------------------------------------------


class AppSelectionBridge(QObject):
    """Cross-thread bridge between the Worker thread and the UI thread for app selection.

    The Worker thread calls ``request_app_selection(winget, manual)`` which emits
    a signal to the UI thread and blocks on a ``threading.Event``. The UI thread
    shows the ``AppSelectorDialog``, collects the result, and calls
    ``provide_result(selection)`` which stores the result and releases the event
    so the Worker can resume.

    This ensures the UI thread is never blocked (the window stays responsive)
    while the Worker waits for user input.

    Requirements: 5.1, 5.6, 15.2
    """

    # Signal emitted to the UI thread with (winget_apps, manual_apps)
    app_selection_requested = pyqtSignal(list, list)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._event = threading.Event()
        self._result: tuple[list[dict], list[dict]] | None = None

    def request_app_selection(
        self, winget: list[dict], manual: list[dict]
    ) -> tuple[list[dict], list[dict]] | None:
        """Request app selection from the UI thread (called on the Worker thread).

        Emits ``app_selection_requested`` to the UI thread, then blocks the
        Worker thread on a ``threading.Event`` until ``provide_result`` is called.

        Args:
            winget: List of discovered winget app dicts.
            manual: List of discovered manual app dicts.

        Returns:
            The selection result provided by the UI thread:
            a tuple of (selected_winget, selected_manual) or None if cancelled.
        """
        self._result = None
        self._event.clear()
        self.app_selection_requested.emit(winget, manual)
        self._event.wait()
        return self._result

    def provide_result(
        self, selection: tuple[list[dict], list[dict]] | None
    ) -> None:
        """Store the selection result and release the Worker thread (called on the UI thread).

        Args:
            selection: The user's selection from the AppSelectorDialog —
                       a tuple of (selected_winget, selected_manual) or None if cancelled.
        """
        self._result = selection
        self._event.set()


# ---------------------------------------------------------------------------
# Workers and threading — ExportWorker
# ---------------------------------------------------------------------------


class ExportWorker(QObject):
    """Background worker that executes an export Operation on a separate thread.

    Given an ``ExportConfig`` and an ``AppSelectionBridge``, the worker:
    1. Resolves the run set via ``resolve_run_modules``.
    2. Checks admin status for the ``power`` module (emits warning if not admin).
    3. Creates the snapshot directory, applies the name, binds ``show_all``,
       and injects the App_Selector via the bridge.
    4. Runs each module, classifying outcomes with ``classify_export_outcome``.
    5. Writes ``snapshot.json``, zips via ``export.zip_snapshot``, cleans temp folder.
    6. Emits a success log with the archive path and format version.
    7. On fatal error: removes any partial archive, emits error, ends operation.
    8. Emits ``finished(ResultsSummary)`` signal.

    Requirements: 2.4, 3.2, 3.3, 4.3, 4.4, 5.6, 6.1, 6.2, 7.1, 7.2, 7.3, 7.4, 7.5
    """

    log = pyqtSignal(str, Severity)
    module_completed = pyqtSignal(ModuleOutcome)
    finished = pyqtSignal(ResultsSummary)
    running_changed = pyqtSignal(bool)

    def __init__(
        self, config: ExportConfig, bridge: AppSelectionBridge, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._bridge = bridge

    def _emit_log(self, message: str, severity: Severity) -> None:
        """Emit a log signal with the given message and severity."""
        self.log.emit(message, severity)

    def _is_admin(self) -> bool:
        """Check if the current process has Administrator privileges."""
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except (AttributeError, OSError):
            # Non-Windows or unavailable — assume not admin
            return False

    def run(self) -> None:
        """Execute the export operation (called on the Worker thread).

        This method orchestrates the full export pipeline: module resolution,
        admin check, snapshot directory creation, module execution, archive
        creation, and cleanup. All progress is communicated via signals.
        """
        import contextlib
        import importlib
        import json
        import os
        import shutil
        import stat

        self.running_changed.emit(True)
        summary = ResultsSummary()
        snapshot_dir: Path | None = None
        zip_path: Path | None = None

        try:
            # 1. Resolve run set
            run_modules = resolve_run_modules(
                self._config.selected_modules, manifest.MODULE_NAMES
            )

            # 2. Admin check for power module
            if "power" in run_modules and not self._is_admin():
                self._emit_log(
                    "Warning: power plan capture will be skipped because "
                    "Administrator privileges are not held.",
                    Severity.WARNING,
                )

            # 3. Generate snapshot name
            name = self._config.name
            if name is None:
                name = default_snapshot_name(datetime.now())

            # 4. Create snapshot directory
            import export as export_module

            snapshot_dir = export_module.create_snapshot_dir(self._config.output_dir)

            # Apply custom name by renaming the directory
            if self._config.name is not None:
                named = snapshot_dir.parent / self._config.name
                snapshot_dir.rename(named)
                snapshot_dir = named

            # 5. Inject the AppSelectionBridge as the checklist replacement
            import modules.checklist as checklist_module
            original_checklist_run = checklist_module.run
            checklist_module.run = self._bridge.request_app_selection

            # 6. Prepare the stdout redirect to capture module print output
            log_stream = LogStream()
            log_stream.log_line.connect(self._emit_log)

            # Build snapshot metadata
            snapshot_data: dict = {
                "winsnap_version": export_module.SNAPSHOT_FORMAT_VERSION,
                "snapshot_format_version": export_module.SNAPSHOT_FORMAT_VERSION,
                "exported_at": datetime.now().isoformat(),
                "exported_on": {
                    "user": os.environ.get("USERNAME", ""),
                    "machine": os.environ.get("COMPUTERNAME", ""),
                },
                "modules_attempted": run_modules,
                "modules": {},
            }

            # 7. Run each module
            for mod_name in run_modules:
                try:
                    mod = importlib.import_module(f"modules.{mod_name}")
                    export_fn = mod.export

                    # Bind show_all for apps module
                    if mod_name == "apps":
                        original_export = export_fn
                        export_fn = lambda d, _fn=original_export: _fn(d, show_all=self._config.show_all)

                    with contextlib.redirect_stdout(log_stream):
                        result = export_fn(snapshot_dir)

                    # Flush any remaining buffered output
                    log_stream.flush()

                    snapshot_data["modules"][mod_name] = result
                    outcome = classify_export_outcome(
                        mod_name, raised=None, result=result
                    )
                except Exception as e:
                    log_stream.flush()
                    snapshot_data["modules"][mod_name] = {"error": str(e)}
                    outcome = classify_export_outcome(
                        mod_name, raised=e, result=None
                    )

                summary.add(outcome)
                self.module_completed.emit(outcome)

            # 8. Restore original checklist.run
            checklist_module.run = original_checklist_run

            # 9. Write snapshot.json
            json_path = snapshot_dir / "snapshot.json"
            json_path.write_text(
                json.dumps(snapshot_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            # 10. Zip the snapshot
            zip_path = export_module.zip_snapshot(snapshot_dir)

            # 11. Clean up the temp snapshot folder
            def _force_remove(func, path, _):
                """If rmtree hits a permission error, chmod and retry."""
                os.chmod(path, stat.S_IWRITE)
                func(path)

            try:
                shutil.rmtree(snapshot_dir, onexc=_force_remove)
            except Exception:
                pass  # Non-fatal: temp folder cleanup failure

            # 12. Emit success logs
            self._emit_log(
                f"Snapshot saved to: {zip_path}",
                Severity.SUCCESS,
            )
            self._emit_log(
                f"Format version: {export_module.SNAPSHOT_FORMAT_VERSION}",
                Severity.SUCCESS,
            )

        except Exception as e:
            # Fatal error: clean up partial archive and snapshot dir
            self._emit_log(
                f"Fatal export error: {e}",
                Severity.ERROR,
            )

            # Remove partial archive if it exists
            if zip_path is not None and zip_path.exists():
                try:
                    zip_path.unlink()
                except OSError:
                    pass

            # Remove snapshot directory if it exists
            if snapshot_dir is not None and snapshot_dir.exists():
                try:
                    shutil.rmtree(snapshot_dir, ignore_errors=True)
                except Exception:
                    pass

        finally:
            self.finished.emit(summary)
            self.running_changed.emit(False)


# ---------------------------------------------------------------------------
# Workers and threading — RestoreWorker
# ---------------------------------------------------------------------------


class RestoreWorker(QObject):
    """Background worker that executes a restore operation.

    Given a ``RestoreConfig``, this worker:
    1. Opens the .winsnap archive and extracts to a temp directory
    2. Reads snapshot.json
    3. Evaluates the format version and logs date+version info
    4. Halts on INCOMPATIBLE version; warns and continues on UNPARSEABLE
    5. Resolves the run set in MODULES_RESTORE_ORDER
    6. For each module: classifies outcome, runs restore or emits dry-run summary
    7. Emits finished(ResultsSummary) when complete

    Signals:
        log(str, Severity): Emitted for each log message with its severity.
        module_completed(ModuleOutcome): Emitted after each module is processed.
        finished(ResultsSummary): Emitted when the operation completes.
        running_changed(bool): Emitted at start (True) and end (False).

    Requirements: 8.2, 8.3, 8.4, 9.3, 9.6, 9.7, 10.1, 10.2, 10.3, 10.4, 10.5, 14.6
    """

    log = pyqtSignal(str, Severity)
    module_completed = pyqtSignal(ModuleOutcome)
    finished = pyqtSignal(ResultsSummary)
    running_changed = pyqtSignal(bool)

    def __init__(self, config: RestoreConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config

    def run(self) -> None:
        """Execute the restore operation (called on the worker thread)."""
        self.running_changed.emit(True)
        summary = ResultsSummary()

        try:
            self._do_restore(summary)
        except Exception as e:
            self.log.emit(f"Unexpected error during restore: {e}", Severity.ERROR)
        finally:
            self.finished.emit(summary)
            self.running_changed.emit(False)

    def _do_restore(self, summary: ResultsSummary) -> None:
        """Internal restore logic, separated for clean exception handling."""
        import restore as _restore

        snapshot_path = self._config.snapshot_path

        # --- Validate and open archive ---
        if not snapshot_path.exists():
            self.log.emit(
                f"File not found: {snapshot_path}", Severity.ERROR
            )
            return

        try:
            zf = zipfile.ZipFile(snapshot_path, "r")
        except (zipfile.BadZipFile, OSError) as e:
            self.log.emit(
                f"Not a valid snapshot archive: {snapshot_path} ({e})",
                Severity.ERROR,
            )
            return

        # --- Extract to temp directory ---
        tmp_dir = Path(tempfile.mkdtemp(prefix="winsnap_restore_"))
        try:
            with zf:
                zf.extractall(tmp_dir)

            # Find the snapshot directory (top-level folder inside the zip)
            extracted_dirs = [d for d in tmp_dir.iterdir() if d.is_dir()]
            if not extracted_dirs:
                self.log.emit(
                    "Snapshot archive appears empty.", Severity.ERROR
                )
                return
            snapshot_dir = extracted_dirs[0]

            # --- Read snapshot.json ---
            json_path = snapshot_dir / "snapshot.json"
            if not json_path.exists():
                self.log.emit(
                    "snapshot.json not found in archive.", Severity.ERROR
                )
                return

            snapshot = json.loads(json_path.read_text(encoding="utf-8"))

            # --- Evaluate format version (Requirement 10.1, 10.2, 10.3, 10.4, 10.5) ---
            raw_version = snapshot.get("snapshot_format_version")
            verdict, _parsed_major = evaluate_version(
                raw_version, _restore.SUPPORTED_MAJOR
            )

            # Log version info with "unknown" placeholders (Requirement 10.3)
            version_msg = format_version_info_message(snapshot)
            self.log.emit(version_msg, Severity.SUCCESS)

            # Halt on INCOMPATIBLE (Requirement 10.2)
            if verdict == VersionVerdict.INCOMPATIBLE:
                self.log.emit(
                    f"Snapshot format version {raw_version} is newer than this "
                    f"restorer supports (v{_restore.SUPPORTED_MAJOR}.x). "
                    f"Update WinSnap and try again.",
                    Severity.ERROR,
                )
                return

            # Warn on UNPARSEABLE (Requirement 10.5)
            if verdict == VersionVerdict.UNPARSEABLE:
                self.log.emit(
                    f"Unrecognized snapshot version format: {raw_version!r}. "
                    f"Attempting restore anyway.",
                    Severity.WARNING,
                )

            # --- Resolve run set (Requirement 9.3) ---
            run_modules = resolve_run_modules(
                self._config.selected_modules, manifest.MODULE_NAMES
            )

            # Build a lookup from module key to module object
            module_map: dict[str, object] = {
                key: mod for key, mod in _restore.ALL_MODULES
            }

            # Get modules data from snapshot
            modules_data = snapshot.get("modules", {})

            # --- Process each module in restore order ---
            for name in MODULES_RESTORE_ORDER:
                selected = name in self._config.selected_modules
                present = name in modules_data
                export_errored = False

                if present and isinstance(modules_data.get(name), dict):
                    export_errored = "error" in modules_data[name]

                # Determine if we should skip this module
                if not selected or not present or export_errored:
                    outcome = classify_restore_outcome(
                        name,
                        selected=selected,
                        present=present,
                        export_errored=export_errored,
                        raised=None,
                    )
                    summary.add(outcome)
                    self.module_completed.emit(outcome)
                    continue

                # Module is selected, present, and not export-errored
                if self._config.dry_run:
                    # Dry-run: emit summary text, apply no changes (Req 9.6, 9.7)
                    dry_text = _restore._summarize(name, modules_data[name])
                    self.log.emit(
                        f"[{name}] {dry_text}", Severity.SUCCESS
                    )
                    outcome = classify_restore_outcome(
                        name,
                        selected=True,
                        present=True,
                        export_errored=False,
                        raised=None,
                    )
                    summary.add(outcome)
                    self.module_completed.emit(outcome)
                    continue

                # Actually run the module's restore function
                mod = module_map.get(name)
                raised: Exception | None = None

                if mod is not None:
                    log_stream = LogStream()
                    log_stream.log_line.connect(self.log.emit)
                    try:
                        with contextlib.redirect_stdout(log_stream):
                            mod.restore(modules_data[name], snapshot_dir)  # type: ignore[union-attr]
                    except Exception as e:
                        raised = e
                        self.log.emit(
                            f"[{name}] ERROR during restore: {e}",
                            Severity.ERROR,
                        )
                    finally:
                        log_stream.flush()

                outcome = classify_restore_outcome(
                    name,
                    selected=True,
                    present=True,
                    export_errored=False,
                    raised=raised,
                )
                summary.add(outcome)
                self.module_completed.emit(outcome)

        finally:
            # Clean up temp directory
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Qt Widgets — MainWindow
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """Main application window for WinSnap GUI.

    Layout:
    - A view-switching QComboBox to toggle between Export and Restore views
    - A QStackedWidget containing ExportView and RestoreView
    - A persistent action bar with "Start Export", "Start Restore" buttons
      and a RunningIndicator (always visible regardless of active view)
    - A shared LogPanel (always visible below the views)
    - A shared ResultsView (always visible below the log)

    State:
    - ``_operation_in_progress``: bool flag indicating whether an Operation
      is currently running. Used by pre-start guards (task 7.2) to prevent
      concurrent operations.

    The two start buttons are always visible and enabled (Req 15.3),
    regardless of which view is shown. The view switcher toggles which
    view's options are displayed, but both start buttons remain accessible.

    Requirements: 1.1, 1.2, 1.3, 15.3, 15.4, 15.5
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("WinSnap")

        # --- State ---
        self._operation_in_progress: bool = False

        # --- Central widget and main layout ---
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- View switcher (QComboBox) ---
        self._view_switcher = QComboBox(self)
        self._view_switcher.addItem("Export")
        self._view_switcher.addItem("Restore")
        main_layout.addWidget(self._view_switcher)

        # --- QStackedWidget for Export/Restore views ---
        self._stacked_widget = QStackedWidget(self)
        self._export_view = ExportView(self)
        self._restore_view = RestoreView(self)
        self._stacked_widget.addWidget(self._export_view)
        self._stacked_widget.addWidget(self._restore_view)
        main_layout.addWidget(self._stacked_widget)

        # Connect view switcher to stacked widget
        self._view_switcher.currentIndexChanged.connect(
            self._stacked_widget.setCurrentIndex
        )

        # --- Persistent action bar ---
        action_bar = QHBoxLayout()
        self._start_export_btn = QPushButton("Start Export", self)
        self._start_restore_btn = QPushButton("Start Restore", self)
        self._running_indicator = RunningIndicator(self)

        action_bar.addWidget(self._start_export_btn)
        action_bar.addWidget(self._start_restore_btn)
        action_bar.addWidget(self._running_indicator)
        main_layout.addLayout(action_bar)

        # --- Shared LogPanel ---
        self._log_panel = LogPanel(self)
        main_layout.addWidget(self._log_panel)

        # --- Shared ResultsView ---
        self._results_view = ResultsView(self)
        main_layout.addWidget(self._results_view)

        # --- Worker thread references (kept alive during operation) ---
        self._worker: ExportWorker | RestoreWorker | None = None
        self._worker_thread: QThread | None = None
        self._bridge: AppSelectionBridge | None = None

        # --- Connect start buttons ---
        self._start_export_btn.clicked.connect(self._start_export)
        self._start_restore_btn.clicked.connect(self._start_restore)

    def try_start_export(self) -> bool:
        """Validate pre-start guards for an export operation.

        Checks (in order):
        1. No operation already in progress (Req 1.4)
        2. Running on Windows (Req 1.5)
        3. Snapshot name is valid if provided (Req 2.7)
        4. At least one module is selected (Req 3.8)

        Emits appropriate error/warning log entries and returns False on
        guard failure. Returns True if all guards pass.
        """
        # Guard: operation already in progress (Req 1.4)
        if self._operation_in_progress:
            self._log_panel.append(LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                message="An operation is already in progress",
                severity=Severity.WARNING,
            ))
            return False

        # Guard: Windows host check (Req 1.5)
        if sys.platform != "win32":
            self._log_panel.append(LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                message="WinSnap runs on Windows only",
                severity=Severity.ERROR,
            ))
            return False

        # Build config from ExportView
        config = self._export_view.build_config()

        # Guard: validate snapshot name if provided (Req 2.7)
        if config.name is not None:
            validation_error = validate_snapshot_name(config.name)
            if validation_error is not None:
                self._log_panel.append(LogEntry(
                    timestamp=datetime.now().strftime("%H:%M:%S"),
                    message=validation_error,
                    severity=Severity.ERROR,
                ))
                return False

        # Guard: at least one module selected (Req 3.8)
        if not config.selected_modules:
            self._log_panel.append(LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                message="At least one module must be selected",
                severity=Severity.ERROR,
            ))
            return False

        return True

    def try_start_restore(self) -> bool:
        """Validate pre-start guards for a restore operation.

        Checks (in order):
        1. No operation already in progress (Req 1.4)
        2. Running on Windows (Req 1.5)
        3. A snapshot file is selected (Req 8.2)
        4. The snapshot file exists (Req 8.3)
        5. The snapshot file is a valid archive (Req 8.4)
        6. At least one module is selected (Req 3.8)

        Emits appropriate error/warning log entries and returns False on
        guard failure. Returns True if all guards pass.
        """
        # Guard: operation already in progress (Req 1.4)
        if self._operation_in_progress:
            self._log_panel.append(LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                message="An operation is already in progress",
                severity=Severity.WARNING,
            ))
            return False

        # Guard: Windows host check (Req 1.5)
        if sys.platform != "win32":
            self._log_panel.append(LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                message="WinSnap runs on Windows only",
                severity=Severity.ERROR,
            ))
            return False

        # Build config from RestoreView
        config = self._restore_view.build_config()

        # Guard: snapshot file selected (Req 8.2)
        if not config.snapshot_path or str(config.snapshot_path) in ("", "."):
            self._log_panel.append(LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                message="A snapshot file must be selected",
                severity=Severity.ERROR,
            ))
            return False

        # Guard: snapshot file exists (Req 8.3)
        if not config.snapshot_path.exists():
            self._log_panel.append(LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                message=f"File not found: {config.snapshot_path}",
                severity=Severity.ERROR,
            ))
            return False

        # Guard: valid archive (Req 8.4)
        try:
            with zipfile.ZipFile(config.snapshot_path, "r") as zf:
                zf.testzip()
        except (zipfile.BadZipFile, OSError):
            self._log_panel.append(LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                message=f"Not a valid snapshot: {config.snapshot_path}",
                severity=Severity.ERROR,
            ))
            return False

        # Guard: at least one module selected (Req 3.8)
        if not config.selected_modules:
            self._log_panel.append(LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                message="At least one module must be selected",
                severity=Severity.ERROR,
            ))
            return False

        return True

    # ------------------------------------------------------------------
    # Operation lifecycle methods
    # ------------------------------------------------------------------

    def _start_export(self) -> None:
        """Start an export operation (called when 'Start Export' button is clicked).

        Validates preconditions via try_start_export(), then:
        - Sets _operation_in_progress = True
        - Builds ExportConfig from ExportView
        - Creates AppSelectionBridge
        - Creates ExportWorker with config and bridge
        - Creates QThread, moves worker to thread
        - Connects all signals
        - Starts the thread

        Requirements: 11.2, 14.1, 15.1, 15.2, 15.4, 15.5
        """
        if not self.try_start_export():
            return

        self._operation_in_progress = True

        # Build config from the export view
        config = self._export_view.build_config()

        # Create the bridge for app selection cross-thread communication
        self._bridge = AppSelectionBridge()

        # Create the worker
        self._worker = ExportWorker(config, self._bridge)

        # Create the thread and move worker to it
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        # Connect worker signals to UI slots
        self._worker.log.connect(self._on_log)
        self._worker.module_completed.connect(self._on_module_completed)
        self._worker.finished.connect(self._on_operation_finished)
        self._worker.running_changed.connect(self._on_running_changed)

        # Connect bridge signal for app selection dialog
        self._bridge.app_selection_requested.connect(self._on_app_selection_requested)

        # Connect thread lifecycle
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._worker_thread.quit)

        # Start the thread
        self._worker_thread.start()

    def _start_restore(self) -> None:
        """Start a restore operation (called when 'Start Restore' button is clicked).

        Validates preconditions via try_start_restore(), then:
        - Sets _operation_in_progress = True
        - Builds RestoreConfig from RestoreView
        - Creates RestoreWorker with config
        - Creates QThread, moves worker to thread
        - Connects all signals
        - Starts the thread

        Requirements: 11.2, 14.1, 15.1, 15.4, 15.5
        """
        if not self.try_start_restore():
            return

        self._operation_in_progress = True

        # Build config from the restore view
        config = self._restore_view.build_config()

        # Create the worker (no bridge needed for restore)
        self._worker = RestoreWorker(config)

        # Create the thread and move worker to it
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        # Connect worker signals to UI slots
        self._worker.log.connect(self._on_log)
        self._worker.module_completed.connect(self._on_module_completed)
        self._worker.finished.connect(self._on_operation_finished)
        self._worker.running_changed.connect(self._on_running_changed)

        # Connect thread lifecycle
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._worker_thread.quit)

        # Start the thread
        self._worker_thread.start()

    # ------------------------------------------------------------------
    # Signal handler slots
    # ------------------------------------------------------------------

    def _on_log(self, message: str, severity: Severity) -> None:
        """Handle a log signal from the worker.

        Creates a LogEntry with the current timestamp and appends it to the LogPanel.

        Requirements: 11.1, 11.2
        """
        entry = LogEntry(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            message=message,
            severity=severity,
        )
        self._log_panel.append(entry)

    def _on_module_completed(self, outcome: ModuleOutcome) -> None:
        """Handle a module_completed signal from the worker.

        Individual module completions are tracked by the worker's ResultsSummary.
        The ResultsView is updated all at once when the operation finishes.

        Requirements: 14.1
        """
        pass

    def _on_operation_finished(self, summary: ResultsSummary) -> None:
        """Handle the finished signal from the worker.

        Shows the ResultsSummary in the ResultsView, clears the running state,
        and re-enables controls for a new operation.

        Requirements: 14.1, 15.4
        """
        self._results_view.show_summary(summary)
        self._operation_in_progress = False

    def _on_running_changed(self, running: bool) -> None:
        """Handle the running_changed signal from the worker.

        Shows or hides the RunningIndicator based on the running state.

        Requirements: 15.5
        """
        if running:
            self._running_indicator.start()
        else:
            self._running_indicator.stop()

    def _on_app_selection_requested(self, winget: list, manual: list) -> None:
        """Handle the app_selection_requested signal from the AppSelectionBridge.

        Shows the AppSelectorDialog on the UI thread, collects the result,
        and provides it back to the bridge so the worker can resume.

        Requirements: 5.1, 15.2
        """
        dialog = AppSelectorDialog(winget, manual, self)
        dialog.exec()
        selection = dialog.result_selection()
        if self._bridge is not None:
            self._bridge.provide_result(selection)


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
