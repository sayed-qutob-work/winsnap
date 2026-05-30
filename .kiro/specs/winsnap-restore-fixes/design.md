# WinSnap Restore Fixes Bugfix Design

## Overview

WinSnap's restore process (`restore.py`) drives a set of per-domain modules, each exposing a `restore(snapshot, snapshot_dir)` function. During testing on a fresh Windows install, four independent defects surfaced across four of these modules. Each defect causes a captured setting to fail to apply, apply incompletely, or apply incorrectly:

1. **Apps (`modules/apps.py`)** — the filtered `winget_export.json` that WinSnap writes for `winget import` omits the `$schema` field, so winget rejects the file as not specifying a recognized schema and installs nothing.
2. **Mouse acceleration (`modules/mouse_display.py`)** — `restore()` never writes the `enhance_precision` (`MouseSpeed`) value, and the generic `WM_SETTINGCHANGE` broadcast it does send cannot apply pointer acceleration to the live session anyway.
3. **Wallpaper (`modules/wallpaper.py`)** — `restore()` always uses the legacy `SystemParametersInfoW(SPI_SETDESKWALLPAPER)` API with no per-monitor handling, producing a glitched/mismatched result on multi-monitor machines.
4. **Taskbar (`modules/taskbar.py`)** — `restore()` copies the pins folder with `shutil.copytree`, which raises `PermissionError` (Errno 13) on the hidden/system `desktop.ini`; because the pin copy runs first, that exception aborts the rest of the taskbar restore (theme writes and Explorer restart).

The fix strategy is **targeted and minimal per module**: each module is repaired only along the path that triggers its bug, and every change is gated so that inputs which do not trigger the bug follow the original code path unchanged. This keeps the four fixes independent and preserves all existing behavior, including the startup "binary not found" skip, which is explicitly expected behavior and out of scope for any fix.

## Glossary

- **Bug_Condition (C)**: The condition that triggers a given defect. There are four distinct bug conditions, one per issue (winget schema missing, pointer-acceleration value present, multi-monitor wallpaper restore, uncopyable file in pins backup).
- **Property (P)**: The desired behavior once the corresponding bug condition holds — winget accepts the JSON, the live pointer-acceleration setting matches the snapshot, the wallpaper applies cleanly, and the taskbar restore completes without aborting.
- **Preservation**: Existing behavior that the fixes must leave byte-for-byte identical for all inputs that do NOT trigger a bug condition (single-monitor wallpaper, other mouse/keyboard/display fields, manual app reporting, taskbar pin/theme restore, startup binary-not-found skip).
- **F / F'**: `F` is the original (unfixed) module behavior; `F'` is the fixed behavior.
- **`_write_filtered_winget_export`**: The function in `modules/apps.py` that rewrites `winget_export.json` to contain only user-selected packages. The source of Bug 1.
- **`apps.restore`**: The function in `modules/apps.py` that runs `winget import` against `winget_export.json`.
- **`mouse_display.restore`**: The function in `modules/mouse_display.py` that writes mouse/keyboard/display registry values and broadcasts a settings-change message.
- **`enhance_precision`**: The snapshot mouse field captured from the registry value `HKCU\Control Panel\Mouse\MouseSpeed` (0 = "Enhance pointer precision" off, 1 = on).
- **`SPI_SETMOUSE`**: The `SystemParametersInfo` action (`0x0004`) that applies the mouse acceleration triple `{threshold1, threshold2, speed}` to the live session.
- **`wallpaper.restore`**: The function in `modules/wallpaper.py` that copies the saved image and applies it via `SystemParametersInfoW(SPI_SETDESKWALLPAPER)`.
- **`IDesktopWallpaper`**: The Windows COM interface (`CLSID_DesktopWallpaper`) that applies a wallpaper per-monitor; the reliable path for multi-monitor configurations.
- **`taskbar.restore`**: The function in `modules/taskbar.py` that copies the pins backup folder, writes theme settings, and restarts Explorer.
- **`TASKBAR_PINS_DIR`**: The Quick Launch / User Pinned / TaskBar folder that holds pinned `.lnk` shortcuts plus a hidden/system `desktop.ini`.

## Bug Details

### Bug Condition

This spec covers four distinct bug conditions. The bug manifests, per issue, as follows.

**Issue 1 — Invalid winget import JSON.** The bug manifests whenever a snapshot contains one or more selected winget packages. `_write_filtered_winget_export` writes a document containing only a `Sources` array (with `SourceDetails` and `Packages`) but no top-level `$schema` field. `winget import` rejects any file that does not declare a recognized schema, so no apps install.

**Formal Specification:**
```
FUNCTION isBugCondition_apps(X)
  INPUT: X = the generated winget import JSON document
  OUTPUT: boolean

  RETURN documentHasSelectedPackages(X)
         AND NOT hasField(X, "$schema")
