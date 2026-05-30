# Requirements Document

## Introduction

WinSnap is a Windows settings backup and restore tool driven by two command-line
entry points: `export.py` (captures the current machine's settings into a
`.winsnap` snapshot) and `restore.py` (applies a snapshot to a target machine).
Both delegate work to thirteen settings modules under `modules/`, each exposing
`export(snapshot_dir)` and `restore(snapshot, snapshot_dir)`.

This feature adds `gui.py` in the project root: a PyQt6 desktop application that
wraps the existing export and restore functionality behind a graphical interface.
The GUI must provide complete feature parity with the two CLIs (every flag, every
module, and every per-module option), replace the terminal-based interactive app
picker (`modules/checklist.py`) with a graphical equivalent, present a persistent
color-coded and timestamped log panel with clear and copy controls, and show a
per-run results summary that lists which modules passed, which failed with their
error messages, and which were skipped.

The GUI is a presentation layer over the existing module contract; it does not
change the snapshot format, the module logic, or the registry/file operations the
modules perform.

**Out of scope:** `build.py` is a developer packaging tool (PyInstaller `.exe`
generation), not an end-user settings operation, and is therefore not surfaced in
this GUI. Modifying the existing export/restore module logic is also out of scope;
the GUI invokes that logic unchanged.

## Glossary

- **WinSnap_GUI**: The PyQt6 desktop application defined in `gui.py` that wraps WinSnap's export and restore functionality.
- **Export_View**: The region of the WinSnap_GUI that collects export options and starts an export operation.
- **Restore_View**: The region of the WinSnap_GUI that collects restore options and starts a restore operation.
- **Module**: One of the thirteen WinSnap settings categories: `wallpaper`, `apps`, `mouse_display`, `power`, `taskbar`, `explorer`, `desktop_icons`, `sound_scheme`, `cursors`, `fonts`, `startup`, `env_vars`, `region_lang`.
- **Module_Selection**: The set of Modules the user has chosen to include in an operation, expressed in the GUI as per-Module checkboxes that map to the CLI `--only` and `--skip` flags.
- **App_Selector**: The graphical replacement for the terminal checklist (`modules/checklist.py`) that lets the user choose which winget apps and which manual apps to include when the `apps` Module runs during export.
- **Snapshot**: A `.winsnap` ZIP archive produced by export and consumed by restore, containing `snapshot.json` and bundled asset folders.
- **Snapshot_Format_Version**: The version string stored in `snapshot.json` under `snapshot_format_version`; the restorer refuses a Snapshot whose MAJOR version exceeds the value it supports.
- **Operation**: A single export run or a single restore run initiated from the WinSnap_GUI.
- **Worker**: The background thread on which the WinSnap_GUI executes an Operation so the user interface remains responsive.
- **Log_Panel**: The persistent, scrollable text area in the WinSnap_GUI that displays timestamped, color-coded log entries for the current and prior Operations of the session.
- **Log_Entry**: A single line shown in the Log_Panel, consisting of a timestamp and message text rendered in a severity color.
- **Severity**: The classification of a Log_Entry as `success`, `warning`, or `error`.
- **Results_Summary**: The structured report shown after an Operation completes, grouping each attempted Module into Passed, Failed, or Skipped.
- **Passed**: A Module outcome where the Module's `export` or `restore` function completed without raising an exception and without reporting an explicit error condition. The validity or quality of the produced data does not affect this classification.
- **Failed**: A Module outcome where the Module's `export` or `restore` function raised an exception or otherwise reported an error condition (for example, the `power` Module being unable to capture the active power plan without Administrator_Privilege). The captured exception text or error message is the failure reason.
- **Skipped**: A Module outcome where the Module did not run because the user deselected it, or (during restore) because the Module is absent from the Snapshot or was recorded with an export error.
- **Dry_Run**: A restore mode (CLI `--dry-run`) that reports what each Module would change without writing any changes.
- **Show_All**: An export option (CLI `--show-all`) that bypasses the apps noise filter so every installed entry appears in the App_Selector.
- **Administrator_Privilege**: The Windows elevated-rights state required for the `power` Module to capture the active power plan during export.

## Requirements

### Requirement 1: Launch and operation mode selection

