# Implementation Plan: WinSnap GUI

## Overview

Build `gui.py` in the project root — a PyQt6 desktop application wrapping WinSnap's export and restore CLIs. The implementation follows a layered architecture: pure core logic (value types + deterministic functions) first, then Qt widgets, then background workers, then integration wiring. Property-based tests validate the 13 correctness properties against the pure core; unit/integration tests cover widget state and worker behavior.

## Tasks

- [x] 1. Define core value types and pure functions
  - [x] 1.1 Create `gui.py` with core enums, dataclasses, and constants
    - Define `Severity`, `ModuleStatus`, `VersionVerdict` enums
    - Define `LogEntry`, `ModuleOutcome`, `ResultsSummary`, `ExportConfig`, `RestoreConfig` dataclasses
    - Define `MODULES_EXPORT_ORDER` and `MODULES_RESTORE_ORDER` lists mirroring `export.py` and `restore.ALL_MODULES`
    - _Requirements: 14.1, 14.7, 3.2, 9.3_

  - [x] 1.2 Implement snapshot name validation and default name generation
    - Implement `validate_snapshot_name(name: str) -> str | None` — reject empty, >255 chars, Windows-forbidden chars, reserved device names, trailing dot/space
    - Implement `default_snapshot_name(start: datetime) -> str` — produce `"winsnap_YYYYMMDD_HHMMSS"`
    - _Requirements: 2.3, 2.4, 2.7_

  - [x] 1.3 Implement severity classification and log formatting
    - Implement `classify_severity(line: str) -> Severity` — error markers → ERROR, warning/advisory markers → WARNING, else SUCCESS
    - Implement `format_log_line(entry: LogEntry) -> str` — prefix with `HH:MM:SS`
    - _Requirements: 12.4, 12.5, 12.6, 11.1_

  - [x] 1.4 Implement version evaluation
    - Implement `evaluate_version(raw: str | None, supported_major: int) -> tuple[VersionVerdict, int | None]`
    - INCOMPATIBLE when parsed MAJOR > supported, COMPATIBLE when ≤, UNPARSEABLE when parsing fails
    - _Requirements: 10.2, 10.4, 10.5_

  - [x] 1.5 Implement module run resolution
    - Implement `resolve_run_modules(selected: set[str], order: list[str]) -> list[str]`
    - Return selected modules in canonical order, no duplicates, no unselected
    - _Requirements: 3.2, 3.3, 9.3_

  - [x] 1.6 Implement outcome classification functions
    - Implement `classify_export_outcome(name, *, raised, result) -> ModuleOutcome`
    - Implement `classify_restore_outcome(name, *, selected, present, export_errored, raised) -> ModuleOutcome`
    - Follow the classification rules from the design (PASSED/FAILED/SKIPPED conditions)
    - _Requirements: 6.2, 7.3, 14.4, 14.5, 14.6_

  - [x] 1.7 Implement app-selection recording
    - Implement `record_app_selection(winget_states, manual_states, winget, manual, confirmed) -> tuple[list[dict], list[dict]]`
    - When confirmed, return entries whose mask is True; when cancelled, return `([], [])`
    - _Requirements: 5.2, 5.4, 5.5, 5.7_

  - [x] 1.8 Write property test: default snapshot name format (Property 1)
    - **Property 1: Default snapshot name format**
    - **Validates: Requirements 2.4**

  - [x] 1.9 Write property test: snapshot name validation (Property 2)
    - **Property 2: Snapshot name validation**
    - **Validates: Requirements 2.7, 2.3**

  - [x] 1.10 Write property test: module run resolution (Property 3)
    - **Property 3: Module run resolution**
    - **Validates: Requirements 3.2, 3.3, 9.3**

  - [x] 1.11 Write property test: select-all / deselect-all coverage (Property 4)
    - **Property 4: Select-all / deselect-all coverage**
    - **Validates: Requirements 3.6, 3.7**

  - [x] 1.12 Write property test: app-selection recording (Property 5)
    - **Property 5: App-selection recording**
    - **Validates: Requirements 5.2, 5.4, 5.5, 5.7**

  - [x] 1.13 Write property test: export outcome classification (Property 6)
    - **Property 6: Export outcome classification**
    - **Validates: Requirements 6.2, 7.3, 14.4, 14.5**

  - [x] 1.14 Write property test: restore outcome classification (Property 7)
    - **Property 7: Restore outcome classification**
    - **Validates: Requirements 14.6**

  - [x] 1.15 Write property test: snapshot version verdict (Property 8)
    - **Property 8: Snapshot version verdict**
    - **Validates: Requirements 10.2, 10.4, 10.5**

  - [x] 1.16 Write property test: version-info message placeholders (Property 9)
    - **Property 9: Version-info message placeholders**
    - **Validates: Requirements 10.3**

  - [x] 1.17 Write property test: severity classification is total and single-valued (Property 10)
    - **Property 10: Severity classification is total and single-valued**
    - **Validates: Requirements 12.4, 12.5, 12.6**

  - [x] 1.18 Write property test: log line timestamp prefix (Property 11)
    - **Property 11: Log line timestamp prefix**
    - **Validates: Requirements 11.1**

  - [x] 1.19 Write property test: log accumulation and copy text (Property 12)
    - **Property 12: Log accumulation and copy text**
    - **Validates: Requirements 11.3, 13.2, 13.3, 13.4**

  - [x] 1.20 Write property test: results summary partition and counts (Property 13)
    - **Property 13: Results summary partition and counts**
    - **Validates: Requirements 14.1, 14.7**

