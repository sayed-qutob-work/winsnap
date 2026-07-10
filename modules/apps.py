"""
apps.py
Captures and restores installed applications using winget.

Export: runs `winget export` to get all winget-known apps, then also reads
        the registry for apps winget doesn't know about (manual installs),
        presenting both lists in the snapshot. Selection can come from the
        interactive terminal checklist (default), from selecting every
        discovered app (`selection="all"`), or from a headless selection
        file (`selection="file"`) — see Req 8 / design Decision D8.

Restore: runs `winget install` **per package** (never a single batch
         `winget import`) so one slow, hanging, or unavailable package can
         never silently abort or truncate the whole run, and so every
         package gets its own reported outcome (installed / already
         installed / unavailable / failed). Manual-only apps are reported
         as skipped items with their reference URL, never as failures. See
         Req 3 / design Decision D3, Process 2.

Filtering (since v0.2):
    The registry scan in _scan_registry_apps now filters out OS components,
    sub-components (entries with a parent), MSI patches, and common noise
    (updaters, runtimes, redistributables, KB updates, drivers, helpers).
    Pass show_all=True to export() to bypass filtering.
"""

import json
import re
import shutil
import subprocess
import winreg
from datetime import datetime
from pathlib import Path

from modules import report


# ---------------------------------------------------------------------------
# winget exit-code classification constants (Req 3.3, 3.4 / design D3)
#
# winget's process exit codes are HRESULT-shaped 32-bit values; Python's
# subprocess.run() surfaces them as *signed* ints, so the well-known HRESULTs
# below are recorded in their signed form. If a future winget release
# renumbers these, the stdout-substring fallback used alongside each constant
# keeps classification working (degrades to a heuristic, never a misreport).
# ---------------------------------------------------------------------------
WINGET_ALREADY_INSTALLED = -1978335135   # 0x8A150061 (APPINSTALLER_CLI_ERROR_PACKAGE_ALREADY_INSTALLED)
WINGET_NO_PACKAGE_FOUND = -1978335212    # 0x8A150014 (APPINSTALLER_CLI_ERROR_NO_APPLICATIONS_FOUND)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

# Patterns that match noisy / non-user-facing entries we don't want in the
# checklist. Matching is case-insensitive against the DisplayName.
_NOISE_PATTERNS = [
    re.compile(r".*\bupdate(r|s)?\b.*",                re.IGNORECASE),
    re.compile(r".*\bhelper\b.*",                      re.IGNORECASE),
    re.compile(r".*\bdriver\b.*",                      re.IGNORECASE),
    re.compile(r".*\bplug[- ]?in\b.*",                 re.IGNORECASE),
    re.compile(r"^kb\d+",                              re.IGNORECASE),
    re.compile(r".*security update.*",                 re.IGNORECASE),
    re.compile(r".*hotfix.*",                          re.IGNORECASE),
    re.compile(r".*microsoft visual c\+\+ \d{4}.*",    re.IGNORECASE),
    re.compile(r".*\.net (framework|runtime|sdk).*",   re.IGNORECASE),
    re.compile(r".*\bredistributable\b.*",             re.IGNORECASE),
    re.compile(r".*\bruntime\b.*",                     re.IGNORECASE),
    re.compile(r".*windows software development kit.*", re.IGNORECASE),
    re.compile(r".*microsoft edge webview.*",          re.IGNORECASE),
    re.compile(r".*windows app runtime.*",             re.IGNORECASE),
]


def _is_noise(name: str) -> bool:
    """Return True if the DisplayName looks like an OS-level/component entry."""
    if not name:
        return True
    return any(p.match(name) for p in _NOISE_PATTERNS)


