# Requirements Document

## Introduction

WinSnap is a Python CLI tool that migrates Windows user settings between machines. `export.py` runs 13 capture modules (each exposing `export(snapshot_dir) -> dict` and `restore(data, snapshot_dir)`), writes `snapshot.json`, and packages everything into a `.winsnap` archive; `restore.py` extracts the archive on the target machine and runs each module's `restore()` in a fixed order.

A completed code audit found that a restore currently "succeeds" only in the sense that it does not throw exceptions — several categories do not actually reproduce the captured state on the target machine, some can damage the target machine, and there is no verification of what was applied. This feature hardens the existing backend so that an export on machine A followed by a restore on machine B genuinely reproduces the captured settings, and adds a verification capability that honestly reports, per category, whether the applied state matches the snapshot.

**Scope:** backend only (export, restore, modules, archive handling, verification). The PyQt6 GUI (`gui.py`) is out of scope and must not be modified, but backend changes must not silently break the GUI's known integration point (its monkey-patch of `modules.checklist.run`). No new settings categories are added.

**Definition of done:** a scripted same-machine export → restore → verify round trip reports every category as *matched* or as *explicitly, honestly skipped* (with reason), never as a false success.

## Requirements

### Requirement 1: Taskbar pin round trip

**User Story:** As a user migrating to a new machine, I want my taskbar pins to actually appear on the target machine, so that my taskbar layout survives the migration instead of reverting to the Windows default.

#### Acceptance Criteria