**User Story:** As a user, I want to open WinSnap as a desktop window and choose between exporting and restoring, so that I can perform either workflow without using the command line.

#### Acceptance Criteria

1. WHEN the user runs `gui.py`, THE WinSnap_GUI SHALL, within 5 seconds, display a single window containing both the Export_View and the Restore_View.
2. WHEN the user selects the Export_View or the Restore_View through the view-switching control, THE WinSnap_GUI SHALL display the selected view as the active view and present that view's controls for input.
3. WHILE no Operation is running, THE WinSnap_GUI SHALL allow the user to start either an export Operation or a restore Operation regardless of which view is currently displayed.
4. IF the user attempts to start an Operation while another Operation is running, THEN THE WinSnap_GUI SHALL not start a second Operation, SHALL leave the running Operation unaffected, and SHALL emit a warning Log_Entry indicating that an Operation is already in progress.
5. IF the user starts an Operation on a host operating system that does not provide the Win32 registry and shell APIs the Modules require, THEN THE WinSnap_GUI SHALL not start the Operation and SHALL display an error Log_Entry stating that WinSnap runs on Windows only.

### Requirement 2: Export output location and snapshot name parity

**User Story:** As a user exporting my settings, I want to set where the snapshot is saved and what it is named, so that I have the same control the `--output` and `--name` flags provide.

#### Acceptance Criteria

1. THE Export_View SHALL provide a control to select the output directory that maps to the export `--output` flag.
2. WHEN the Export_View is first shown and the user has not selected an output directory, THE Export_View SHALL default the output directory to the current user's Desktop directory and SHALL display that directory path.
3. THE Export_View SHALL provide a control to enter a snapshot name of up to 255 characters that maps to the export `--name` flag.
4. WHEN the user leaves the snapshot name empty and starts an export Operation, THE WinSnap_GUI SHALL name the Snapshot `winsnap_<timestamp>`, where `<timestamp>` is the export start date and time formatted as `YYYYMMDD_HHMMSS`.
5. WHEN the user selects an output directory through the directory control, THE Export_View SHALL display the selected directory path.
6. IF the user opens the directory control and cancels without choosing a directory, THEN THE Export_View SHALL leave the displayed output directory unchanged.
7. IF the user enters a snapshot name that contains characters not permitted in a Windows file name, THEN THE WinSnap_GUI SHALL emit an error Log_Entry indicating that the snapshot name is invalid and SHALL NOT start the export Operation.

### Requirement 3: Export module selection parity

**User Story:** As a user exporting my settings, I want to choose exactly which modules run, so that I get the same control the `--only` and `--skip` flags provide.

#### Acceptance Criteria

1. THE Export_View SHALL present a selectable control for each of the thirteen Modules.
2. WHEN the user starts an export Operation, THE WinSnap_GUI SHALL run only the Modules whose controls are selected in the Module_Selection.
3. WHEN the user deselects a Module before starting an export Operation, THE WinSnap_GUI SHALL exclude that Module from the export Operation.
4. WHEN the Export_View is first shown, THE WinSnap_GUI SHALL preselect all thirteen Modules.
5. THE Export_View SHALL provide a control to select all Modules and a control to deselect all Modules.
6. WHEN the user activates the select-all control, THE WinSnap_GUI SHALL place all thirteen Modules in the selected state in the Module_Selection.
7. WHEN the user activates the deselect-all control, THE WinSnap_GUI SHALL place all thirteen Modules in the deselected state in the Module_Selection.
8. IF the user starts an export Operation while zero Modules are selected in the Module_Selection, THEN THE WinSnap_GUI SHALL emit an error Log_Entry stating that at least one Module must be selected and SHALL NOT start the Operation.

### Requirement 4: Apps noise filter option parity

**User Story:** As a power user, I want to include every installed entry in the app list, so that I get the same behavior the `--show-all` flag provides.

#### Acceptance Criteria