END FUNCTION
```

**Issue 2 — Mouse pointer acceleration not applied.** The bug manifests whenever the snapshot mouse data carries a non-null `enhance_precision` value. `mouse_display.restore` writes `MouseSensitivity`, `DoubleClickSpeed`, `SwapMouseButtons`, `WheelScrollLines`, keyboard values, and `LogPixels`, but it never writes `MouseSpeed` and never calls `SPI_SETMOUSE`; the `WM_SETTINGCHANGE` broadcast it sends does not apply pointer acceleration to the live session.

**Formal Specification:**
```
FUNCTION isBugCondition_mouse(X)
  INPUT: X = snapshot mouse data with enhance_precision (MouseSpeed) value
  OUTPUT: boolean

  RETURN X.enhance_precision IS NOT NULL
END FUNCTION
```

**Issue 3 — Wallpaper glitched on multiple monitors.** The bug manifests when wallpaper restore runs on a machine reporting more than one display monitor. `wallpaper.restore` unconditionally calls `SystemParametersInfoW(SPI_SETDESKWALLPAPER)`, the legacy single-surface API, which has no per-monitor awareness and yields a glitched/mismatched composition across monitors.

**Formal Specification:**
```
FUNCTION isBugCondition_wallpaper(X)
  INPUT: X = restore environment (monitor count, wallpaper data)
  OUTPUT: boolean

  RETURN X.wallpaper.enabled
         AND monitorCount() > 1
END FUNCTION
```

**Issue 4 — Taskbar restore aborts on desktop.ini permission error.** The bug manifests when the pins backup folder being restored contains a file that cannot be copied due to permissions — in practice the hidden/system `desktop.ini`. `shutil.copytree` raises `PermissionError` (Errno 13) on that file; the pin copy is the first step of `taskbar.restore`, so the raised exception propagates and the remaining steps (theme writes, Explorer restart) never run.

**Formal Specification:**
```
FUNCTION isBugCondition_taskbar(X)
  INPUT: X = taskbar pins backup folder being copied during restore
  OUTPUT: boolean

  RETURN containsUncopyableFile(X)   // e.g. desktop.ini raising Errno 13
