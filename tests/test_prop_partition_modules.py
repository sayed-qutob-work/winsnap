"""
test_prop_partition_modules.py — Property-based tests for
restore.partition_modules, including a parity guard against run_modules's
own inline membership check.

Feature: gui-backend-alignment, Task 1.3 (Req 2.2, 2.6)

partition_modules is a deliberate, hand-mirrored copy of the two-line
membership check inline in run_modules's loop (`key not in modules_data` /
`"error" in data`) -- run_modules itself is left untouched per the design
notes, so this duplication is intentional and needs a guard against drift.
The parity test below runs both partition_modules and the real run_modules
(with module functions mocked to return a trivial matched report) over the
same arbitrary modules_to_run/modules_data and asserts the set of keys
run_modules actually reports on equals partition_modules's attemptable set.
"""

import contextlib
import io
import sys
import types
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import restore as restore_module
from modules import report as report_module


# ---------------------------------------------------------------------------
# Stub module helper -- returns a trivial matched report, never touches the
# registry, and records nothing else (parity tests only care about which
# keys get a report at all, not their content).
# ---------------------------------------------------------------------------

def _make_matched_stub():
    mod = types.SimpleNamespace()

    def restore(data, snapshot_dir):
        rpt = report_module.Report("stub", "restore")
        rpt.add_matched("thing")
        return rpt.finalize()

    mod.restore = restore
    return mod


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_KEYS = ["mod_a", "mod_b", "mod_c", "mod_d", "mod_e"]

# A subset (order-preserving, unique) of _KEYS to act as modules_to_run.
_modules_to_run_keys = st.lists(
    st.sampled_from(_KEYS), unique=True, min_size=0, max_size=len(_KEYS)
)

# For each key that might appear in modules_to_run, independently decide its
# presence in modules_data: absent, present-with-error, present-without-error.
_presence = st.sampled_from(["absent", "error", "ok"])


def _build_modules_data(keys: list, presences: list) -> dict:
    data: dict = {}
    for key, presence in zip(keys, presences):
        if presence == "absent":
            continue
        elif presence == "error":
            data[key] = {"error": "export blew up"}
        else:
            data[key] = {"some": "payload"}
    return data


# ---------------------------------------------------------------------------
# Unit-level classification correctness
# ---------------------------------------------------------------------------

def test_key_absent_from_modules_data_is_not_found_in_snapshot():
    modules_to_run = [("mod_a", _make_matched_stub())]
    attemptable, skipped = restore_module.partition_modules(modules_to_run, {})
    assert attemptable == []
    assert skipped == {"mod_a": "not_found_in_snapshot"}


def test_key_present_with_error_dict_is_export_error():
    stub = _make_matched_stub()
    modules_to_run = [("mod_a", stub)]
    modules_data = {"mod_a": {"error": "boom"}}
    attemptable, skipped = restore_module.partition_modules(modules_to_run, modules_data)
    assert attemptable == []
    assert skipped == {"mod_a": "export_error"}


def test_key_present_without_error_is_attemptable():
    stub = _make_matched_stub()
    modules_to_run = [("mod_a", stub)]
    modules_data = {"mod_a": {"some": "payload"}}
    attemptable, skipped = restore_module.partition_modules(modules_to_run, modules_data)
    assert attemptable == [("mod_a", stub)]
    assert skipped == {}


def test_non_dict_data_is_treated_as_attemptable():
    """A list/str/etc payload has no 'error' key check applicable via `in` on
    a dict -- isinstance guard means non-dict data is never classified as
    export_error, matching run_modules's own `isinstance(data, dict)` guard."""
    stub = _make_matched_stub()
    modules_to_run = [("mod_a", stub)]
    modules_data = {"mod_a": ["a", "list", "payload"]}
    attemptable, skipped = restore_module.partition_modules(modules_to_run, modules_data)
    assert attemptable == [("mod_a", stub)]
    assert skipped == {}


def test_order_of_attemptable_preserves_modules_to_run_order():
    stub_a, stub_b, stub_c = (_make_matched_stub() for _ in range(3))
    modules_to_run = [("mod_a", stub_a), ("mod_b", stub_b), ("mod_c", stub_c)]
    modules_data = {"mod_a": {}, "mod_b": {}, "mod_c": {}}
    attemptable, skipped = restore_module.partition_modules(modules_to_run, modules_data)
    assert attemptable == modules_to_run
    assert skipped == {}


# ---------------------------------------------------------------------------
# Parity property: partition_modules's attemptable set == the set of keys
# run_modules actually reports on, for arbitrary modules_to_run/modules_data.
# ---------------------------------------------------------------------------

@given(keys=_modules_to_run_keys, data=st.data())
@settings(max_examples=100)
def test_partition_modules_parity_with_run_modules(keys, data):
    presences = [data.draw(_presence, label=f"presence_{k}") for k in keys]
    modules_data = _build_modules_data(keys, presences)

    modules_to_run = [(key, _make_matched_stub()) for key in keys]

    attemptable, _skipped = restore_module.partition_modules(modules_to_run, modules_data)
    expected_keys = {key for key, _ in attemptable}

    # Stub modules never touch snapshot_dir, so a fixed dummy Path (rather
    # than a function-scoped tmp_path fixture, which hypothesis's health
    # checks correctly flag as unsafe to share across generated examples)
    # is sufficient here.
    dummy_snapshot_dir = Path("dummy_snapshot_dir")

    with contextlib.redirect_stdout(io.StringIO()):
        reports = restore_module.run_modules(
            modules_to_run, modules_data, dummy_snapshot_dir, dry_run=False
        )

    assert set(reports.keys()) == expected_keys


@given(keys=_modules_to_run_keys, data=st.data())
@settings(max_examples=100)
def test_partition_modules_covers_every_module_to_run(keys, data):
    """Every key in modules_to_run ends up in exactly one of attemptable or
    skipped, and skipped never contains a key that is attemptable."""
    presences = [data.draw(_presence, label=f"presence_{k}") for k in keys]
    modules_data = _build_modules_data(keys, presences)
    modules_to_run = [(key, _make_matched_stub()) for key in keys]

    attemptable, skipped = restore_module.partition_modules(modules_to_run, modules_data)
    attemptable_keys = {key for key, _ in attemptable}

    assert attemptable_keys | set(skipped) == set(keys)
    assert attemptable_keys.isdisjoint(skipped)
    assert set(skipped.values()) <= {"not_found_in_snapshot", "export_error"}