1. THE Export_View SHALL provide a Show_All control that maps to the export `--show-all` flag.
2. WHEN the Export_View is first shown, THE WinSnap_GUI SHALL set the Show_All control to the disabled state, matching the CLI default where `--show-all` is absent.
3. WHEN the user enables the Show_All control and the `apps` Module runs during an export Operation, THE WinSnap_GUI SHALL pass `show_all=True` to the `apps` export function so the noise filter is bypassed, while the `apps` Module's deduplication of entries already covered by winget continues to apply.
4. WHEN the Show_All control is disabled and the `apps` Module runs during an export Operation, THE WinSnap_GUI SHALL pass `show_all=False` to the `apps` export function so the default apps noise filter is applied.

### Requirement 5: Graphical app selection replacing the terminal checklist

**User Story:** As a user exporting apps, I want to pick which winget and manual applications to include in a graphical list, so that I do not need the terminal-based checklist.

#### Acceptance Criteria

1. WHILE the `apps` Module runs during an export Operation, THE App_Selector SHALL display the discovered winget apps in one group and the discovered manual apps in a second distinct group, with each individual app shown as a separately selectable entry.
2. WHEN the App_Selector is first shown for a run, THE WinSnap_GUI SHALL preselect every listed app in both groups.
3. THE App_Selector SHALL provide, within each group, one control that selects every app in that group and one control that deselects every app in that group.
4. WHEN the user confirms the App_Selector, THE WinSnap_GUI SHALL record in the Snapshot only the winget apps and manual apps that are selected at the moment of confirmation, recording zero apps for any group in which no app is selected.
5. IF the user cancels the App_Selector, THEN THE WinSnap_GUI SHALL record zero winget apps and zero manual apps for the `apps` Module and SHALL continue the export Operation with the remaining selected Modules.
6. WHILE the `apps` Module is not selected in the Module_Selection, THE WinSnap_GUI SHALL omit the App_Selector from the export Operation.
7. IF the `apps` Module runs during an export Operation and a group (winget apps or manual apps) contains no discovered apps, THEN THE App_Selector SHALL display that group with no selectable entries while still allowing the user to confirm or cancel the App_Selector.

### Requirement 6: Administrator detection for power plan export

**User Story:** As a user exporting my power plan, I want to know when the app lacks the rights to capture it, so that I understand why the power module produces no data.

#### Acceptance Criteria

1. WHILE the WinSnap_GUI lacks Administrator_Privilege, WHEN the user starts an export Operation whose Module_Selection includes the `power` Module, THE WinSnap_GUI SHALL emit a Log_Entry with Severity `warning`, before the `power` Module executes, stating that power plan capture will be skipped because Administrator_Privilege is not held.
2. WHEN an export Operation runs the `power` Module while the WinSnap_GUI lacks Administrator_Privilege, THE WinSnap_GUI SHALL detect the `power` Module's not-admin skip result returned in place of an exception and SHALL report the `power` Module as Failed in the Results_Summary, with a failure reason indicating that Administrator_Privilege is required to capture the active power plan.

### Requirement 7: Export execution and snapshot creation

**User Story:** As a user, I want the GUI to build the `.winsnap` file from my selected modules, so that I get the same archive the CLI produces.

#### Acceptance Criteria

1. WHEN an export Operation completes and no fatal error occurred, THE WinSnap_GUI SHALL write exactly one `.winsnap` archive to the selected output directory, where a fatal error is any condition that prevents the archive from being written (for example, the selected output directory is not writable or the archive write fails) and is distinct from a single Module being recorded as Failed.
2. WHEN an export Operation writes a `.winsnap` archive, THE WinSnap_GUI SHALL emit a success Log_Entry containing the full absolute path of the created `.winsnap` archive.
3. IF a Module raises an exception or reports an error condition during an export Operation, THEN THE WinSnap_GUI SHALL record that Module as Failed and continue running the remaining selected Modules.
4. WHEN an export Operation writes a `.winsnap` archive, THE WinSnap_GUI SHALL emit a Log_Entry containing the Snapshot_Format_Version stored in that archive.
5. IF a fatal error prevents the `.winsnap` archive from being written during an export Operation, THEN THE WinSnap_GUI SHALL emit an error Log_Entry identifying the failure, SHALL NOT leave a partial `.winsnap` archive in the selected output directory, and SHALL end the Operation.

### Requirement 8: Restore snapshot selection parity

**User Story:** As a user restoring settings, I want to choose the snapshot file to apply, so that I provide the same input the restore positional argument requires.

#### Acceptance Criteria