END FUNCTION
```

### Examples

- **Issue 1:** A snapshot selects `Microsoft.VisualStudioCode` and `Git.Git`. Expected: `winget import` accepts the JSON and installs both. Actual: winget prints "The JSON file does not specify a recognized schema" and installs nothing.
- **Issue 2:** A snapshot was captured with "Enhance pointer precision" ON (`MouseSpeed = 1`) and is restored on a machine where the toggle is OFF. Expected: after restore the toggle reads ON and the live cursor uses acceleration. Actual: the registry value and the live toggle remain OFF; the module still reports success.
- **Issue 3:** A dual-monitor laptop+external setup restores a captured wallpaper. Expected: the saved image applies cleanly to the desktop. Actual: the desktop shows a glitched mix of two per-monitor backgrounds.
- **Issue 4:** The pins backup folder contains `Notepad.lnk`, `Terminal.lnk`, and a hidden `desktop.ini`. Expected: both `.lnk` shortcuts are restored, theme is applied, and Explorer restarts. Actual: copying `desktop.ini` raises `PermissionError`, taskbar restore aborts, theme is never applied, and Explorer is never restarted.
- **Edge case (Issue 3 preservation):** A single-monitor machine restores a wallpaper. Expected and actual (must remain): the legacy `SPI_SETDESKWALLPAPER` path applies the image cleanly.
- **Edge case (Issue 1 preservation):** A snapshot has zero selected winget packages. Expected and actual (must remain): `apps.restore` prints "No winget apps to install."

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- **Single-monitor wallpaper restore** must continue to apply the saved wallpaper exactly as before (legacy `SystemParametersInfoW(SPI_SETDESKWALLPAPER)` path).
- **Other mouse/keyboard/display fields** (mouse speed, double-click speed, swap buttons, scroll lines, keyboard repeat delay/speed, display DPI/LogPixels) must continue to be written exactly as before.
- **Manual (non-winget) app reporting** must continue to print the manual install list with names and URLs.
- **Winget success/failure reporting** must continue: success message on `returncode == 0`, warning otherwise.
- **Taskbar pin `.lnk` restore, theme/accent registry writes, and Explorer restart** must continue to run as before for the steps that were already working.
- **Startup binary-not-found skip** must continue to skip entries that reference a missing binary with a warning — this is expected behavior, not a defect, and no fix touches `modules/startup.py`.

**Scope:**
All inputs that do NOT trigger one of the four bug conditions must be completely unaffected. Specifically:
- A winget JSON that already contains selected packages still lists exactly the same packages (only the `$schema` field is added).
- Mouse restores with `enhance_precision == None` write nothing new for acceleration.
- Single-monitor (and zero-/one-monitor) wallpaper restores follow the original API path.
- Pins backups with no uncopyable files restore exactly the same set of `.lnk` shortcuts.

**Note:** The actual expected correct behavior for each buggy input is defined in the Correctness Properties section below. This section focuses on what must NOT change.

## Hypothesized Root Cause

### Issue 1 — Invalid winget import JSON (`modules/apps.py`)

Most likely cause is a **missing schema declaration** in the rewritten export file:

1. **Missing `$schema` field**: `_write_filtered_winget_export` constructs the dict from scratch with only `Sources` → `SourceDetails` + `Packages`. A genuine `winget export` emits a top-level `$schema` (e.g. `https://aka.ms/winget-packages.schema.2.0.json`) plus `CreationDate`/`WinGetVersion`. `winget import` validates against the schema declaration and rejects files that lack it. This matches the observed error string exactly.
2. **Dropped metadata on rewrite**: Even though `_export_winget` reads a valid file, the filtered rewrite discards everything except `Sources`, so any schema/version metadata present in the original is lost.

### Issue 2 — Mouse pointer acceleration not applied (`modules/mouse_display.py`)

Two compounding causes:

1. **Missing registry write**: `restore()` has no branch for `mouse.get("enhance_precision")`. The value is captured on export (`MouseSpeed`) but never written back, so the registry value is unchanged.
2. **Wrong apply mechanism**: Pointer acceleration is not applied by a generic `WM_SETTINGCHANGE` for "Control Panel". Windows applies it through `SystemParametersInfo(SPI_SETMOUSE, ...)` with the integer triple `{MouseThreshold1, MouseThreshold2, MouseSpeed}`. Without that call the live session keeps the old behavior even if the registry is updated.

### Issue 3 — Wallpaper glitched on multiple monitors (`modules/wallpaper.py`)

1. **Legacy single-surface API on multi-monitor**: `SystemParametersInfoW(SPI_SETDESKWALLPAPER)` sets one wallpaper surface and relies on a cached transcoded image. On multi-monitor systems Windows expects per-monitor assignment via the `IDesktopWallpaper` COM interface; using the legacy path can leave stale per-monitor images, producing the glitched mix.
2. **No monitor-count branching**: The code never inspects the monitor count, so it applies the legacy path unconditionally.

### Issue 4 — Taskbar restore aborts on desktop.ini permission error (`modules/taskbar.py`)

1. **`copytree` is all-or-nothing for metadata**: `shutil.copytree(..., dirs_exist_ok=True)` uses `copy2`, which attempts to copy metadata onto the destination `desktop.ini`. That file is hidden/system and frequently read-only, so the write raises `PermissionError` (Errno 13).
2. **Fatal first step**: The pin copy is the first action in `taskbar.restore`; an unhandled exception there aborts the theme writes and Explorer restart that follow.
3. **Non-essential file treated as essential**: `desktop.ini` is folder-view metadata, not a pinned shortcut. It does not need to be copied at all; only `.lnk` files matter for restoring pins.

## Correctness Properties

Property 1: Bug Condition — Winget import JSON declares a recognized schema

