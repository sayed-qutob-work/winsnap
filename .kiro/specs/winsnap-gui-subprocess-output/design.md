# WinSnap GUI Subprocess Output Capture Bugfix Design

## Overview

The WinSnap GUI (`gui.py`) runs each Operation (export or restore) on a background `Worker` thread and captures module output for display in the Log Panel by wrapping the module call in `contextlib.redirect_stdout(log_stream)`, where `log_stream` is a `LogStream(QObject)` file-like object. `LogStream.write` splits incoming text into lines, classifies each line's severity via `classify_severity`, and emits a `log_line` signal that the UI thread renders as timestamped, color-coded Log Entries.

This mechanism works for any module that produces output with Python `print()`, because `print()` writes to whatever object `sys.stdout` currently points at — which `redirect_stdout` has swapped to the `LogStream`. It does **not** work for output produced by a **child process**. `contextlib.redirect_stdout` only rebinds the Python-level `sys.stdout` object; it leaves the operating system's stdout file descriptor (fd 1) untouched. When `modules/apps.py` `restore()` calls `subprocess.run(["winget", "import", ...])` without capturing output, the spawned `winget` process inherits the parent's OS stdout handle and writes straight to fd 1. That output never passes through `LogStream`, so it never becomes a Log Entry. On the `console=True` PyInstaller build the user happens to see the raw text on the attached console; on a windowed (no-console) build it is lost entirely.

The fix strategy is to **capture output at the OS file-descriptor level inside the Worker, for the duration of an Operation**, and feed the captured bytes into the existing `LogStream` / `classify_severity` path. Redirecting fd 1 (and fd 2) to a pipe captures **both** Python `print()` output and child-process output without modifying any module, so the same class of bug cannot recur in other modules that shell out. The change is confined to the GUI Worker layer and is gated so that the headless/pytest case — where `sys.stdout` may not expose a real `fileno()` — falls back to the existing object-level `redirect_stdout` capture, and so that the original file descriptors are always restored in a `finally` block.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the defect — an Operation produces output at the OS stdout/stderr file-descriptor level (typically from a child process whose handles are inherited) rather than through the Python `sys.stdout` object, so `redirect_stdout` cannot intercept it.
- **Property (P)**: The desired behavior once the bug condition holds — the fd-level output is captured and appended to the Log Panel as timestamped, color-coded Log Entries with no reliance on an attached console.
- **Preservation**: Existing behavior the fix must leave identical for all inputs that do NOT trigger the bug condition — Python `print()` capture, severity classification/coloring, the headless capture path (no real `fileno`), and clean teardown/restoration of stdout/stderr.
- **F / F'**: `F` is the original (unfixed) Worker behavior; `F'` is the fixed behavior.
- **`LogStream`**: The `QObject` file-like object in `gui.py` (around line 1021) that buffers text, splits it on newlines, classifies severity with `classify_severity`, and emits the `log_line` signal per complete line. Requirements 11.2, 12.4, 12.5, 12.6.
- **`classify_severity`**: The pure function in `gui.py` (around line 146) that maps a line to `Severity.ERROR` / `WARNING` / `SUCCESS` based on marker keywords.
- **`Severity`**: The enum (`success`, `warning`, `error`) used to color a Log Entry.
- **`ExportWorker` / `RestoreWorker`**: The background `QObject` workers in `gui.py` (around lines 1136 and 1345). Each wraps the module call in `with contextlib.redirect_stdout(log_stream):` — the export loop around line 1260 (`result = export_fn(snapshot_dir)`) and the restore loop around line 1524 (`mod.restore(...)`).
- **`contextlib.redirect_stdout`**: A context manager that temporarily rebinds `sys.stdout` to a given object. It does NOT change the OS-level stdout file descriptor (fd 1); this is the source of the bug.
- **fd 1 / fd 2**: The operating system's standard-output and standard-error file descriptors. Child processes inherit these handles, so a child writing to them bypasses any Python-object-level redirect.
- **`apps.restore`**: The function in `modules/apps.py` that runs `winget import` via `subprocess.run([...], timeout=600)` **without** `capture_output=True`. The source of the leaked output.
- **`_export_winget`**: The function in `modules/apps.py` that already runs `winget export` with `capture_output=True`; it does not leak, and is the reason export is unaffected.
- **FdCapture**: The new helper (introduced by this fix) that, for the duration of an Operation, redirects fd 1/fd 2 to an OS pipe, runs a background reader thread that feeds the pipe's bytes into the `LogStream`, and restores the original fds in a `finally` block.

## Bug Details

### Bug Condition

The bug manifests whenever an Operation produces output by writing to the OS stdout/stderr file descriptors instead of through the Python `sys.stdout` object — in practice, whenever a module spawns a child process whose stdout/stderr handles are inherited and not captured. The concrete, confirmed instance is `apps.restore` running `subprocess.run(["winget", "import", ...])` during a restore Operation: `winget` writes its progress lines to fd 1, which `contextlib.redirect_stdout(log_stream)` cannot intercept, so the lines never reach `LogStream` and never become Log Entries.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X = an Operation that produces output during its run
  OUTPUT: boolean

  RETURN producesFdLevelOutput(X)            // child process writes to fd 1 / fd 2
         AND NOT outputCapturedByPython(X)   // not capture_output, not a print()
END FUNCTION
```

