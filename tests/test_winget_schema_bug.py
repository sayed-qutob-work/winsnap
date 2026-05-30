"""
Property-based exploration test for Bug Condition 1: Invalid winget import JSON.

**Validates: Requirements 1.1, 2.1**

Goal: Surface counterexamples demonstrating that `_write_filtered_winget_export`
omits the `$schema` field from the written JSON. Without `$schema`, winget rejects
the file as "not specifying a recognized schema" and installs nothing.

Bug Condition (from design):
    isBugCondition_apps(X) = documentHasSelectedPackages(X) AND NOT hasField(X, "$schema")

Expected Behavior (Property 1):
    The written JSON has a recognized top-level `$schema` field AND the exact
    selected packages are preserved.

This test is EXPECTED TO FAIL on unfixed code — that failure confirms the bug exists.
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.apps import _write_filtered_winget_export


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A winget package identifier looks like "Publisher.AppName" or "Publisher.AppName.Sub"
_package_id_segment = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), min_codepoint=65, max_codepoint=122),
    min_size=2,
    max_size=20,
)

_package_identifier = st.builds(
    lambda parts: ".".join(parts),
    st.lists(_package_id_segment, min_size=2, max_size=3),
)

_package_entry = st.builds(
    lambda pid: {"PackageIdentifier": pid},
    _package_identifier,
)

# Non-empty list of selected packages (bug condition requires at least one package)
_selected_packages = st.lists(_package_entry, min_size=1, max_size=10)


# ---------------------------------------------------------------------------
# Property Test
# ---------------------------------------------------------------------------

@given(selected=_selected_packages)
@settings(max_examples=50, deadline=5000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_winget_export_has_schema_and_preserves_packages(tmp_path, selected):
    """
    Property 1: Bug Condition — Invalid winget import JSON

    **Validates: Requirements 1.1, 2.1**

    For any non-empty set of selected winget packages, the written
    winget_export.json MUST contain:
      1. A top-level `$schema` field (so winget accepts the file)
      2. The exact selected packages preserved in the Sources/Packages array

    Bug Condition: documentHasSelectedPackages(X) AND NOT hasField(X, "$schema")

    On UNFIXED code this test FAILS because `_write_filtered_winget_export`
    constructs the JSON with only `Sources` and no `$schema`.
    """
    # Use a unique subdirectory per example to avoid cross-contamination
    snapshot_dir = tmp_path / f"snapshot_{id(selected)}"
    snapshot_dir.mkdir(exist_ok=True)

    # Act: write the filtered export
    _write_filtered_winget_export(snapshot_dir, selected)

    # Load the written file
    out_file = snapshot_dir / "winget_export.json"
    assert out_file.exists(), "winget_export.json was not written"
    data = json.loads(out_file.read_text(encoding="utf-8"))

    # Assert 1: $schema field is present at the top level
    assert "$schema" in data, (
        f"Bug confirmed: winget_export.json has no '$schema' field. "
        f"Top-level keys are: {list(data.keys())}. "
        f"winget will reject this file as 'not specifying a recognized schema'."
    )

    # Assert 2: The selected packages are preserved exactly
    sources = data.get("Sources", [])
    assert len(sources) >= 1, "Expected at least one source entry"
    written_packages = sources[0].get("Packages", [])
    assert written_packages == selected, (
        f"Packages mismatch: expected {selected}, got {written_packages}"
    )
