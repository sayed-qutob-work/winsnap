# Requirements Document

## Introduction

The backend-roundtrip-hardening feature changed WinSnap's backend contracts: modules no longer raise on failure but return structured report dicts (`{"status": "matched"|"partial"|"failed"|"skipped", "reason", "items", "explorer_restart_required"}`), every module implements `verify()`, `restore.py` gained safe extraction (`safe_extract`), snapshot-dir discovery (`find_snapshot_dir`), a deferred single Explorer restart policy, and a version fallback chain; `export.py` gained `resolve_output_path` collision handling; and `modules/manifest.py` became the single source of module names and ordering. `gui.py` was deliberately left untouched and now duplicates this orchestration in stale, pre-hardening form — most severely, it discards restore report dicts and shows cleanly-failed modules as successes.

This feature realigns the PyQt6 GUI so it becomes an honest, thin consumer of the hardened backend. The governing principle is **single source of truth**: the GUI must not carry a second copy of any logic that exists as an importable backend function. Workers become thin Qt adapters — configuration in, backend orchestration functions called under the existing `LogStream` stdout capture, report dicts out via signals — and the results view renders the report dicts faithfully.

**Scope constraints (locked):**
- The exported/restored data formats and the module contract (`export(snapshot_dir) -> dict`, `restore(data, snapshot_dir) -> report dict`, `verify(data, snapshot_dir) -> report dict`, report shape per `modules/report.py`) are LOCKED. The GUI consumes them; it never changes them.
- Correctness and honest reporting only. No visual redesign, no restyling, no look-and-feel reorganization. New UI surface is limited to what honest reporting strictly requires (representing "partial" status, showing verify results, per-item failure detail, an overwrite affordance for export collisions).
- The backend must stay usable without the GUI. Backend behavior does not change; backend code may only gain refactors that expose already-existing logic for reuse without changing CLI behavior.
- The GUI's worker-thread structure (QThread + worker QObject + signals, `AppSelectionBridge`, `LogStream` stdout capture) is sound and stays.

## Requirements

### Requirement 1: Report-based restore outcome classification (restore honesty)

**User Story:** As a user restoring a snapshot through the GUI, I want each module's displayed outcome to reflect the structured report the module actually returned, so that failures and partial restores are never shown as successes.

#### Acceptance Criteria

1. WHEN a module's `restore()` returns a report dict THEN the GUI SHALL classify that module's outcome from the report's `status` field (`matched`, `partial`, `failed`, or `skipped`), not from whether the call raised an exception.
2. WHEN the GUI restore run completes THEN the GUI SHALL NOT retain any outcome-classification path that marks a module as passed solely because its `restore()` call did not raise (the current `classify_restore_outcome` exception-based behavior SHALL be removed or replaced).
3. WHEN a module's report has status `failed` or `skipped` and a non-empty `reason` THEN the GUI SHALL surface that reason to the user in the results display.
4. WHEN a module's report has status `partial` THEN the GUI SHALL represent it as a distinct outcome, visually and programmatically separate from full success and full failure.
5. IF the GUI's internal module-status representation cannot express all four report statuses THEN the GUI SHALL extend that representation (e.g. the `ModuleStatus` enum) so that `matched`, `partial`, `failed`, and `skipped` each map to a distinct value.
6. WHEN a module's `restore()` raises an exception THEN the GUI SHALL present that module with the same synthesized `failed` outcome semantics as `restore.run_modules` (which converts exceptions into `{"status": "failed"}` reports), and the restore run SHALL continue with the remaining modules.
7. WHEN the GUI maps a report dict to a displayed outcome THEN the mapping SHALL be implemented as a pure, Qt-independent function that is unit-testable headless.

### Requirement 2: Reuse of backend restore orchestration (single source of truth)

**User Story:** As a maintainer, I want the GUI restore path to call `restore.py`'s importable orchestration functions instead of re-implementing the module loop, so that GUI and CLI restore semantics can never drift again.

#### Acceptance Criteria