1. WHEN export runs the taskbar module THEN the system SHALL capture both the `.lnk` shortcut files from `%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\` and the `Favorites` and `FavoritesResolve` REG_BINARY values from `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband` into the snapshot.
2. WHEN restore runs the taskbar module AND the snapshot contains Taskband registry data THEN the system SHALL restore the `.lnk` files to the User Pinned\TaskBar folder AND write the `Favorites`/`FavoritesResolve` REG_BINARY values back to the Taskband key.
3. WHEN the taskbar module has applied pin data THEN the system SHALL restart Explorer (as the final step of the overall restore sequence, per Requirement 2) so the restored Taskband state is loaded.
4. IF writing the Taskband registry blob fails THEN the system SHALL record the taskbar category result as partial or failed with the specific failure reason, and SHALL NOT report the taskbar restore as successful.
5. IF the snapshot predates Taskband capture (older format, field missing) THEN the system SHALL restore only the `.lnk` files and SHALL report the pin-state portion as skipped with a reason, not as matched.
6. WHEN verify runs for the taskbar module THEN the system SHALL re-read the Taskband registry values and the pinned `.lnk` file set and compare them against the snapshot, reporting matched, partial, or failed.

### Requirement 2: Restore ordering

**User Story:** As a user restoring a snapshot, I want applications to be installed before settings that depend on them are applied, so that startup entries and taskbar pins are not silently skipped or broken because their target binaries do not exist yet.

#### Acceptance Criteria

1. WHEN restore executes the module sequence THEN the system SHALL run the apps module (winget install) before the startup module and before the taskbar module.
2. WHEN restore executes the module sequence THEN the system SHALL perform the Explorer restart as the last action of the restore sequence, after all modules that affect Explorer-managed state have run.
3. WHEN the startup module restores a Run entry AND the referenced binary exists at restore time THEN the system SHALL write the Run entry.
4. IF the startup module skips a Run entry because its binary is missing after apps installation has completed THEN the system SHALL record that entry as skipped with its path in the module result, not silently drop it.
5. WHEN the restore order is defined THEN the system SHALL express it as a single explicit ordered list in `restore.py` (or equivalent single source of truth) so tests can assert apps precedes startup and taskbar.

### Requirement 3: Robust winget import/export

**User Story:** As a user restoring apps on a target machine, I want each application install to be attempted and reported individually, so that one slow or unavailable package cannot silently abort or truncate the whole batch.

#### Acceptance Criteria

1. IF winget is not present on the target machine THEN the apps module SHALL detect this before attempting installs, report the apps category as skipped/failed with a clear "winget not found" message, and SHALL NOT raise an unhandled exception.
2. WHEN the apps module installs packages THEN the system SHALL either install per-package or run the batch import without a fixed timeout that can kill installs mid-flight; in either mode, no install in progress SHALL be terminated by a WinSnap-imposed 600-second (or similar arbitrary) timeout.
3. WHEN the apps module completes installation THEN the system SHALL produce a per-application result (installed, already installed, unavailable, failed) aggregated into the module result.
4. IF a package is unavailable in the configured sources THEN the system SHALL continue with the remaining packages (equivalent to `--ignore-unavailable` semantics) and record that package as unavailable.
5. WHEN export runs `winget export` THEN the system SHALL NOT silently return an empty app list on timeout; IF the export command fails or times out THEN the system SHALL surface an explicit warning/error in the export result rather than writing an empty list that looks like "no apps".
6. WHEN the apps module writes `winget_export.json` THEN the `CreationDate` field SHALL contain the real export timestamp, not a hardcoded date.

### Requirement 4: Safe environment-variable restore

**User Story:** As a user restoring onto a machine with a different username, I want environment variables to be adapted to the target profile instead of copied verbatim, so that the restore never leaves my machine with broken TEMP/TMP/PATH values pointing into a non-existent user profile.

#### Acceptance Criteria

1. WHEN the env_vars module restores HKCU environment variables THEN the system SHALL skip variables on a machine-specific denylist that includes at minimum `TEMP`, `TMP`, `OneDrive` (and OneDrive-consumer/commercial variants), `USERPROFILE`, `HOMEPATH`, `HOMEDRIVE`, `APPDATA`, `LOCALAPPDATA`, and `USERNAME`, and SHALL record each skipped variable with reason in the module result.
2. WHEN a restored variable value contains a `C:\Users\<sourceuser>\` prefix that differs from the target user's profile THEN the system SHALL rewrite that prefix to the target's `%USERPROFILE%` (as an expandable `REG_EXPAND_SZ` form or the resolved target path) before writing.
3. WHEN the env_vars module merges PATH THEN the system SHALL preserve existing target PATH entries (merge, not replace) AND SHALL apply the same source-profile rewrite to incoming entries.
4. IF a rewritten incoming PATH entry refers to a directory that does not exist on the target THEN the system SHALL drop that entry and record it as dropped in the module result.
5. WHEN verify runs for the env_vars module THEN the system SHALL compare the written variables (post-rewrite expected values) against the live registry and report matched, partial, or failed; denylisted variables SHALL appear as skipped, not as mismatches.

### Requirement 5: Wallpaper fidelity

**User Story:** As a user, I want my wallpaper image, its fit style, and tiling to be reproduced exactly on the target machine, so that the desktop looks the same as on the source machine.

#### Acceptance Criteria

1. WHEN export runs the wallpaper module THEN the system SHALL capture `WallpaperStyle` and `TileWallpaper` from `HKCU\Control Panel\Desktop` in addition to the image file.
2. WHEN the captured wallpaper file has no usable extension (e.g. Windows `TranscodedWallpaper`) THEN the system SHALL sniff the image type from magic bytes (at minimum JPEG, PNG, BMP, GIF) and store the bundled file with the correct extension.
3. IF the image type cannot be determined from magic bytes THEN the system SHALL still bundle the file, record the unknown type in the snapshot, and restore SHALL attempt to apply it with a warning rather than failing silently.
4. WHEN restore runs the wallpaper module THEN the system SHALL apply the bundled image AND write the captured `WallpaperStyle`/`TileWallpaper` values before triggering the wallpaper refresh.
5. WHEN the multi-monitor code path is addressed THEN the system SHALL either (a) implement `IDesktopWallpaper` correctly using `comtypes` declared as a project dependency, with a proper interface class defining the COM methods, or (b) remove the dead COM path entirely; the system SHALL NOT retain a code path that always throws and is exercised only by mocks.
6. IF option (a) is chosen AND the COM call fails at runtime THEN the system SHALL fall back to the `SystemParametersInfoW` path and record the fallback in the module result.
7. WHEN verify runs for the wallpaper module THEN the system SHALL re-read the `Wallpaper`, `WallpaperStyle`, and `TileWallpaper` registry values and compare the applied image file (path and content hash) against the snapshot.

### Requirement 6: Reliable power-plan restore

**User Story:** As a user restoring a snapshot, I want power-plan restore to either work or tell me exactly why it could not, so that I am not left with a vague failure or a silently inactive plan.

#### Acceptance Criteria

1. WHEN the power module's restore begins THEN the system SHALL check whether the process has administrator privileges; IF it does not THEN the system SHALL report the power category as skipped with an explicit "requires elevation" message and SHALL NOT attempt `powercfg /import`.
2. WHEN the power module imports a plan THEN the system SHALL use a correct import flow: import with a known destination GUID (or parse the resulting GUID only from a successful import's output), then run `powercfg /setactive` with that GUID.
3. IF a plan with the same GUID already exists on the target THEN the system SHALL NOT treat this as a fatal error; the system SHALL proceed to activate the existing plan (or import under a new GUID and activate it) and record the outcome.
4. WHEN any `powercfg` invocation fails THEN the system SHALL capture the command's stderr/stdout in the module result; the dead-logic path that parses a "new GUID" out of a failed import's output SHALL be removed.
5. WHEN verify runs for the power module THEN the system SHALL query the active power scheme (`powercfg /getactivescheme`) and compare its GUID (or name) against the snapshot's intended plan, reporting matched, failed, or skipped (non-elevated).

### Requirement 7: Verification pass and honest reporting

**User Story:** As a user who has just run a restore, I want a per-category report that compares what is actually on the machine against the snapshot, so that I can trust "success" and know exactly what needs manual attention.

#### Acceptance Criteria

1. WHEN a module implements verification THEN it SHALL expose `verify(data, snapshot_dir) -> report` alongside the existing `export`/`restore` contract, where the report identifies the category status as one of: matched, partial, failed, or skipped (with reason).
2. WHEN the user runs `restore.py` with a `--verify` flag (or verification runs automatically post-apply) THEN the system SHALL, for each restored category, re-read the state the module claims to have set (registry values, files, active plan, installed apps, etc.) and compare it against the snapshot.
3. WHEN verification completes THEN the system SHALL print a per-category report listing matched / partial / failed / skipped with per-item detail for partial and failed categories.
4. WHEN any individual write (e.g. a single registry value) fails during restore THEN the system SHALL aggregate that failure into the owning module's result instead of only printing a warning; the final banner SHALL NOT claim unconditional success when any module result contains failures.
5. WHEN the restore process exits THEN the process exit code SHALL be 0 only if no category is failed; IF any category failed THEN the exit code SHALL be non-zero, so scripted round trips can assert the outcome.
6. IF a category cannot be verified programmatically (inherently unverifiable state) THEN the system SHALL report it as skipped with an explicit reason rather than defaulting it to matched.
7. WHEN a scripted same-machine export → restore --verify round trip is executed THEN every category SHALL report matched or explicitly skipped with reason; this scripted round trip SHALL exist as an executable test/script in the repository.

### Requirement 8: Non-interactive (headless) export

**User Story:** As a user or automation script, I want to run export without an interactive terminal checklist, so that exports can run headless (CI, scheduled tasks, remote sessions) while the interactive checklist remains the CLI default.

#### Acceptance Criteria

1. WHEN export is invoked with a headless selection flag (e.g. `--all-apps`) THEN the apps module SHALL select all exported apps without invoking the interactive `msvcrt` checklist.
2. WHEN export is invoked with a selection-file option THEN the apps module SHALL take its app selection from that file without any interactive prompt.
3. WHEN export is invoked with no selection flags in an interactive terminal THEN the system SHALL retain the current interactive checklist behavior as the default.
4. WHEN the selection mechanism is refactored THEN the system SHALL keep `modules.checklist.run` as a monkey-patchable injection point (or provide an equivalent that does not silently break the GUI's existing runtime patch at `gui.py:1227`); `gui.py` itself SHALL NOT be modified.
5. IF export runs headless without a TTY AND no selection flag is provided THEN the system SHALL fail fast with a clear message (or fall back to a documented default) instead of hanging on `msvcrt` input.

### Requirement 9: Accent color fidelity

**User Story:** As a user, I want my full Windows accent color state restored, so that the accent does not partially revert to defaults after migration.

#### Acceptance Criteria

1. WHEN export captures theming state THEN the system SHALL capture, in addition to the existing DWM `AccentColor`/`ColorizationColor` values, the `AccentPalette` (REG_BINARY), `AccentColorMenu`, and `StartColorMenu` values from `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Accent`.
2. WHEN restore applies theming state AND the snapshot contains the Accent key values THEN the system SHALL write them back to the Explorer Accent key.
3. IF the snapshot predates accent-palette capture THEN the system SHALL restore only the legacy DWM values and report the accent-palette portion as skipped with reason.
4. WHEN verify runs for the accent portion THEN the system SHALL re-read the Accent key values and compare byte-for-byte (for REG_BINARY) against the snapshot.

### Requirement 10: Bundle custom cursor and sound files

**User Story:** As a user with custom cursors and sound schemes, I want the actual `.cur`/`.ani`/`.wav` files bundled in the snapshot and re-pointed on the target, so that the restored registry entries do not dangle at source-machine paths.

#### Acceptance Criteria

1. WHEN export runs the cursors module AND a cursor registry value points at a file outside the Windows default cursor directories THEN the system SHALL copy that file into the snapshot bundle.
2. WHEN export runs the sounds module AND a sound-event registry value points at a `.wav` outside the Windows default media directories THEN the system SHALL copy that file into the snapshot bundle.
3. WHEN restore applies cursors or sounds AND the snapshot bundles the referenced file THEN the system SHALL place the file at a stable location on the target (e.g. under the user profile) and rewrite the restored registry value to that new path.
4. IF a referenced custom file was missing at export time THEN the system SHALL record it as missing in the snapshot and, at restore time, skip that entry with reason instead of writing a dangling path.
5. WHEN verify runs for cursors and sounds THEN the system SHALL confirm that every restored registry path points at a file that exists on the target.

### Requirement 11: Remove fake DPI coverage

**User Story:** As a user reading the restore report, I want WinSnap to claim only what it can actually do, so that DPI "restore" that is a no-op on modern Windows is not presented as coverage.

#### Acceptance Criteria

1. WHEN the mouse_display module is updated THEN the system SHALL remove the restore of `LogPixels` and the capture of `DpiScaling` (captured-but-never-restored dead data), or at minimum SHALL cease presenting them as restored settings.
2. WHEN the mouse_display module is updated THEN the system SHALL remove the `cursor_scheme` field from mouse_display capture, since `cursors.py` owns cursor-scheme state; cursor coverage SHALL exist in exactly one module.
3. IF an older snapshot contains the removed fields THEN restore SHALL ignore them gracefully (no error) and the verify report SHALL list DPI as not covered/skipped rather than matched.
4. WHEN documentation or module docstrings describe mouse_display coverage THEN they SHALL NOT claim DPI restore capability.

### Requirement 12: Live application of mouse/keyboard settings

**User Story:** As a user, I want mouse and keyboard settings to take effect immediately after restore, so that I do not need to log off for speed, double-click, and repeat settings to apply.

#### Acceptance Criteria

1. WHEN restore applies mouse speed THEN the system SHALL call `SystemParametersInfoW` with `SPI_SETMOUSESPEED` (with the persist-to-registry flags) in addition to or instead of a registry-only write.
2. WHEN restore applies double-click time THEN the system SHALL apply it live via `SPI_SETDOUBLECLICKTIME`.
3. WHEN restore applies keyboard delay and repeat speed THEN the system SHALL apply them live via `SPI_SETKEYBOARDDELAY` and `SPI_SETKEYBOARDSPEED`.
4. WHEN export captures mouse acceleration THEN the system SHALL capture the actual `MouseThreshold1` and `MouseThreshold2` values; restore SHALL write the captured values and SHALL NOT hardcode 6/10.
5. IF an SPI call fails THEN the system SHALL still perform the registry write, record the live-apply failure in the module result, and note that a logoff may be required.
6. WHEN verify runs for mouse/keyboard THEN the system SHALL re-read the corresponding registry values (and, where available, live SPI GET counterparts) and compare against the snapshot.

### Requirement 13: Archive and CLI hygiene

**User Story:** As a user, I want archive extraction and CLI edge cases handled safely, so that a malicious or unusual `.winsnap` file cannot write outside the extraction directory and ordinary CLI usage does not crash.

#### Acceptance Criteria

1. WHEN restore extracts a `.winsnap` archive THEN the system SHALL sanitize every zip member path and SHALL refuse to extract any member that would resolve outside the extraction directory (zip-slip protection), reporting which member was rejected.
2. WHEN restore locates the snapshot content after extraction THEN the system SHALL select the extracted directory that contains `snapshot.json`, and IF no extracted directory contains `snapshot.json` THEN the system SHALL fail with a clear error instead of using `extracted_dirs[0]` blindly.
3. WHEN export is invoked with `--name` AND the target directory or output file already exists THEN the system SHALL handle the collision deterministically (documented behavior: fail with a clear message, or overwrite/uniquify under an explicit flag) instead of crashing on rename.
4. WHEN a `.winsnap` archive contains no zip-slip violations and a valid `snapshot.json` THEN extraction and directory selection SHALL behave exactly as before (no regression for well-formed archives).

### Requirement 14: Snapshot format versioning and backward compatibility

**User Story:** As a user with existing 0.2.0 snapshots, I want old snapshots to keep restoring after the format gains new fields, so that this upgrade does not orphan my previous exports.

#### Acceptance Criteria

1. WHEN the snapshot format gains new fields (Taskband blob, wallpaper style, accent palette, bundled cursor/sound files, etc.) THEN the system SHALL bump the snapshot format minor version (e.g. 0.2.0 → 0.3.0).
2. WHEN restore reads a snapshot with an older format version THEN each module SHALL treat missing new fields as skip-gracefully (restore what is present, report the missing portions as skipped with reason) and SHALL NOT raise on absent keys.
3. WHEN restore reads a snapshot with a newer major-incompatible version than it supports THEN the system SHALL fail with a clear version-mismatch message before applying anything.
4. WHEN verify runs against an old-format snapshot THEN fields absent from the snapshot SHALL be reported as skipped, not as failed.

### Requirement 15: Non-functional constraints and test integrity

**User Story:** As a maintainer, I want the hardening to preserve the existing module contract, platform constraints, and test suite, so that the change is safe to land and future modules follow the same shape.

#### Acceptance Criteria

1. WHEN modules are modified THEN the system SHALL preserve the `export(snapshot_dir) -> dict` / `restore(data, snapshot_dir)` contract; the only permitted contract addition is `verify(data, snapshot_dir) -> report`.
2. WHEN registry writes are performed THEN the system SHALL write only under HKCU; the sole exception is `powercfg`, which SHALL remain admin-gated per Requirement 6.
3. WHEN dependencies are declared THEN the system SHALL require only the Python 3.10+ standard library, plus `comtypes` if and only if the COM wallpaper path is kept (Requirement 5), and pytest+hypothesis for tests.
4. WHEN new code is added THEN the existing test suite in `tests/` SHALL continue to pass, and new behavior (ordering, rewriting, sniffing, sanitization, verification reporting) SHALL be covered by new pytest/hypothesis tests.
5. WHEN tests exercise the new verification capability THEN at least one scripted same-machine export → restore → verify round trip SHALL exist and SHALL assert that every category reports matched or explicitly skipped (the feature's definition of done).
6. WHEN backend changes touch code paths the GUI consumes THEN `gui.py` SHALL NOT be modified, and the `modules.checklist.run` injection point SHALL remain patchable as described in Requirement 8.
