# Bugfix Requirements Document

## Introduction

While restoring a snapshot through the WinSnap GUI (`gui.py`) on a real VirtualBox Windows 11 machine, the output produced by the `apps` module's `winget import` command did not appear in the GUI Log Panel. Every Python `print()`-based module rendered correctly as timestamped, color-coded Log Entries (for example `15:44:17  [env_vars] Restored 4 variables...`), but once `[apps] Installing winget apps...` ran, the winget output lost its timestamps and dumped raw to the console instead:

```
Found an existing package already installed. Trying to upgrade the installed package...
No available upgrade found.
Package not found: XP89DCGQ3K6VLD
```

The root cause is that both `ExportWorker` and `RestoreWorker` capture module output with `contextlib.redirect_stdout(log_stream)`, which only swaps Python's `sys.stdout` object. It does not change the OS-level stdout file descriptor (fd 1). When `modules/apps.py` `restore()` runs `subprocess.run(["winget", "import", ...])` without capturing output, the child process inherits the OS stdout fd and writes directly to fd 1, bypassing the Python-level redirect entirely. The output is therefore never routed through `LogStream` / `classify_severity` into the Log Panel.

This was only visible to the user because the `winsnap-restore.spec` PyInstaller build is built with `console=True`; in a windowed (no-console) build the same output would be lost completely. This violates WinSnap GUI Requirement 11.2 ("WHEN a Module or Operation produces output during an Operation, THE WinSnap_GUI SHALL append that output to the Log_Panel as one or more Log_Entries").

This document captures the defective behavior, the expected correct behavior, and the existing behavior that must be preserved. The existing capture of Python `print()` output from modules (which already works) is explicitly treated as behavior to preserve, not a defect.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a module Operation spawns a child process (e.g. `winget import` during restore) whose stdout/stderr is inherited rather than captured, THEN the system writes that child-process output directly to the OS stdout file descriptor (fd 1), bypassing `contextlib.redirect_stdout(log_stream)`, so the output never reaches the Log Panel and appears (if at all) only on an attached console without a timestamp or severity color.

1.2 WHEN the restore Operation runs the `apps` module and `winget import` produces output, THEN the system loses that output from the Log Panel; in a windowed (no-console) build the output is lost entirely because there is no console to receive fd 1.

### Expected Behavior (Correct)

2.1 WHEN a module Operation spawns a child process whose output is written to the OS stdout/stderr file descriptors during the Operation, THEN the system SHALL capture that output and append it to the Log Panel as one or more timestamped, color-coded Log Entries, with no reliance on an attached console.

2.2 WHEN the restore Operation runs the `apps` module and `winget import` produces output, THEN the system SHALL route that output through the existing `LogStream` / `classify_severity` path so it appears in the Log Panel as Log Entries, including in a windowed (no-console) build where it must not be lost.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a module produces output via Python `print()` during an Operation, THEN the system SHALL CONTINUE TO capture that output and display it in the Log Panel as timestamped, color-coded Log Entries exactly as before.

3.2 WHEN a Log Entry's text contains error or warning markers, THEN the system SHALL CONTINUE TO classify and color the Log Entry using `classify_severity` exactly as before (error, warning, or success).

3.3 WHEN the headless test environment captures module output (where `sys.stdout` may be replaced by a stream without a real OS file descriptor), THEN the system SHALL CONTINUE TO function without raising, falling back to the existing object-level capture rather than failing.

3.4 WHEN an Operation completes, raises, or is otherwise torn down, THEN the system SHALL CONTINUE TO leave the process's original stdout/stderr file descriptors and `sys.stdout`/`sys.stderr` restored to their prior state, with no leaked pipes or threads.

3.5 WHEN the `apps` module exports (`_export_winget` already runs `winget export` with `capture_output=True`), THEN the system SHALL CONTINUE TO behave as before, since export does not leak child-process output to fd 1.

## Bug Condition Derivation

The following pseudocode captures the bug condition and properties used to validate the fix. **F** is the original (unfixed) behavior; **F'** is the fixed behavior.

### Child-process output not routed to the Log Panel

```pascal
FUNCTION isBugCondition(X)
  INPUT: X = an Operation that produces output during its run
  OUTPUT: boolean
  // Bug triggers when output is written to the OS stdout/stderr file
  // descriptors (fd 1 / fd 2) by a child process whose handles are
  // inherited, because redirect_stdout only swaps the Python sys.stdout
  // object and cannot intercept fd-level writes.
  RETURN producesFdLevelOutput(X)            // e.g. winget import via subprocess.run
         AND NOT outputCapturedByPython(X)   // not capture_output / not a print()
END FUNCTION

// Property: Fix Checking
FOR ALL X WHERE isBugCondition(X) DO
  runOperation'(X)
  ASSERT outputAppearsInLogPanel(X)          // routed through LogStream as Log Entries
         AND NOT requiresAttachedConsole(X)
END FOR
```

### Preservation

```pascal
// Property: Preservation Checking
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

This ensures that Python `print()` capture, severity classification/coloring, the headless capture path (no real `fileno`), and clean teardown/restoration of stdout/stderr all behave identically to before the fix.