def _normalize_name(name: str) -> str:
    """
    Normalize a DisplayName for deduplication and cross-matching.

    Strips: version numbers, architecture markers, build IDs, redundant whitespace.
    Lowercases the result.

    Examples:
        "Microsoft Visual Studio Code (User)"  -> "microsoft visual studio code"
        "Discord 1.0.9034"                     -> "discord"
        "Steam (x64)"                          -> "steam"
    """
    if not name:
        return ""
    n = name.lower()
    # Remove parenthetical qualifiers like (x64), (User), (64-bit), etc.
    n = re.sub(r"\(([^)]*)\)", "", n)
    # Remove version numbers (1.2, 1.2.3, 1.2.3.4)
    n = re.sub(r"\b\d+(\.\d+){1,3}\b", "", n)
    # Remove leftover architecture / bitness markers
    n = re.sub(r"\b(x86|x64|x32|32[- ]?bit|64[- ]?bit|amd64|arm64)\b", "", n)
    # Remove "Inc.", "LLC", "Ltd." etc. that sometimes appear
    n = re.sub(r"\b(inc|llc|ltd|corp|corporation|gmbh|co\.?)\b\.?", "", n)
    # Collapse whitespace and trim punctuation
    n = re.sub(r"\s+", " ", n).strip(" -_,.")
    return n


def _winget_id_to_normalized(pkg_id: str) -> str:
    """
    Convert a winget PackageIdentifier (e.g. 'Microsoft.VisualStudioCode')
    into a normalized name comparable to a registry DisplayName.

        'Microsoft.VisualStudioCode' -> 'microsoft visual studio code'
        'Discord.Discord'            -> 'discord discord'  (caller can split)
    """
    if not pkg_id:
        return ""
    # Split on dots, then split CamelCase within each segment
    parts = []
    for seg in pkg_id.split("."):
        # Insert spaces before capitals: "VisualStudioCode" -> "Visual Studio Code"
        spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", seg)
        parts.append(spaced)
    return _normalize_name(" ".join(parts))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(snapshot_dir: Path, show_all: bool = False,
           selection: str = "interactive", selection_file: Path | None = None) -> dict:
    """
    Returns a dict stored under snapshot["apps"].

    Args:
        snapshot_dir:   Directory to write winget_export.json into.
        show_all:       If True, bypass the noise filter on the registry scan.
                        Useful for power users who want every entry.
        selection:      "interactive" (default) | "all" | "file". Controls how
                        the app selection is made (Req 8):
                          - "interactive": launches the terminal checklist via
                            `modules.checklist.run` (attribute lookup at call
                            time, so the GUI's runtime monkey-patch of
                            `checklist.run` keeps working unmodified).
                          - "all": selects every discovered winget + manual
                            app, no UI, no checklist import at all.
                          - "file": loads the selection from `selection_file`
                            (a JSON `{"winget": [ids...], "manual": [names...]}`
                            document), matching against the discovered lists;
                            unmatched entries are recorded as warnings rather
                            than silently dropped.
        selection_file: Path to the JSON selection file. Required (and only
                        used) when selection == "file".
    """
    winget_apps, winget_export_error = _export_winget(snapshot_dir)
    manual_apps = _scan_registry_apps(show_all=show_all)

    # --- Smart deduplication ---
    # Build a set of normalized winget names from PackageIdentifiers so we can
    # exclude registry entries that already correspond to a winget app.
    winget_normalized = set()
    for pkg in winget_apps:
        pid = pkg.get("PackageIdentifier", "")
        # Add the full normalized form
        winget_normalized.add(_winget_id_to_normalized(pid))
        # Also add the last segment alone (catches things like
        # "Discord.Discord" matching "discord")
        if "." in pid:
            last_seg = pid.rsplit(".", 1)[-1]
            spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", last_seg)
            winget_normalized.add(_normalize_name(spaced))

    manual_only = []
    for app in manual_apps:
        norm = _normalize_name(app["name"])
        if norm and norm not in winget_normalized:
            manual_only.append(app)

    suffix = " (show_all=True)" if show_all else ""
    print(f"[apps] Found {len(winget_apps)} winget apps, "
          f"{len(manual_only)} manual-only apps{suffix}.")

    selection_warnings: list = []

    if selection == "all":
        # Headless: select every discovered app, no checklist involved at all
        # (Req 8.1).
        selected_winget = [
            {"PackageIdentifier": pkg.get("PackageIdentifier", "")}
            for pkg in winget_apps
        ]
        selected_manual = list(manual_only)
        print(f"[apps] Headless selection (all): {len(selected_winget)} winget, "
              f"{len(selected_manual)} manual.")

    elif selection == "file":
        # Headless: select from a selection file, no checklist involved
        # (Req 8.2).
        selected_winget, selected_manual, selection_warnings = _select_from_file(
            selection_file, winget_apps, manual_only
        )
        print(f"[apps] Headless selection (file): {len(selected_winget)} winget, "
              f"{len(selected_manual)} manual.")
        for warning in selection_warnings:
            print(f"[apps] WARNING: {warning}")

    else:
        # Interactive default (Req 8.3): keep the exact current call
        # sequence — `from modules import checklist; checklist.run(...)` is
        # an attribute lookup on the module object performed at call time, so
        # the GUI's runtime monkey-patch (gui.py:1228-1230, which replaces
        # `checklist_module.run` before this line ever executes) keeps
        # working unmodified (Req 8.4, 15.6). Do not resolve `checklist.run`
        # any earlier than this call.
        print("[apps] Launching app selection checklist...")
        from modules import checklist
        result = checklist.run(winget_apps, manual_only)

        if result is None:
            print("[apps] Selection cancelled. No apps will be saved.")
            return {
                "winget": [],
                "manual": [],
                "winget_export_error": winget_export_error,
            }

        selected_winget, selected_manual = result

    print(f"[apps] Selected: {len(selected_winget)} winget, "
          f"{len(selected_manual)} manual.")

    # Re-run winget export but filtered to selected packages only
    _write_filtered_winget_export(snapshot_dir, selected_winget)

    result_dict = {
        "winget": selected_winget,
        "manual": selected_manual,
        "winget_export_error": winget_export_error,
    }
    if selection_warnings:
        result_dict["selection_warnings"] = selection_warnings
    return result_dict