1. WHEN the GUI performs a restore THEN it SHALL invoke `restore.run_modules` (or the same importable backend function the CLI uses) rather than iterating modules with its own loop, with the function's printed output captured by the existing `LogStream` stdout capture.
2. WHEN a module in the snapshot is absent from the modules data, or was captured with an export error THEN the GUI restore SHALL skip it with the same semantics and skip reasons as the CLI.
3. WHEN a module's `restore()` returns `None` (contract violation) THEN the GUI SHALL record that module as skipped with the same "module returned no report" semantics as the CLI.
4. WHEN the GUI restore run finishes THEN the GUI SHALL receive the per-module report dicts produced by the backend orchestration (e.g. as the function's return value) and SHALL emit them to the UI thread via signals for rendering.
5. IF an already-existing piece of backend logic needed by the GUI is not currently importable without side effects THEN the backend MAY be refactored to expose it, and WHEN such a refactor is made THEN the CLI's observable behavior SHALL remain unchanged.
6. WHEN this feature is complete THEN the GUI SHALL NOT contain a re-implementation of any orchestration logic that exists as an importable backend function (module run loop, verify loop, extraction, snapshot-dir discovery, version fallback, output-path collision resolution, zip creation, snapshot metadata construction).

### Requirement 3: Verify support in the GUI

**User Story:** As a user restoring a snapshot, I want to optionally verify the restored settings and see per-module verify results alongside restore results, so that I know whether the restore actually took effect.

#### Acceptance Criteria

1. WHERE the GUI restore configuration is presented, the GUI SHALL provide a user-selectable verify option equivalent to the CLI's `--verify` flag, defaulting to off (matching CLI default behavior).
2. WHEN the verify option is selected and the restore phase completes THEN the GUI SHALL run verification by invoking `restore.run_verify` (or the same importable backend function the CLI uses) over the same selected modules.
3. WHEN verification runs THEN the GUI SHALL classify each module's verify outcome from the returned verify report dict's `status` field, using the same report-to-outcome mapping rules as Requirement 1.
4. WHEN both restore and verify results exist for a module THEN the results display SHALL show the restore status and the verify status as distinguishable per-module outcomes, with information content equivalent to `restore.py`'s `print_summary` "restore=X verify=Y" lines.
5. IF the verify option is not selected THEN the GUI SHALL NOT run verification and the results display SHALL NOT show empty or placeholder verify outcomes.
6. WHEN a verify report has status `partial` or `failed` THEN the GUI SHALL surface the report's `reason` and per-item detail per Requirement 8.

### Requirement 4: Safe archive extraction and snapshot-dir discovery

**User Story:** As a user opening a `.winsnap` archive in the GUI, I want the same extraction safety and snapshot layout handling as the CLI, so that malicious archives are refused and valid archive layouts (including flat archives) restore correctly.

#### Acceptance Criteria

1. WHEN the GUI extracts a `.winsnap` archive THEN it SHALL use `restore.safe_extract` (the same importable function as the CLI), not a bare `extractall`.
2. IF any archive member would extract outside the target directory (zip-slip) THEN the GUI SHALL refuse the entire archive, perform no partial extraction beyond what `safe_extract` guarantees, and present the user a clear error stating the archive was rejected as unsafe (surfacing the `ZipSlipError` information).
3. WHEN the archive is extracted THEN the GUI SHALL locate the snapshot directory using `restore.find_snapshot_dir` (checking the extraction root first, then immediate subdirectories for `snapshot.json`), not by picking the first extracted directory.
4. WHEN the archive is flat (snapshot.json at the archive root) THEN the GUI SHALL restore it successfully.
5. IF no valid snapshot layout is found (`SnapshotLayoutError`) THEN the GUI SHALL report a clean, user-readable error identifying the archive as not containing a recognizable snapshot, without crashing or showing a raw traceback as the primary message.

### Requirement 5: Module ordering and list derivation from the manifest

**User Story:** As a maintainer, I want every GUI module list (export order, restore order, module-selector checkboxes) derived from `modules/manifest.py`, so that a module added to the manifest automatically appears in the GUI in the correct order and ordering bugs like "apps after taskbar" cannot recur.

#### Acceptance Criteria

1. WHEN the GUI determines module export order, module restore order, or the set of selectable modules THEN it SHALL derive them from `manifest.MODULE_NAMES` (directly or via `restore.ALL_MODULES`, which preserves the manifest order), and the GUI SHALL NOT contain hardcoded module-name lists (`MODULES_EXPORT_ORDER`, `MODULES_RESTORE_ORDER`, and any hardcoded ModuleSelector list SHALL be removed or replaced by manifest-derived values).
2. WHEN the GUI restores modules THEN the effective execution order SHALL be identical to the CLI's manifest order — in particular, `apps` SHALL run before `startup` and `taskbar`.
3. IF a new module is added to `manifest.MODULE_NAMES` THEN it SHALL appear in the GUI's module selector and run in manifest position without any GUI code change.
4. WHEN the GUI consumes `restore.ALL_MODULES` THEN it SHALL continue to consume it as `(key, module)` tuples (the preserved shape SHALL NOT be broken).

### Requirement 6: Explorer restart policy parity

**User Story:** As a user restoring explorer, desktop-icons, or taskbar settings through the GUI, I want Explorer restarted exactly once after all modules finish (when any module requires it), so that restored settings actually take visual effect — the same behavior as the CLI.

#### Acceptance Criteria

1. WHILE the GUI restore module loop is running, inline Explorer restarts SHALL be suppressed with the same mechanism as the CLI (`taskbar.INLINE_EXPLORER_RESTART = False`, restored in a `finally`), which is satisfied automatically if the GUI reuses `restore.run_modules` per Requirement 2.
2. WHEN all selected modules have run and at least one restore report has `explorer_restart_required` set true THEN the GUI restore SHALL perform exactly one `winutil.restart_explorer()` call after the loop.
3. IF no restore report has `explorer_restart_required` set true THEN the GUI SHALL NOT restart Explorer.
4. WHEN a GUI restore includes `explorer` or `desktop_icons` but not `taskbar` THEN Explorer SHALL still be restarted if those modules' reports request it (regression guard for the current silent-no-restart bug).

### Requirement 7: Snapshot version evaluation parity

**User Story:** As a user restoring an older but valid snapshot through the GUI, I want the same version acceptance decision as the CLI, so that a snapshot the CLI restores cleanly is not flagged as unrecognized by the GUI.

#### Acceptance Criteria

1. WHEN the GUI evaluates a snapshot's format version THEN it SHALL use the same fallback chain as `restore._check_format_version`: `snapshot_format_version`, then `winsnap_version`, then `"0.1.0"`.
2. WHEN the GUI and CLI evaluate the same snapshot metadata THEN they SHALL produce the same accept/warn/refuse outcome.
3. IF the version-evaluation logic is needed by both CLI and GUI THEN it SHALL exist as a single importable backend function consumed by both, per Requirement 2.6.

### Requirement 8: Honest results presentation

**User Story:** As a user, I want the GUI results view to show per-module status (including partial), reasons, per-item outcomes, and verify results, so that I get information equivalent to the CLI's `print_summary` without reading logs.

#### Acceptance Criteria

1. WHEN the results view renders a restore or verify run THEN it SHALL display, per module, the report status (`matched`, `partial`, `failed`, or `skipped`) using four visually distinct states.
2. WHEN a module's report status is `partial` or `failed` THEN the results view SHALL make per-item detail available for that module: each item's `name`, `status`, `detail`, and, where present, `expected` and `actual` values.
3. WHEN a module's report has a non-empty `reason` THEN the results view SHALL display it with the module's outcome.
4. WHEN a run includes verify results THEN the results view SHALL present restore and verify status side by side per module, per Requirement 3.4.
5. WHEN the results view summarizes the run THEN its aggregate information content SHALL be equivalent to `restore.py`'s `print_summary` (per-module status lines plus reasons and item detail for non-matched modules); it MAY differ in layout but SHALL NOT omit information `print_summary` would show.
6. WHERE the results view is extended, the extension SHALL be limited to what honest reporting strictly requires — no restyling or reorganization of existing UI beyond adding the partial state, verify column/field, reasons, and per-item detail affordance.
7. WHEN report dicts are transformed into results-view row data THEN the transformation SHALL be a pure, Qt-independent function unit-testable headless.
8. WHEN the GUI displays dry-run output THEN it SHALL continue to reuse `restore._summarize` (existing compatible behavior SHALL NOT be broken).

### Requirement 9: Export output-path collision handling

**User Story:** As a user exporting a snapshot with a custom name through the GUI, I want name collisions detected before modules run and a clear choice to overwrite or pick another name, so that the export never crashes after doing all the work.

#### Acceptance Criteria

1. WHEN the user starts an export with a custom name THEN the GUI SHALL resolve the output path via `export.resolve_output_path(output, name, force)` (the same importable function as the CLI) before any module runs.
2. IF the resolved output path (directory or `.winsnap` file) already exists and overwrite is not authorized THEN the GUI SHALL fail fast before running any module and SHALL present the `FileExistsError` as a clear, user-readable message identifying the conflicting path.
3. WHEN a collision is reported THEN the GUI SHALL offer the user a minimal overwrite affordance (e.g. a confirmation prompt or checkbox equivalent to the CLI's `--force`), and WHEN the user authorizes overwrite THEN the GUI SHALL re-run the export with force semantics identical to the CLI (delete-and-overwrite via `resolve_output_path`).
4. WHEN this feature is complete THEN the GUI SHALL NOT contain the bare `snapshot_dir.rename(named)` collision-prone path, and the dead default-name computation duplicating `create_snapshot_dir`'s timestamping SHALL be removed.

### Requirement 10: Export pipeline reuse

**User Story:** As a maintainer, I want the GUI export path built from `export.py`'s importable pieces, so that snapshot metadata, zipping, and cleanup written by GUI exports and CLI exports cannot drift.

#### Acceptance Criteria

1. WHEN the GUI performs an export THEN it SHALL use `export.create_snapshot_dir`, `export.resolve_output_path`, `export.zip_snapshot`, and `export.SNAPSHOT_FORMAT_VERSION` (and any other importable export-pipeline functions the CLI uses) rather than inline re-implementations.
2. WHEN a GUI export writes `snapshot.json` THEN the metadata fields and values SHALL be constructed by the same code path as a CLI export, such that GUI-written and CLI-written snapshot metadata cannot structurally drift.
3. WHEN the GUI export runs modules THEN modules SHALL run in manifest order per Requirement 5, and any remaining GUI-side pipeline glue SHALL be structurally minimal (configuration marshalling and signal emission only).
4. WHEN the GUI export uses the apps module THEN the existing `modules.checklist.run` monkey-patch mechanism (including the call-time attribute lookup in `apps.py` and the TTY-guard placement that keeps the GUI path safe), `AppSelectionBridge` (including `None`-on-cancel handling by `apps.export`), and `apps.export(snapshot_dir, show_all=...)` behavior SHALL continue to work unchanged.

### Requirement 11: Non-functional requirements

**User Story:** As a maintainer, I want the realigned GUI to preserve the project's architecture and testability guarantees, so that the change is safe, reviewable, and future-proof.

#### Acceptance Criteria

1. WHEN this feature is implemented THEN the backend SHALL remain fully usable without the GUI: no backend module SHALL import PyQt6 or any GUI code, and the CLI behavior of `export.py` and `restore.py` SHALL be observably unchanged.
2. WHEN this feature is implemented THEN the locked module contract and report dict shapes SHALL be unchanged.
3. WHEN GUI logic is added or modified THEN report-mapping, version-evaluation consumption, ordering derivation, and results-row construction SHALL be implemented as pure functions separable from Qt widgets and testable headless with pytest (consistent with the existing gui.py test approach: mock-heavy, no real registry, no display required).
4. WHEN this feature is implemented THEN the existing worker-thread structure (QThread + worker QObject + signals, `AppSelectionBridge`, `LogStream` stdout capture) SHALL be retained; workers SHALL act as thin adapters that pass configuration in, call backend orchestration under `LogStream` capture, and emit report dicts out via signals.
5. WHEN backend orchestration functions print progress during GUI runs THEN that output SHALL continue to appear in the GUI log view via the existing `LogStream` capture.
6. WHEN this feature is implemented THEN all existing tests SHALL continue to pass, and new tests SHALL cover: report-to-outcome mapping for all four statuses, verify on/off flows, zip-slip refusal, flat and nested archive layouts, `SnapshotLayoutError` handling, manifest-derived ordering (including apps-before-startup/taskbar), single deferred Explorer restart, version fallback parity, and export collision fail-fast plus force-overwrite paths.
7. WHEN long-running backend orchestration executes THEN it SHALL run on the worker thread (never the UI thread), and UI updates SHALL occur only via signal/slot delivery.