### Examples

- **Confirmed (restore / winget import):** A snapshot with a non-empty `apps` selection is restored through the GUI. `winget import` prints `Found an existing package already installed...`, `No available upgrade found.`, and `Package not found: XP89DCGQ3K6VLD`. Expected: those lines appear in the Log Panel as timestamped Log Entries (`Package not found` classified as... — see note below). Actual: the lines dump raw to the console (or are lost on a windowed build) and never appear in the Log Panel.
- **Python print (preservation):** The `env_vars` module prints `[env_vars] Restored 4 variables...`. Expected and actual (must remain): the line appears as `15:44:17  [env_vars] Restored 4 variables...` in the Log Panel, colored by severity.
- **Edge case (headless tests):** Under pytest, `sys.stdout` is replaced by an object without a real OS `fileno()`. Expected: fd-level capture is skipped and the existing object-level capture is used, so tests do not raise.
- **Edge case (no output):** An Operation that produces no fd-level output behaves exactly as before; nothing extra is emitted.

> Note on severity: `classify_severity` treats lines containing `error`/`exception`/`traceback`/`failed` as `ERROR` and `warning`/`advisory`/`skipped` as `WARNING`. Whatever severity a captured line would receive from `classify_severity` is preserved by the fix — the fix only changes whether the line is captured at all, not how it is classified.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- **Python `print()` capture** from modules must continue to flow into the Log Panel as timestamped, color-coded Log Entries exactly as before.
- **Severity classification and coloring** via `classify_severity` / `Severity` must continue to behave identically — the same line maps to the same severity and color.
- **The headless capture path** must continue to work: when `sys.stdout` has no real OS `fileno()` (as in the pytest harness), capture must fall back to the existing object-level `redirect_stdout(log_stream)` and must not raise.
- **Teardown and restoration** must continue to leave the process's original fd 1/fd 2 and `sys.stdout`/`sys.stderr` restored after every Operation — on success, on exception, and on cancellation — with no leaked pipes or reader threads.
- **Export behavior** must remain unchanged, since `_export_winget` already uses `capture_output=True` and does not leak child-process output.
- **The module contract** must remain unchanged: no module under `modules/` is modified by the preferred fix, so module-level behavior (return values, registry/file/subprocess effects) is identical.

**Scope:**
All inputs that do NOT trigger the bug condition must be completely unaffected. Specifically:
- Operations whose output is produced only via Python `print()` route through the Log Panel exactly as before.
- Severity classification for every captured line is unchanged.
- In environments without a real OS stdout `fileno()`, the Operation uses the original object-level capture path with identical observable behavior.
- After any Operation, `sys.stdout`, `sys.stderr`, fd 1, and fd 2 are restored to exactly what they were before the Operation.

**Note:** The actual expected correct behavior for the buggy input is defined in the Correctness Properties section below. This section focuses on what must NOT change.

## Hypothesized Root Cause

Based on the bug description and the code in `gui.py` and `modules/apps.py`, the cause is well understood (this is a confirmed bug, not a speculative one), but the candidate mechanisms are enumerated for the exploration test to confirm or refute:

1. **`redirect_stdout` is object-level, not fd-level (primary cause)**: `contextlib.redirect_stdout(log_stream)` rebinds only the Python `sys.stdout` object. The OS stdout file descriptor (fd 1) still points at the original console/handle. Any writer that targets fd 1 directly — every child process by default — bypasses the redirect. This fully explains why `print()` (which uses `sys.stdout`) is captured but `winget` (a child process) is not.

2. **`apps.restore` does not capture subprocess output**: The `winget import` call uses `subprocess.run([...], timeout=600)` with no `capture_output=True` and no `stdout=`/`stderr=` redirection, so the child inherits fd 1/fd 2 and writes to them directly. (By contrast, `_export_winget` uses `capture_output=True`, which is why export does not leak.)

3. **Build console mode masks the symptom**: The `winsnap-restore.spec` build sets `console=True`, so fd 1 is attached to a real console and the leaked output is visible there. A windowed build (`console=False`) would have no console attached to fd 1, so the output would be discarded — making the data loss total rather than merely misplaced.

4. **Ruled-out alternatives** (the exploration test should confirm these are not the cause): the `LogStream`/`classify_severity` path itself is correct (proven by the working `print()` modules and `tests/test_log_stream.py`); the Worker threading/signal wiring is correct (other modules render fine); the issue is specifically the fd-level escape of child-process output.

## Correctness Properties

Property 1: Bug Condition — Child-process output reaches the Log Panel

_For any_ Operation where the bug condition holds (output is written to the OS stdout/stderr file descriptors by a child process whose handles are inherited rather than captured by Python — e.g. `winget import` during restore), the fixed Worker SHALL capture that output and route it through `LogStream` so it appears in the Log Panel as one or more timestamped, color-coded Log Entries, with no reliance on an attached console (so it is not lost in a windowed build).

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation — Non-fd-level behavior is identical

_For any_ input where the bug condition does NOT hold (output produced via Python `print()`, severity classification of any captured line, the headless path where `sys.stdout` lacks a real `fileno()`, and teardown/restoration of stdout/stderr after an Operation), the fixed code SHALL produce the same observable result as the original code, preserving `print()` capture into the Log Panel, `classify_severity` behavior and coloring, the object-level fallback capture, clean restoration of fd 1/fd 2 and `sys.stdout`/`sys.stderr`, and the unchanged module contract.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Approach Selection

Two approaches were considered:

- **Option 1 — OS file-descriptor capture in the Worker (CHOSEN).** For the duration of an Operation, redirect fd 1 (and fd 2) to an OS pipe, run a background reader thread that feeds the pipe's bytes into the existing `LogStream`, and restore the original fds in a `finally` block. This captures **both** Python `print()` output and child-process output, without modifying any module.
- **Option 2 — Capture subprocess output inside `modules/apps.py`.** Run `winget import` with `capture_output=True` (or stream `Popen.stdout`) and `print()` the result so it flows through the existing `redirect_stdout`. This modifies module logic.

**Decision: Option 1.** It is the more general fix: it captures the entire class of "child process writes to fd 1/2" bugs, so the same defect cannot recur in any other module that shells out (now or in the future), and it requires no change to the module contract. Option 2 would fix only `apps.restore` and would leave every other current and future subprocess-spawning module exposed to the identical bug; it also pulls subprocess-capture logic into module code.

**Scope note (explicit):** The original `winsnap-gui` spec declared module logic out of scope ("Modifying the existing export/restore module logic is also out of scope; the GUI invokes that logic unchanged"). Option 1 honors that boundary — the fix lives entirely in the GUI Worker layer and changes no module. This is called out deliberately: had Option 2 been chosen, this bugfix spec would have had to redefine that scope to permit editing `modules/apps.py`. Because Option 1 is chosen, the module-logic-out-of-scope constraint remains intact and is reinforced by Preservation Requirement 3.5.

### Changes Required

Assuming the root-cause analysis above is correct, the change is confined to the GUI Worker layer in `gui.py`.

**File**: `gui.py`

**New helper**: `FdCapture` (a context manager / small class) used by both workers