1. THE Restore_View SHALL provide a control that restricts selection to a single `.winsnap` file and maps the chosen file to the restore snapshot argument.
2. IF the user starts a restore Operation without selecting a `.winsnap` file, THEN THE WinSnap_GUI SHALL emit an error Log_Entry stating that a snapshot file must be selected and SHALL NOT start the Operation.
3. IF the selected `.winsnap` file does not exist when the user starts a restore Operation, THEN THE WinSnap_GUI SHALL emit an error Log_Entry stating that the file was not found and SHALL NOT start the Operation.
4. IF the selected `.winsnap` file exists but cannot be read as a valid `.winsnap` archive when the user starts a restore Operation, THEN THE WinSnap_GUI SHALL emit an error Log_Entry stating that the file is not a valid snapshot and SHALL NOT start the Operation.
5. WHEN the user selects a `.winsnap` file, THE Restore_View SHALL display the full path of the selected file.
6. WHILE no `.winsnap` file has been selected, THE Restore_View SHALL display an indication that no snapshot file is selected.
7. IF the user opens the file-selection control and cancels without choosing a file, THEN THE Restore_View SHALL leave the displayed snapshot file path unchanged.

### Requirement 9: Restore module selection and dry-run parity

**User Story:** As a user restoring settings, I want to choose which modules apply and preview changes first, so that I get the same control the `--only`, `--skip`, and `--dry-run` flags provide.

#### Acceptance Criteria

1. THE Restore_View SHALL present a selectable control for each of the thirteen Modules.
2. WHEN the Restore_View is first shown, THE WinSnap_GUI SHALL preselect all thirteen Modules.
3. WHEN the user starts a restore Operation, THE WinSnap_GUI SHALL run only the Modules whose controls are selected in the Module_Selection.
4. THE Restore_View SHALL provide a Dry_Run control that maps to the restore `--dry-run` flag.
5. WHEN the Restore_View is first shown, THE WinSnap_GUI SHALL set the Dry_Run control to the disabled state, matching the CLI default where changes are applied.
6. WHILE the Dry_Run control is enabled, WHEN a selected Module runs during a restore Operation, THE WinSnap_GUI SHALL emit a Log_Entry describing what that Module would change.
7. WHILE the Dry_Run control is enabled during a restore Operation, THE WinSnap_GUI SHALL apply no changes to the system's registry, files, or settings.
8. THE Restore_View SHALL provide a control to select all Modules and a control to deselect all Modules.

### Requirement 10: Snapshot format version compatibility

**User Story:** As a user restoring an unfamiliar snapshot, I want to be warned when the snapshot is too new, so that I do not apply settings the tool cannot interpret.

#### Acceptance Criteria

1. WHEN the user starts a restore Operation, THE WinSnap_GUI SHALL read the Snapshot_Format_Version from the selected Snapshot before any Module runs.
2. IF the Snapshot_Format_Version MAJOR component exceeds the version the restorer supports, THEN THE WinSnap_GUI SHALL emit an error Log_Entry indicating that the Snapshot is newer than the version WinSnap supports, SHALL halt the restore Operation before any Module runs, and SHALL apply no changes to the system.
3. WHEN a restore Operation begins, THE WinSnap_GUI SHALL emit a Log_Entry containing the Snapshot's export date and Snapshot_Format_Version, substituting an explicit "unknown" placeholder for either value that is absent from the Snapshot.
4. WHEN the Snapshot_Format_Version MAJOR component does not exceed the version the restorer supports, THE WinSnap_GUI SHALL continue the restore Operation and run the selected Modules.
5. IF the Snapshot_Format_Version cannot be parsed into a recognizable version, THEN THE WinSnap_GUI SHALL emit a warning Log_Entry indicating that the Snapshot version is unrecognized and SHALL continue the restore Operation.

### Requirement 11: Persistent timestamped log panel

**User Story:** As a user, I want a continuous timestamped log of what the tool is doing, so that I can follow progress and review history during my session.

#### Acceptance Criteria