def _select_from_file(selection_file, winget_apps: list, manual_apps: list) -> tuple:
    """
    Loads a headless selection file (`{"winget": [ids...], "manual":
    [names...]}`) and matches its entries against the discovered winget/manual
    app lists (Req 8.2).

    Returns (selected_winget, selected_manual, warnings). `warnings` lists
    every requested id/name that did not match a discovered app — those are
    never silently dropped.
    """
    spec = json.loads(Path(selection_file).read_text(encoding="utf-8"))
    wanted_winget_ids = spec.get("winget", [])
    wanted_manual_names = spec.get("manual", [])

    warnings: list = []

    by_id = {pkg.get("PackageIdentifier"): pkg for pkg in winget_apps}
    selected_winget = []
    for pid in wanted_winget_ids:
        if pid in by_id:
            selected_winget.append({"PackageIdentifier": pid})
        else:
            warnings.append(f"winget package not found in export: {pid}")

    by_name = {app.get("name"): app for app in manual_apps}
    selected_manual = []
    for name in wanted_manual_names:
        if name in by_name:
            selected_manual.append(by_name[name])
        else:
            warnings.append(f"manual app not found: {name}")

    return selected_winget, selected_manual, warnings


def _write_filtered_winget_export(snapshot_dir: Path, selected: list):
    """Rewrites winget_export.json to contain only the selected packages."""
    out_file = snapshot_dir / "winget_export.json"
    data = {
        "$schema": "https://aka.ms/winget-packages.schema.2.0.json",
        # Real export timestamp (Req 3.6) — was previously hardcoded to a
        # fixed 2024-01-01 date, which made every snapshot's export file
        # falsely claim the same creation time.
        "CreationDate": datetime.now().astimezone().isoformat(),
        "Sources": [
            {
                "SourceDetails": {
                    "Name": "winget",
                    "Identifier": "Microsoft.Winget.Source_8wekyb3d8bbwe",
                    "Argument": "https://cdn.winget.microsoft.com/cache",
                    "Type": "Microsoft.PreIndexed.Package"
                },
                "Packages": selected
            }
        ]
    }
    out_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _export_winget(snapshot_dir: Path) -> tuple:
    """
    Uses `winget export` to get a JSON list of winget-manageable apps.

    Returns (packages, error_msg): error_msg is None on a clean export, or an
    explicit message on TimeoutExpired / FileNotFoundError / a nonzero exit
    code / invalid JSON (Req 3.5). This lets callers distinguish "winget
    export genuinely found no apps" from "winget export failed and here is an
    empty list" — the two used to look identical.

    Export is a metadata dump, not an install, so a generous fixed timeout
    (120s) is appropriate here (unlike apps.restore's per-package installs,
    which use no timeout at all — see Req 3.2).
    """
    out_file = snapshot_dir / "winget_export.json"
    try:
        result = subprocess.run(
            ["winget", "export", "-o", str(out_file), "--accept-source-agreements"],
            capture_output=True, text=True, timeout=120
        )
        if out_file.exists():
            data = json.loads(out_file.read_text(encoding="utf-8"))
            packages = []
            for source in data.get("Sources", []):
                packages.extend(source.get("Packages", []))
            if result.returncode != 0:
                error_msg = (f"winget export exited {result.returncode}: "
                             f"{result.stderr.strip()}")
                print(f"[apps] WARNING: {error_msg}")
                return packages, error_msg
            return packages, None
        else:
            error_msg = f"winget export failed: {result.stderr.strip()}"
            print(f"[apps] WARNING: {error_msg}")
            return [], error_msg
    except subprocess.TimeoutExpired:
        error_msg = "winget export timed out after 120s"
        print(f"[apps] WARNING: {error_msg}")
        return [], error_msg
    except FileNotFoundError as e:
        error_msg = f"winget not available: {e}"
        print(f"[apps] WARNING: {error_msg}")
        return [], error_msg
    except json.JSONDecodeError as e:
        error_msg = f"winget export produced invalid JSON: {e}"
        print(f"[apps] WARNING: {error_msg}")
        return [], error_msg