**Specific Changes**:
1. **Introduce an fd-level capture context manager (`FdCapture`)**:
   - On enter: flush `sys.stdout`/`sys.stderr`; create an OS pipe with `os.pipe()`; save duplicates of the original fd 1 and fd 2 with `os.dup(1)` / `os.dup(2)`; redirect fd 1 and fd 2 to the pipe's write end with `os.dup2(write_fd, 1)` and `os.dup2(write_fd, 2)`.
   - Start a daemon **reader thread** that reads the pipe's read end (e.g. line-buffered via `os.read` accumulation or an `os.fdopen` text wrapper) and writes each chunk/line into the provided `LogStream` (which then emits `log_line` with `classify_severity`), so both child-process and `print()` output funnel through the same path.
   - On exit (`finally`): flush; restore fd 1/fd 2 from the saved duplicates with `os.dup2`; close the pipe write end so the reader thread sees EOF; join the reader thread; close the saved duplicate fds and the pipe read end. Leave `sys.stdout`/`sys.stderr` as they were.
2. **Guard for environments without a real `fileno()` (headless/pytest)**:
   - Before attempting fd capture, probe `sys.stdout.fileno()` (and/or `sys.__stdout__`). If it raises `OSError`/`ValueError`/`io.UnsupportedOperation` or is otherwise unavailable, **fall back** to the existing `contextlib.redirect_stdout(log_stream)` object-level capture path so headless tests and odd hosts keep working (Preservation 3.3).
3. **Wire both workers to use the capture**:
   - In `RestoreWorker.run`, wrap the `mod.restore(modules_data[name], snapshot_dir)` call (around line 1524) so the Operation's fd-level output is captured into the per-module `LogStream` (still connected to `self.log.emit`), replacing/augmenting the bare `with contextlib.redirect_stdout(log_stream):`.
   - In `ExportWorker.run`, apply the same wrapping around `result = export_fn(snapshot_dir)` (around line 1260). Export does not currently leak (its only subprocess uses `capture_output=True`), but applying the same capture keeps the two workers symmetric and protects against any future export-time subprocess output.
4. **Preserve flush/teardown semantics**:
   - Keep the existing `log_stream.flush()` calls so any trailing partial line is emitted, and ensure `FdCapture` teardown runs in a `finally` block so fds are always restored even when a module raises (Preservation 3.4). The existing per-module exception handling and `classify_restore_outcome`/`classify_export_outcome` flow stay unchanged.
5. **No module changes**:
   - `modules/apps.py` and all other modules are left untouched (Preservation 3.5 and the module-logic-out-of-scope boundary).

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface a counterexample that demonstrates the bug on the unfixed code (fd-level/child-process output does not reach the Log Panel); then verify the fix captures that output and that all non-buggy behavior is preserved. Qt objects are exercised headless with `QT_QPA_PLATFORM=offscreen` and a module-level `QApplication.instance() or QApplication(sys.argv)`, mirroring `tests/test_log_stream.py`. Child-process output is simulated at the OS boundary (writing to fd 1 / using the `FakeSubprocess` fixture) rather than invoking real `winget`, so the suite runs on the `windows-latest` CI runners.

### Exploratory Bug Condition Checking

**Goal**: Surface the counterexample that demonstrates the bug BEFORE implementing the fix. Confirm or refute the root-cause analysis (object-level `redirect_stdout` cannot capture fd-level/child-process output). If refuted, re-hypothesize.

**Test Plan**: Construct a `LogStream`, connect its `log_line` signal to a collector, and run a callable that writes to the OS stdout file descriptor (fd 1) — emulating a child process such as `winget import` — inside the same capture context the unfixed Worker uses (`contextlib.redirect_stdout(log_stream)`). Assert that the fd-level output is captured into the Log Panel. On unfixed code this FAILS because `redirect_stdout` never sees fd-level writes.

**Test Cases**:
1. **Direct fd-1 write (emulated child process)**: Inside `redirect_stdout(log_stream)`, write bytes to fd 1 via `os.write(1, ...)`; assert the text is collected via `log_line`. (Will FAIL on unfixed code — proves the bug.)
2. **`subprocess`-style write**: Run a short child process (e.g. `python -c "print(...)"`) without `capture_output=True` inside the capture context; assert its stdout is collected. (Will FAIL on unfixed code.)
3. **`print()` baseline (control)**: A `print()` inside the same context IS collected on both unfixed and fixed code — demonstrating the gap is specific to fd-level output.
4. **No-console scenario**: Assert the captured output is delivered via `log_line` without depending on a console being attached (the collector receives it regardless).

