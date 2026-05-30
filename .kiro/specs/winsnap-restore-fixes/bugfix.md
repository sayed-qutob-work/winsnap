# Bugfix Requirements Document

## Introduction

While testing WinSnap on a fresh Windows install, the restore process exhibited several defects that prevented captured settings from being applied correctly. A snapshot that contained valid data either failed to apply, applied incompletely, or applied incorrectly:

- **Apps (winget):** The snapshot listed several winget apps, but `winget import` rejected the generated JSON ("JSON file is not valid / does not specify a recognized schema"), so no apps were installed even though the dry-run detected them.
- **Mouse acceleration:** The "Enhance pointer precision" (pointer acceleration) toggle did not change from on to off, even though the module reported restoring the mouse fields.
- **Wallpaper (multi-monitor):** On a dual-monitor setup the restored wallpaper was a glitched mix of the two per-monitor backgrounds rather than a clean restore.
- **Taskbar (permission error):** Restoring the taskbar aborted with `Permission denied` while copying the hidden/system `desktop.ini`, causing the whole taskbar restore to fail.

This document captures the defective behavior, the expected correct behavior, and the existing behavior that must be preserved. The startup "binary not found" skips observed on a fresh install are explicitly treated as expected behavior (regression prevention), not a defect.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a snapshot containing one or more winget apps is restored THEN the system runs `winget import` against a generated JSON that lacks the recognized schema field, and winget rejects it with "JSON file is not valid / The JSON file does not specify a recognized schema", so none of the apps are installed.

1.2 WHEN a snapshot is restored whose mouse data turns "Enhance pointer precision" (pointer acceleration) off THEN the system reports restoring the mouse fields but the pointer-acceleration setting does not actually change, because the acceleration registry values are not written and/or are not applied to the live session.

1.3 WHEN a wallpaper is restored on a machine with multiple monitors THEN the system applies a single wallpaper via the legacy method without per-monitor handling, producing a glitched/mismatched result across the monitors.

1.4 WHEN the taskbar restore copies the pinned-items folder that contains a hidden/system `desktop.ini` THEN the copy fails with a permission error (Errno 13) that propagates and aborts the entire taskbar restore.

### Expected Behavior (Correct)

2.1 WHEN a snapshot containing one or more winget apps is restored THEN the system SHALL provide `winget import` a JSON document that includes the recognized schema field so that winget accepts the file and installs the listed apps.

2.2 WHEN a snapshot is restored whose mouse data turns "Enhance pointer precision" (pointer acceleration) off (or on) THEN the system SHALL apply the pointer-acceleration setting so that the live setting reflects the snapshot value after restore.

2.3 WHEN a wallpaper is restored on a machine with multiple monitors THEN the system SHALL apply the wallpaper correctly for the multi-monitor configuration without producing a glitched or mismatched result.

2.4 WHEN the taskbar restore encounters a hidden/system `desktop.ini` (or another non-essential file that cannot be copied due to permissions) THEN the system SHALL skip that file and continue, completing the taskbar restore of the remaining pinned items without aborting.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a wallpaper is restored on a machine with a single monitor THEN the system SHALL CONTINUE TO apply the saved wallpaper correctly.

3.2 WHEN a snapshot is restored that includes other mouse, keyboard, and display fields (mouse speed, double-click speed, swap buttons, scroll lines, keyboard repeat, DPI) THEN the system SHALL CONTINUE TO restore those fields as before.

3.3 WHEN a snapshot includes manual (non-winget) apps THEN the system SHALL CONTINUE TO report the manual install list to the user.

3.4 WHEN the taskbar restore runs THEN the system SHALL CONTINUE TO restore the pinned `.lnk` shortcuts and theme/accent settings and restart Explorer as before.

3.5 WHEN a startup registry entry references a binary that does not exist on the target machine (e.g. OneDrive, Steam, Discord on a fresh install) THEN the system SHALL CONTINUE TO skip that entry with a warning, since this is expected behavior and not a defect.

3.6 WHEN winget apps are restored AND `winget import` succeeds THEN the system SHALL CONTINUE TO report success, and on failure SHALL CONTINUE TO surface a warning to the user.

## Bug Condition Derivation

The following pseudocode captures the bug conditions and properties used to validate the fixes. **F** is the original (unfixed) behavior; **F'** is the fixed behavior.

### Issue 1 — Invalid winget import JSON

```pascal
FUNCTION isBugCondition(X)
  INPUT: X = the generated winget import JSON document
  OUTPUT: boolean
  // Bug triggers when the document winget is asked to import
  // lacks the recognized schema field winget requires.
  RETURN NOT hasRecognizedSchemaField(X)
END FUNCTION

// Property: Fix Checking
FOR ALL X WHERE isBugCondition(X) DO
  doc ← buildWingetImportJson'(selectedPackages)
  ASSERT hasRecognizedSchemaField(doc) AND wingetAccepts(doc)
END FOR
```

### Issue 2 — Mouse pointer acceleration not applied

```pascal
FUNCTION isBugCondition(X)
  INPUT: X = snapshot mouse data with enhance_precision (MouseSpeed) value
  OUTPUT: boolean
  // Bug triggers whenever a pointer-acceleration value is present to restore,
  // because it is not written/applied to the live session.
  RETURN X.enhance_precision IS NOT NULL
END FUNCTION

// Property: Fix Checking
FOR ALL X WHERE isBugCondition(X) DO
  restoreMouse'(X)
  ASSERT liveEnhancePrecision() = X.enhance_precision
END FOR
```

### Issue 3 — Wallpaper glitched on multiple monitors

```pascal
FUNCTION isBugCondition(X)
  INPUT: X = restore environment (monitor count, wallpaper data)
  OUTPUT: boolean
  // Bug triggers on multi-monitor configurations.
  RETURN X.wallpaper.enabled AND monitorCount() > 1
END FUNCTION

// Property: Fix Checking
FOR ALL X WHERE isBugCondition(X) DO
  restoreWallpaper'(X)
  ASSERT wallpaperAppliedCleanly(X) AND NOT glitched()
END FOR
```

### Issue 4 — Taskbar restore aborts on desktop.ini permission error

```pascal
FUNCTION isBugCondition(X)
  INPUT: X = taskbar pins backup folder being copied during restore
  OUTPUT: boolean
  // Bug triggers when a hidden/system file (e.g. desktop.ini) cannot be copied.
  RETURN containsUncopyableFile(X)   // e.g. desktop.ini raising Errno 13
END FUNCTION

// Property: Fix Checking
FOR ALL X WHERE isBugCondition(X) DO
  result ← restoreTaskbar'(X)
  ASSERT completedWithoutAbort(result) AND pinsRestored(X)
END FOR
```

### Preservation (all issues)

```pascal
// Property: Preservation Checking
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

This ensures single-monitor wallpaper restores, other mouse/keyboard/display fields, manual app reporting, taskbar pin/theme restore, and the existing startup binary-not-found skip all behave identically to before the fix.