def _scan_registry_apps(show_all: bool = False) -> list:
    """
    Reads installed programs from the Windows registry (Add/Remove Programs)
    and filters out OS components, sub-components, patches, and noise.

    Set show_all=True to disable filtering entirely.

    Filters applied (when show_all is False):
      - DisplayName missing or empty
      - SystemComponent == 1
      - ParentKeyName or ParentDisplayName present (sub-components)
      - WindowsInstaller == 1 with no DisplayIcon (MSI patches)
      - ReleaseType is "Update", "Hotfix", or "Security Update"
      - Name matches one of the noise patterns (updaters, runtimes, KB####, ...)
    """
    apps = []
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    # Dedupe by normalized name across all hives
    seen_normalized: set[str] = set()

    for hive, path in reg_paths:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue

        i = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1

            try:
                subkey = winreg.OpenKey(key, subkey_name)
            except OSError:
                continue

            try:
                # --- Pull all the fields we care about ---
                def _get(field, default=None):
                    try:
                        val, _ = winreg.QueryValueEx(subkey, field)
                        return val
                    except OSError:
                        return default

                name = _get("DisplayName")
                if not name:
                    continue

                if not show_all:
                    # Skip OS components
                    if _get("SystemComponent", 0) == 1:
                        continue
                    # Skip sub-components (have a parent in the tree)
                    if _get("ParentKeyName") or _get("ParentDisplayName"):
                        continue
                    # Skip MSI patches: WindowsInstaller=1 + no icon usually
                    # means a patch / silent component.
                    if _get("WindowsInstaller", 0) == 1 and not _get("DisplayIcon"):
                        continue
                    # Skip explicit update/hotfix release types
                    rel_type = (_get("ReleaseType") or "").lower()
                    if rel_type in ("update", "hotfix", "security update"):
                        continue
                    # Skip noise by name
                    if _is_noise(name):
                        continue

                # Dedupe
                norm = _normalize_name(name)
                if not norm or norm in seen_normalized:
                    continue
                seen_normalized.add(norm)

                entry = {"name": name}
                for field in ("DisplayVersion", "Publisher",
                              "InstallLocation", "URLInfoAbout"):
                    val = _get(field)
                    if val:
                        entry[field.lower()] = val
                apps.append(entry)
            finally:
                winreg.CloseKey(subkey)

        winreg.CloseKey(key)

    return sorted(apps, key=lambda a: a["name"].lower())


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(data: dict, snapshot_dir: Path) -> dict:
    """
    Installs winget apps via a per-package `winget install` loop (Req 3.2,
    3.3, 3.4 / design D3, Process 2), then reports the manual list as
    skipped items. Never raises — winget absence, per-package failures, and
    unavailable packages are all recorded on the returned report instead.

    Per package the loop runs `winget install --id <PackageIdentifier>
    --exact --accept-package-agreements --accept-source-agreements
    --disable-interactivity` with **no `timeout` kwarg**: a WinSnap-imposed
    timeout could kill a legitimately slow install mid-flight (Req 3.2), so
    winget owns its own hang behavior here. The loop always continues to the
    next package regardless of outcome (`--ignore-unavailable` semantics,
    Req 3.4) — one bad package can never abort the batch.
    """
    rpt = report.Report("apps", "restore")

    if not shutil.which("winget"):
        print("[apps] winget not found on target; skipping installs.")
        return rpt.skip_all("winget not found on target")

    winget_packages = data.get("winget", [])
    if winget_packages:
        print(f"[apps] Installing {len(winget_packages)} winget package(s)...")

    for pkg in winget_packages:
        pkg_id = pkg.get("PackageIdentifier", "")
        if not pkg_id:
            continue

        result = subprocess.run(
            ["winget", "install", "--id", pkg_id, "--exact",
             "--accept-package-agreements", "--accept-source-agreements",
             "--disable-interactivity"],
            capture_output=True, text=True,
        )
        stdout_lower = (result.stdout or "").lower()

        if result.returncode == 0:
            rpt.add_matched(pkg_id, "installed")
        elif (result.returncode == WINGET_ALREADY_INSTALLED
              or "already installed" in stdout_lower):
            rpt.add_matched(pkg_id, "already installed")
        elif (result.returncode == WINGET_NO_PACKAGE_FOUND
              or "no package found" in stdout_lower):
            rpt.add_skipped(pkg_id, "unavailable")
        else:
            # winget writes its human-readable error to stdout, not stderr,
            # so both streams go into the detail; the code is also rendered
            # as hex because winget exit codes are HRESULT-shaped.
            output_tail = "\n".join(
                s for s in ((result.stdout or "").strip(),
                            (result.stderr or "").strip()) if s
            )[-500:]
            rpt.add_failed(
                pkg_id,
                f"returncode={result.returncode} "
                f"(0x{result.returncode & 0xFFFFFFFF:08X}); {output_tail}"
            )

    manual = data.get("manual", [])
    for app in manual:
        name = app.get("name", "Unknown")
        url = app.get("urlinfoabout", "no URL saved")
        rpt.add_skipped(name, f"manual install required, url={url}")

    return rpt.finalize()


def verify(data: dict, snapshot_dir: Path) -> dict:
    """
    Read-only re-check of what restore() claims to have installed (Req 7.2).

    Winget absent -> whole category skipped. Otherwise each winget package is
    re-checked with `winget list --id <id> --exact --disable-interactivity`
    (returncode 0 -> matched, else failed). Manual apps are inherently not
    programmatically verifiable, so they are always reported skipped rather
    than defaulted to matched (Req 7.6).
    """
    rpt = report.Report("apps", "verify")

    if not shutil.which("winget"):
        return rpt.skip_all("winget not found on target")

    for pkg in data.get("winget", []):
        pkg_id = pkg.get("PackageIdentifier", "")
        if not pkg_id:
            continue
        result = subprocess.run(
            ["winget", "list", "--id", pkg_id, "--exact", "--disable-interactivity"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            rpt.add_matched(pkg_id, "found on target")
        else:
            rpt.add_failed(pkg_id, f"not found (returncode={result.returncode})")

    for app in data.get("manual", []):
        rpt.add_skipped(app.get("name", "Unknown"), "not verifiable programmatically")

    return rpt.finalize()