**Expected Counterexamples**:
- Output written to fd 1 (or by a child process) inside `redirect_stdout(log_stream)` does not appear in the collected `log_line` entries on unfixed code.
- Possible alternative causes to rule out: the `LogStream` line-splitting/severity path being broken (refuted — `print()` is captured fine and `tests/test_log_stream.py` passes); the Worker signal wiring being broken (refuted — other modules render).

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed capture routes fd-level output to the Log Panel (Property 1).

**Pseudocode:**
```
FOR ALL X WHERE isBugCondition(X) DO
  collected := runWithFdCapture_fixed(X, log_stream)
  ASSERT outputAppearsInLogPanel(collected, X)
         AND NOT requiresAttachedConsole(X)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed code produces the same result as the original code (Property 2).

**Pseudocode:**
```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain (arbitrary text lines, severities, ordering, and chunk boundaries).
- It catches edge cases that manual unit tests might miss (e.g. lines split across writes, embedded markers).
- It provides strong guarantees that `print()` capture and severity classification are unchanged for all non-buggy inputs.

**Test Plan**: Observe behavior on the UNFIXED code first for `print()` output and `classify_severity`, then write property-based tests asserting that behavior continues after the fix. For the headless fallback, assert that when `sys.stdout` has no real `fileno()` the existing object-level path is used and does not raise.

**Test Cases**:
1. **`print()` preservation (property)**: For arbitrary generated text lines, assert that `print()`-ing them inside the capture yields the same collected `log_line` entries (text + severity) as the original `redirect_stdout(log_stream)` path.
2. **Severity classification preservation (property)**: For arbitrary lines containing/omitting error and warning markers, assert `classify_severity` produces the identical `Severity` before and after the fix.
3. **Headless fallback preservation**: With `sys.stdout` lacking a real `fileno()`, assert the Operation uses the object-level capture path, collects `print()` output, and does not raise (Requirement 3.3).
4. **Teardown/restoration preservation**: After an Operation (including one that raises), assert fd 1/fd 2 and `sys.stdout`/`sys.stderr` are restored to their prior values and no reader thread remains alive (Requirement 3.4).
5. **Export-unchanged preservation**: Assert `_export_winget` still runs with `capture_output=True` and that export output handling is unchanged (Requirement 3.5).

### Unit Tests

- `FdCapture` redirects fd 1/fd 2 to a pipe, delivers written bytes to the `LogStream`, and restores the original fds on exit (including when the body raises).
- The `fileno()` guard correctly detects an unavailable file descriptor and falls back to `redirect_stdout`.
- `LogStream` continues to split lines, classify severity, and emit `log_line` (existing `tests/test_log_stream.py` stays green).
- A simulated `winget import` (via `FakeSubprocess` / a short child process) writing to fd 1 during `RestoreWorker`'s capture results in `log` signal emissions.

### Property-Based Tests

- Generate random sequences of text lines (with and without error/warning markers, split across arbitrary write-chunk boundaries) and assert all are captured in order with correct severities through the fd-capture path (Property 1) and identically through the `print()` path (Property 2). Use `hypothesis` with `@settings(max_examples=...)` >= 50, matching the existing bug tests (`tests/test_winget_schema_bug.py`, `tests/test_mouse_accel_bug.py`, `tests/test_wallpaper_multimon_bug.py`).
- Generate random lines and assert `classify_severity` parity before/after the fix (Property 2).

### Integration Tests

- Drive `RestoreWorker.run` (or its capture wrapper) with a stubbed `apps`-style module whose `restore` writes to fd 1 like `winget import`, under `QT_QPA_PLATFORM=offscreen`; assert the simulated winget lines arrive as `log` emissions with timestamps applied by the Log Panel formatting path.
- Drive `ExportWorker.run` symmetrically to confirm export still captures `print()` output and that the added fd capture introduces no regression.
- Confirm that after a full simulated Operation, stdout/stderr fds are restored and the worker reports the same `ModuleOutcome` classifications as before.
