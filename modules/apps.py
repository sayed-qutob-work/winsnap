"""
apps.py
Captures and restores installed applications using winget.

Export: runs `winget export` to get all winget-known apps, then also reads
        the registry for apps winget doesn't know about (manual installs),
        presenting both lists in the snapshot.

Restore: runs `winget import` for all winget apps, then prints a manual
         install list for anything else.

Filtering (since v0.2):
    The registry scan in _scan_registry_apps now filters out OS components,
    sub-components (entries with a parent), MSI patches, and common noise
    (updaters, runtimes, redistributables, KB updates, drivers, helpers).
    Pass show_all=True to export() to bypass filtering.
"""

import json
import re
import subprocess
import winreg
from pathlib import Path


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

def export(snapshot_dir: Path, show_all: bool = False) -> dict:
    """
    Returns a dict stored under snapshot["apps"].

    Args:
        snapshot_dir: Directory to write winget_export.json into.
        show_all:     If True, bypass the noise filter on the registry scan.
                      Useful for power users who want every entry.
    """
    winget_apps = _export_winget(snapshot_dir)
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

    # --- Interactive checklist ---
    print("[apps] Launching app selection checklist...")
    from modules import checklist
    result = checklist.run(winget_apps, manual_only)

    if result is None:
        print("[apps] Selection cancelled. No apps will be saved.")
        return {"winget": [], "manual": []}

    selected_winget, selected_manual = result
    print(f"[apps] Selected: {len(selected_winget)} winget, "
          f"{len(selected_manual)} manual.")

    # Re-run winget export but filtered to selected packages only
    _write_filtered_winget_export(snapshot_dir, selected_winget)

    return {
        "winget": selected_winget,
        "manual": selected_manual,
    }


def _write_filtered_winget_export(snapshot_dir: Path, selected: list):
    """Rewrites winget_export.json to contain only the selected packages."""
    out_file = snapshot_dir / "winget_export.json"
    data = {
        "$schema": "https://aka.ms/winget-packages.schema.2.0.json",
        "CreationDate": "2024-01-01T00:00:00.000-00:00",
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


def _export_winget(snapshot_dir: Path) -> list:
    """Uses `winget export` to get a JSON list of winget-manageable apps."""
    out_file = snapshot_dir / "winget_export.json"
    try:
        result = subprocess.run(
            ["winget", "export", "-o", str(out_file), "--accept-source-agreements"],
            capture_output=True, text=True, timeout=60
        )
        if out_file.exists():
            data = json.loads(out_file.read_text(encoding="utf-8"))
            packages = []
            for source in data.get("Sources", []):
                packages.extend(source.get("Packages", []))
            return packages
        else:
            print(f"[apps] winget export failed: {result.stderr.strip()}")
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"[apps] winget not available or failed: {e}")
        return []


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

def restore(snapshot: dict, snapshot_dir: Path):
    """
    Installs winget apps via `winget import`, then prints the manual list.
    """
    winget_file = snapshot_dir / "winget_export.json"
    if winget_file.exists() and snapshot.get("winget"):
        print("[apps] Installing winget apps...")
        result = subprocess.run(
            ["winget", "import", "-i", str(winget_file),
             "--accept-package-agreements", "--accept-source-agreements"],
            timeout=600
        )
        if result.returncode == 0:
            print("[apps] winget apps installed successfully.")
        else:
            print("[apps] Some winget apps may have failed. Check output above.")
    else:
        print("[apps] No winget apps to install.")

    manual = snapshot.get("manual", [])
    if manual:
        print("\n[apps] The following apps need manual installation:")
        for app in manual:
            url = app.get("urlinfoabout", "no URL saved")
            print(f"  - {app['name']} | {url}")
