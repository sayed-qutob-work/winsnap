"""
Smoke test for modules/apps.py filtering & normalization.

Run from the project root:
    python tests/smoke_apps.py

Does NOT launch the interactive checklist. It only exercises:
  - _normalize_name
  - _is_noise
  - _winget_id_to_normalized
  - _scan_registry_apps with show_all=False vs True (compares counts)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules import apps


def assert_eq(actual, expected, label):
    status = "PASS" if actual == expected else "FAIL"
    print(f"  [{status}] {label}")
    if actual != expected:
        print(f"         expected: {expected!r}")
        print(f"         got:      {actual!r}")
    return actual == expected


def test_normalize_name():
    print("\n=== _normalize_name ===")
    cases = [
        ("Microsoft Visual Studio Code (User)", "microsoft visual studio code"),
        ("Discord 1.0.9034",                    "discord"),
        ("Steam (x64)",                         "steam"),
        ("Google Chrome",                       "google chrome"),
        ("7-Zip 23.01 (x64)",                   "7-zip"),
        ("",                                    ""),
        ("NVIDIA Graphics Driver 546.33",       "nvidia graphics driver"),
    ]
    ok = True
    for inp, expected in cases:
        ok &= assert_eq(apps._normalize_name(inp), expected, f"{inp!r}")
    return ok


def test_is_noise():
    print("\n=== _is_noise ===")
    noise = [
        "Microsoft Edge Update",
        "Google Update Helper",
        "NVIDIA Display Driver",
        "KB5031356",
        "Microsoft Visual C++ 2015 Redistributable",
        "Microsoft .NET Framework 4.8",
        "Microsoft Edge WebView2 Runtime",
        "Security Update for Windows",
        "Windows Software Development Kit",
    ]
    not_noise = [
        "Discord",
        "Steam",
        "Google Chrome",
        "Visual Studio Code",
        "7-Zip 23.01",
        "Spotify",
    ]
    ok = True
    for n in noise:
        ok &= assert_eq(apps._is_noise(n), True, f"{n!r} -> noise")
    for n in not_noise:
        ok &= assert_eq(apps._is_noise(n), False, f"{n!r} -> NOT noise")
    return ok


def test_winget_id_to_normalized():
    print("\n=== _winget_id_to_normalized ===")
    cases = [
        ("Microsoft.VisualStudioCode",    "microsoft visual studio code"),
        ("Discord.Discord",               "discord discord"),
        ("Valve.Steam",                   "valve steam"),
        ("Google.Chrome",                 "google chrome"),
        ("",                              ""),
    ]
    ok = True
    for pid, expected in cases:
        ok &= assert_eq(apps._winget_id_to_normalized(pid), expected, f"{pid!r}")
    return ok


def test_registry_scan_filtering():
    print("\n=== _scan_registry_apps: filtered vs show_all ===")
    filtered = apps._scan_registry_apps(show_all=False)
    full     = apps._scan_registry_apps(show_all=True)
    print(f"  filtered:  {len(filtered)} apps")
    print(f"  show_all:  {len(full)} apps")
    print(f"  reduction: {len(full) - len(filtered)} entries hidden "
          f"({(1 - len(filtered)/max(len(full),1))*100:.1f}%)")

    # Sanity: filtered list should be a subset of (or equal to) full count
    if len(filtered) > len(full):
        print("  [FAIL] filtered count is larger than show_all count!")
        return False

    # Show a sample of what made it through
    print("\n  Sample of filtered (first 10):")
    for app in filtered[:10]:
        print(f"    - {app['name']}")

    # Show a sample of what was filtered out
    filtered_names = {a["name"] for a in filtered}
    hidden = [a for a in full if a["name"] not in filtered_names]
    print(f"\n  Sample of hidden (first 10):")
    for app in hidden[:10]:
        print(f"    - {app['name']}")

    return True


def main():
    results = [
        test_normalize_name(),
        test_is_noise(),
        test_winget_id_to_normalized(),
        test_registry_scan_filtering(),
    ]
    print("\n" + "=" * 50)
    if all(results):
        print("  All smoke tests PASSED")
        sys.exit(0)
    else:
        print("  Some tests FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