- [x] 2. Checkpoint - Ensure all core logic tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Build Qt widgets — views and controls
  - [x] 3.1 Implement `ModuleSelector` widget
    - 13 labeled `QCheckBox`es (all checked by default), "Select all" / "Deselect all" buttons
    - `selected() -> set[str]`, `set_all(bool)` methods
    - _Requirements: 3.1, 3.4, 3.5, 3.6, 3.7, 9.1, 9.2, 9.8_

  - [x] 3.2 Implement `ExportView` widget
    - Output-directory chooser (button + read-only path label, default = Desktop)
    - Snapshot-name `QLineEdit` (maxLength=255)
    - `ModuleSelector` instance
    - Show_All `QCheckBox` (default unchecked)
    - `build_config() -> ExportConfig` method
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 3.1, 3.4, 4.1, 4.2_

  - [x] 3.3 Implement `RestoreView` widget
    - Snapshot-file chooser (filter `*.winsnap`, single file)
    - Selected-path label (or "No snapshot file selected")
    - `ModuleSelector` instance
    - Dry_Run `QCheckBox` (default unchecked)
    - `build_config() -> RestoreConfig` method
    - _Requirements: 8.1, 8.5, 8.6, 8.7, 9.1, 9.2, 9.4, 9.5_

  - [x] 3.4 Implement `LogPanel` widget
    - Read-only `QTextEdit` (rich text for per-line color) + "Clear" and "Copy" buttons
    - `append(entry: LogEntry)`, `clear()`, `copy()`, `plain_text() -> str` methods
    - Color mapping: success→green, warning→amber, error→red
    - Auto-scroll to newest entry on append
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 12.1, 12.2, 12.3, 13.1, 13.2, 13.3, 13.4_

  - [x] 3.5 Implement `ResultsView` widget
    - Three labeled groups (Passed/Failed/Skipped) with per-module rows
    - Failed rows show error message; Skipped rows show reason
    - Counts header (passed/failed/skipped)
    - `show_summary(summary: ResultsSummary)` method
    - _Requirements: 14.1, 14.2, 14.3, 14.7_

  - [x] 3.6 Implement `AppSelectorDialog`
    - Two `QGroupBox` sections ("Winget apps", "Manual apps") with scrollable checkable lists
    - Per-group "Select all" / "Deselect all" buttons, plus OK/Cancel
    - All entries preselected on open; empty group still confirmable/cancelable
    - `result_selection() -> tuple[list[dict], list[dict]] | None`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.7_

  - [x] 3.7 Implement `RunningIndicator` widget
    - Indeterminate `QProgressBar`/spinner shown while an Operation runs
    - _Requirements: 15.5_

  - [x] 3.8 Write unit tests for widget initial states and configuration
    - Test ModuleSelector defaults (all 13 checked), select-all/deselect-all behavior
    - Test ExportView defaults (Desktop path, empty name, show_all unchecked)
    - Test RestoreView defaults (no file selected, dry_run unchecked)
    - Test LogPanel color mapping and auto-scroll
    - Test AppSelectorDialog preselection and empty-group handling
    - Run with `QT_QPA_PLATFORM=offscreen`
    - _Requirements: 3.4, 4.2, 8.6, 9.2, 9.5, 5.2, 5.7_