_For any_ set of selected winget packages where the bug condition holds (`_write_filtered_winget_export` would otherwise omit `$schema`), the fixed function SHALL write a `winget_export.json` that includes a recognized top-level `$schema` field and still lists exactly the selected packages, so that `winget import` accepts the file and installs the listed apps.

**Validates: Requirements 2.1**

Property 2: Bug Condition — Pointer acceleration applied to the live session

_For any_ snapshot mouse data where `enhance_precision` is not null (bug condition holds), the fixed `mouse_display.restore` SHALL write the `MouseSpeed` registry value and apply it to the live session via `SPI_SETMOUSE`, so that the live "Enhance pointer precision" setting equals the snapshot value after restore.

**Validates: Requirements 2.2**

Property 3: Bug Condition — Wallpaper applies cleanly on multiple monitors

_For any_ restore environment where the wallpaper is enabled and the monitor count is greater than one (bug condition holds), the fixed `wallpaper.restore` SHALL apply the saved image cleanly across the monitors (via the per-monitor `IDesktopWallpaper` path) without producing a glitched or mismatched result.

**Validates: Requirements 2.3**

Property 4: Bug Condition — Taskbar restore tolerates uncopyable files

_For any_ pins backup folder that contains a file which cannot be copied due to permissions (e.g. a hidden/system `desktop.ini` raising Errno 13), the fixed `taskbar.restore` SHALL skip that file, complete the copy of the remaining pinned `.lnk` shortcuts, and continue to theme writes and Explorer restart without aborting.

**Validates: Requirements 2.4**

Property 5: Preservation — Non-buggy inputs behave identically

_For any_ input where none of the bug conditions hold (single-monitor wallpaper restore, `enhance_precision == None`, winget JSON already correct in package content, pins backup with no uncopyable files), the fixed code SHALL produce the same observable result as the original code, preserving single-monitor wallpaper restore, all other mouse/keyboard/display fields, manual app reporting, winget success/failure reporting, taskbar pin/theme restore and Explorer restart, and the startup binary-not-found skip.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

Assuming the root-cause analysis above is correct, four independent, minimal changes are required — one per module.

**File**: `modules/apps.py`

**Function**: `_write_filtered_winget_export`

**Specific Changes**:
1. **Add the recognized schema declaration**: Insert a top-level `"$schema"` key (e.g. `"https://aka.ms/winget-packages.schema.2.0.json"`) into the `data` dict before writing, so winget recognizes the document.
2. **Optionally include companion metadata**: Add `CreationDate` and `WinGetVersion` if available, matching a genuine export; these are non-essential for acceptance but improve fidelity. Keep the existing `Sources`/`SourceDetails`/`Packages` structure and the exact `selected` list unchanged so package content is preserved.
3. **No change to `apps.restore`**: The import command, success/failure reporting, and manual list printing stay exactly as-is (preservation 3.3, 3.6).

**File**: `modules/mouse_display.py`

**Function**: `mouse_display.restore`

**Specific Changes**:
1. **Write the `MouseSpeed` registry value**: Add a guarded branch `if mouse.get("enhance_precision") is not None:` that writes `MouseSpeed` to `HKCU\Control Panel\Mouse` (string value, matching how it is captured).
2. **Apply to the live session via `SPI_SETMOUSE`**: After the registry write, call `SystemParametersInfoW(SPI_SETMOUSE=0x0004, 0, <pointer to int[3] {threshold1, threshold2, speed}>, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)`. Use the conventional thresholds (`6`, `10`) when acceleration is on, and `speed = int(enhance_precision)`.
3. **Leave all other writes and the `WM_SETTINGCHANGE` broadcast untouched**: speed, double-click, swap, scroll, keyboard, and DPI writes remain exactly as before (preservation 3.2).

**File**: `modules/wallpaper.py`

**Function**: `wallpaper.restore`