1. THE Log_Panel SHALL display each Log_Entry prefixed with a timestamp in `HH:MM:SS` format.
2. WHEN a Module or Operation produces output during an Operation, THE WinSnap_GUI SHALL append that output to the Log_Panel as one or more Log_Entries.
3. WHILE multiple Operations occur within one application session, THE Log_Panel SHALL retain the Log_Entries from prior Operations until the user clears the Log_Panel.
4. WHEN a new Log_Entry is appended, THE Log_Panel SHALL scroll to make the newest Log_Entry visible.

### Requirement 12: Color-coded log severity

**User Story:** As a user, I want log entries color-coded by severity, so that I can spot successes, warnings, and errors at a glance.

#### Acceptance Criteria

1. WHEN a Log_Entry has Severity `success`, THE Log_Panel SHALL render that Log_Entry in green.
2. WHEN a Log_Entry has Severity `warning`, THE Log_Panel SHALL render that Log_Entry in amber.
3. WHEN a Log_Entry has Severity `error`, THE Log_Panel SHALL render that Log_Entry in red.
4. THE WinSnap_GUI SHALL assign each Log_Entry exactly one Severity of `success`, `warning`, or `error`, and THE Log_Panel SHALL render the Log_Entry using only the color mapped to that Severity.
5. WHEN a Module or Operation reports a failure or an exception, THE WinSnap_GUI SHALL classify the corresponding Log_Entry with Severity `error`.
6. WHEN a Module or Operation reports a non-fatal advisory, THE WinSnap_GUI SHALL classify the corresponding Log_Entry with Severity `warning`.

### Requirement 13: Log panel clear and copy controls

**User Story:** As a user, I want to clear the log and copy its contents, so that I can reset the view or paste the log into a bug report.

#### Acceptance Criteria

1. THE Log_Panel SHALL provide a clear control and a copy control.
2. WHEN the user activates the clear control, THE WinSnap_GUI SHALL remove all Log_Entries from the Log_Panel.
3. WHEN the user activates the copy control, THE WinSnap_GUI SHALL place the full text of all current Log_Entries onto the system clipboard.
4. WHEN the user activates the copy control while the Log_Panel contains no Log_Entries, THE WinSnap_GUI SHALL place an empty string onto the system clipboard.

### Requirement 14: Per-run results summary

**User Story:** As a user, I want a summary after each export or restore that shows which modules passed, failed, or were skipped, so that I know the exact outcome of the run.

#### Acceptance Criteria

1. WHEN an Operation completes, THE Results_Summary SHALL list every attempted Module grouped as Passed, Failed, or Skipped.
2. WHEN a Module is reported as Failed, THE Results_Summary SHALL display that Module's captured error message alongside the Module name.
3. WHEN a Module is reported as Skipped, THE Results_Summary SHALL display the Module name and the reason the Module was Skipped.
4. WHEN a Module's `export` or `restore` function completes without raising an exception and without reporting an error condition, THE WinSnap_GUI SHALL report that Module as Passed in the Results_Summary, regardless of the validity or quality of the data the Module produced.
5. WHEN the user deselects a Module before an Operation, THE WinSnap_GUI SHALL report that Module as Skipped in the Results_Summary.
6. WHILE a restore Operation runs and a selected Module is absent from the Snapshot or was recorded with an export error, THE WinSnap_GUI SHALL report that Module as Skipped in the Results_Summary.
7. WHEN an Operation completes, THE Results_Summary SHALL display the count of Passed Modules, the count of Failed Modules, and the count of Skipped Modules.

### Requirement 15: Responsive interface during operations

**User Story:** As a user, I want the window to stay responsive while an export or restore runs, so that I can read the log and the app does not appear frozen.

#### Acceptance Criteria

1. WHEN the user starts an Operation, THE WinSnap_GUI SHALL execute that Operation on a Worker separate from the user interface thread.
2. WHILE an Operation is running, THE WinSnap_GUI SHALL keep the window responsive to scrolling and to the log clear and copy controls.
3. WHILE an Operation is running, THE WinSnap_GUI SHALL keep the controls that start an export Operation and a restore Operation visible and enabled, while preventing any additional Operation from starting until the running Operation completes.
4. WHEN an Operation finishes, THE WinSnap_GUI SHALL again permit the user to start a new export Operation or restore Operation.
5. WHILE an Operation is running, THE WinSnap_GUI SHALL display a running indicator until the Operation completes.