- [x] 4. Checkpoint - Ensure all widget tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Build background workers and threading infrastructure
  - [x] 5.1 Implement `LogStream` file-like object
    - `write(text)` and `flush()` methods that split text into lines
    - Emit `log(line, classify_severity(line))` signal per complete line
    - _Requirements: 11.2, 12.4, 12.5, 12.6_

  - [x] 5.2 Implement `AppSelectionBridge`
    - `request_app_selection(winget, manual)` — emits signal to UI, blocks Worker on `threading.Event`
    - `provide_result(selection)` — stores result, releases event
    - Cross-thread safe: Worker blocks, UI thread shows dialog
    - _Requirements: 5.1, 5.6, 15.2_

  - [x] 5.3 Implement `ExportWorker`
    - Resolve run set via `resolve_run_modules`
    - Admin check for `power` module (emit warning log if not admin)
    - Create snapshot dir, apply name, bind `show_all`, inject App_Selector via `AppSelectionBridge`
    - Run each module, classify outcomes with `classify_export_outcome`
    - Write `snapshot.json`, zip via `export.zip_snapshot`, clean temp folder
    - Emit success log with archive path and format version
    - On fatal error: remove partial archive, emit error, end operation
    - Emit `finished(ResultsSummary)` signal
    - _Requirements: 2.4, 3.2, 3.3, 4.3, 4.4, 5.6, 6.1, 6.2, 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 5.4 Implement `RestoreWorker`
    - Validate/open archive, read `snapshot.json`
    - Evaluate format version (`evaluate_version`), log date+version with "unknown" placeholders
    - Halt on INCOMPATIBLE; warn and continue on UNPARSEABLE
    - Resolve run set in `MODULES_RESTORE_ORDER`
    - Classify each module via `classify_restore_outcome`
    - In dry-run: emit `restore._summarize(...)` text, apply no changes
    - Otherwise call `mod.restore(...)`
    - Emit `finished(ResultsSummary)` signal
    - _Requirements: 8.2, 8.3, 8.4, 9.3, 9.6, 9.7, 10.1, 10.2, 10.3, 10.4, 10.5, 14.6_

  - [x] 5.5 Write integration tests for ExportWorker
    - Test archive creation with mocked modules (using conftest.py OS-boundary mocks)
    - Test fatal-error cleanup (no partial archive left)
    - Test admin warning emission for power module
    - _Requirements: 7.1, 7.2, 7.5, 6.1_

  - [x] 5.6 Write integration tests for RestoreWorker
    - Test dry-run applies no changes
    - Test incompatible version halts before modules
    - Test module skip conditions (absent, export-errored, deselected)
    - _Requirements: 9.6, 9.7, 10.2, 14.6_

- [x] 6. Checkpoint - Ensure all worker tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Wire MainWindow and integrate all components
  - [x] 7.1 Implement `MainWindow`
    - View switcher + `QStackedWidget` for Export/Restore views
    - Persistent action bar: "Start Export", "Start Restore" buttons + `RunningIndicator`
    - Shared `LogPanel` and `ResultsView`
    - `_operation_in_progress: bool` state flag
    - _Requirements: 1.1, 1.2, 1.3, 15.3, 15.4, 15.5_

  - [x] 7.2 Implement pre-start validation guards in MainWindow
    - `try_start_export()`: validate name (2.7), check ≥1 module selected (3.8), check Windows host (1.5), check no op running (1.4)
    - `try_start_restore()`: check file selected (8.2), check file exists (8.3), check valid archive (8.4), check ≥1 module selected, check Windows host (1.5), check no op running (1.4)
    - Emit appropriate error/warning log entries and return early on guard failure
    - _Requirements: 1.4, 1.5, 2.7, 3.8, 8.2, 8.3, 8.4_

  - [x] 7.3 Wire Worker signals to UI slots
    - Connect `log` signal → `LogPanel.append`
    - Connect `module_completed` signal → `ResultsView` updates
    - Connect `finished` signal → show `ResultsSummary`, clear running state, re-enable controls
    - Connect `app_selection_requested` → show `AppSelectorDialog` on UI thread
    - Connect `running_changed` → `RunningIndicator` visibility
    - _Requirements: 11.2, 14.1, 15.1, 15.2, 15.4, 15.5_

  - [x] 7.4 Add application entry point (`if __name__ == "__main__"`)
    - Create `QApplication`, instantiate `MainWindow`, show, exec
    - _Requirements: 1.1_

  - [x] 7.5 Write smoke test for MainWindow construction
    - Verify window constructs and shows both views without errors
    - Verify both start buttons are visible and enabled
    - Run with `QT_QPA_PLATFORM=offscreen`
    - _Requirements: 1.1, 1.3, 15.3_

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties defined in the design
- Unit tests validate specific examples and edge cases
- All Qt widget tests use `QT_QPA_PLATFORM=offscreen` for headless CI execution
- The pure core logic in tasks 1.1–1.7 has zero Qt dependencies and can be tested without a display
- Workers reuse existing helpers from `export.py` and `restore.py` — no format drift

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "1.5", "1.6", "1.7"] },
    { "id": 2, "tasks": ["1.8", "1.9", "1.10", "1.11", "1.12", "1.13", "1.14", "1.15", "1.16", "1.17", "1.18", "1.19", "1.20"] },
    { "id": 3, "tasks": ["3.1", "3.7"] },
    { "id": 4, "tasks": ["3.2", "3.3", "3.4", "3.5", "3.6"] },
    { "id": 5, "tasks": ["3.8", "5.1", "5.2"] },
    { "id": 6, "tasks": ["5.3", "5.4"] },
    { "id": 7, "tasks": ["5.5", "5.6"] },
    { "id": 8, "tasks": ["7.1"] },
    { "id": 9, "tasks": ["7.2", "7.3", "7.4"] },
    { "id": 10, "tasks": ["7.5"] }
  ]
}
```