**Specific Changes**:
1. **Detect monitor count**: Read `GetSystemMetrics(SM_CMONITORS=80)` via `ctypes.windll.user32`.
2. **Branch on monitor count**: If monitor count `<= 1`, follow the existing legacy `SystemParametersInfoW(SPI_SETDESKWALLPAPER)` path unchanged (preservation 3.1). If monitor count `> 1`, apply the image through the `IDesktopWallpaper` COM interface, enumerating monitors and setting the same saved image on each for a clean restore.
3. **Keep the copy-to-Pictures step shared**: The copy of the snapshot image into `~/Pictures/WinSnap` and the missing-file / disabled guards stay common to both paths.
4. **Graceful fallback**: If the COM path is unavailable or fails, fall back to the legacy API and report, so multi-monitor restore degrades rather than crashes.

**File**: `modules/taskbar.py`

**Function**: `taskbar.restore` (pins copy step)

**Specific Changes**:
1. **Replace the fatal `copytree` with a tolerant copy**: Copy the pinned items individually (iterate the `.lnk` shortcuts, or use `copytree` with an `ignore` callable / per-file `try/except`), skipping any file that raises `PermissionError`/`OSError`.
2. **Skip `desktop.ini` explicitly**: Treat `desktop.ini` (and similarly non-essential hidden/system files) as ignorable; only `.lnk` shortcuts must be restored.
3. **Continue past per-file failures**: Log a warning for each skipped file and proceed, ensuring the remaining pins are restored.
4. **Preserve downstream steps**: Theme writes (`_write_theme_settings`) and `_restart_explorer()` must still run after the pins copy, regardless of skipped files (preservation 3.4).

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate each bug on the unfixed code; then verify each fix produces the expected behavior and that all non-buggy inputs are preserved. Because the modules call into the Windows registry and Win32/COM APIs, tests isolate the pure/inspectable logic (JSON construction, branch selection, file selection) and mock the OS boundaries (`winreg`, `ctypes.windll`, `subprocess`, COM) so the suite can run on CI.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate each bug BEFORE implementing the fix. Confirm or refute the root-cause analysis. If refuted, re-hypothesize.

**Test Plan**: Drive each module's `restore`/helper on the UNFIXED code with inputs that satisfy the corresponding bug condition, capturing the artifact or the calls made, and assert the buggy outcome to characterize the defect.

**Test Cases**:
1. **Winget schema test**: Call `_write_filtered_winget_export` with a non-empty selection, then load the JSON and assert `"$schema"` is absent (will fail to contain schema on unfixed code; demonstrates the rejection cause).
2. **Mouse acceleration test**: Call `mouse_display.restore` with `enhance_precision = "1"` under mocked `winreg`/`ctypes` and assert that no `MouseSpeed` write and no `SPI_SETMOUSE` call occurred (will show the missing write/apply on unfixed code).
3. **Multi-monitor wallpaper test**: Call `wallpaper.restore` with monitor count mocked to `2` and assert only the legacy `SPI_SETDESKWALLPAPER` call was made with no per-monitor handling (demonstrates the glitched-path cause on unfixed code).
4. **Taskbar desktop.ini test**: Stage a pins backup containing `*.lnk` plus a `desktop.ini` made to raise `PermissionError` on copy, call `taskbar.restore`, and assert the exception aborts before theme writes / Explorer restart (will fail/abort on unfixed code).

**Expected Counterexamples**:
- `winget_export.json` lacks `$schema`; winget rejects it.
- `MouseSpeed` is never written and `SPI_SETMOUSE` is never invoked.
- Only the legacy single-surface wallpaper API is called on a 2-monitor environment.
- `PermissionError` (Errno 13) on `desktop.ini` aborts the taskbar restore before theme/Explorer steps.
- Possible alternative causes to rule out: winget rejecting on a malformed `SourceDetails` (Issue 1); pointer acceleration governed by a different value (Issue 2); wallpaper glitch caused by image format rather than monitor count (Issue 3); abort caused by a different file than `desktop.ini` (Issue 4).

### Fix Checking

**Goal**: Verify that for all inputs where a bug condition holds, the fixed function produces the expected behavior (Properties 1–4).

**Pseudocode:**
```
FOR ALL X WHERE isBugCondition_apps(X) DO
  doc := buildWingetImportJson_fixed(selectedPackages)
  ASSERT hasField(doc, "$schema") AND packagesPreserved(doc, selectedPackages)
END FOR

FOR ALL X WHERE isBugCondition_mouse(X) DO
  restoreMouse_fixed(X)
  ASSERT wroteRegistry("MouseSpeed", X.enhance_precision)
         AND calledSpiSetMouse(speed = int(X.enhance_precision))
END FOR

FOR ALL X WHERE isBugCondition_wallpaper(X) DO
  restoreWallpaper_fixed(X)
  ASSERT usedPerMonitorPath() AND NOT usedLegacyOnly()
END FOR

FOR ALL X WHERE isBugCondition_taskbar(X) DO
  result := restoreTaskbar_fixed(X)
  ASSERT completedWithoutAbort(result)
         AND allLnkPinsRestored(X)
         AND themeWritten() AND explorerRestarted()
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where NO bug condition holds, the fixed function produces the same result as the original function (Property 5).

**Pseudocode:**
```
FOR ALL X WHERE NOT (isBugCondition_apps(X) OR isBugCondition_mouse(X)
                     OR isBugCondition_wallpaper(X) OR isBugCondition_taskbar(X)) DO
  ASSERT F(X) = F'(X)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain (random package selections, mouse field combinations, monitor counts, pins-folder contents).
- It catches edge cases that manual unit tests might miss.
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs.

**Test Plan**: Observe behavior on the UNFIXED code first for non-buggy inputs (single-monitor wallpaper, `enhance_precision == None`, package content of the JSON, pins backups without uncopyable files, manual app list), then write tests asserting that behavior continues after the fix.

**Test Cases**:
1. **Single-monitor wallpaper preservation**: With monitor count `1`, assert the fixed `wallpaper.restore` makes the identical legacy `SPI_SETDESKWALLPAPER` call as the original.
2. **Other mouse/keyboard/display preservation**: With `enhance_precision == None`, assert the set of registry writes (speed, double-click, swap, scroll, keyboard, DPI) and the `WM_SETTINGCHANGE` broadcast are identical to the original.
3. **Winget package-content preservation**: Assert the `Packages`/`SourceDetails` content and ordering in `winget_export.json` are identical to the original aside from the added `$schema` (and optional metadata).
4. **Manual app reporting preservation**: Assert the manual install list output is unchanged.
5. **Taskbar normal-pins preservation**: With a pins backup containing only `.lnk` files, assert the same shortcuts are restored and theme/Explorer steps run as before.
6. **Startup binary-not-found preservation**: Assert `modules/startup.py` still skips entries with missing binaries (no change introduced).

### Unit Tests

- `_write_filtered_winget_export` produces JSON containing `$schema` and the exact selected packages; empty selection still yields the documented no-op path in `apps.restore`.
- `mouse_display.restore` writes `MouseSpeed` and calls `SPI_SETMOUSE` when `enhance_precision` is set; writes nothing extra when it is `None`.
- `wallpaper.restore` selects the per-monitor path when monitor count `> 1` and the legacy path when `<= 1`; disabled/missing-file guards still short-circuit.
- `taskbar.restore` skips `desktop.ini` and per-file `PermissionError`, restores remaining `.lnk` pins, and still calls theme write + Explorer restart.

### Property-Based Tests

- Generate random selected-package lists and assert the written JSON always contains `$schema` and preserves the package list exactly (Property 1).
- Generate random mouse field combinations and assert acceleration is applied iff `enhance_precision` is non-null, and other fields are written identically regardless (Properties 2 and 5).
- Generate random monitor counts and assert legacy path iff count `<= 1`, per-monitor path iff count `> 1` (Properties 3 and 5).
- Generate random pins-folder contents (mix of `.lnk` files and uncopyable/hidden files) and assert all `.lnk` pins are restored and the restore never aborts (Properties 4 and 5).

### Integration Tests

- Full `restore.py` run against a crafted snapshot whose `apps` selection is non-empty: assert `winget import` is invoked with a schema-valid JSON (mock `subprocess`) and success/warning reporting is preserved.
- Full restore run on a mocked 2-monitor environment: assert the wallpaper module reports a clean per-monitor apply and the overall run reports no errors.
- Full restore run where the taskbar pins backup includes a permission-denied `desktop.ini`: assert the taskbar step completes, theme is applied, Explorer restart is invoked, and `restore.py` reports zero errors for `taskbar`.
- Context/ordering check: confirm the module run order in `restore.py` is unchanged and that a per-module exception is still caught and surfaced in the final error summary.
