"""
test_bundled_files.py — tests for cursor/sound file bundling (Task 10).

Feature: backend-roundtrip-hardening.

Covers, for both modules/cursors.py and modules/sound_scheme.py:
  - export() bundles only files that live outside the Windows default
    directory (%SystemRoot%\\Cursors / %SystemRoot%\\Media), recording a
    "bundled" map; a source file absent at export time is recorded
    "missing": true rather than dropped (Req 10.1, 10.2, 10.4).
  - restore() copies a bundled file to the stable per-user location
    (%LOCALAPPDATA%\\WinSnap\\cursors|media) and writes the *rewritten* target
    path to the registry, not the source-machine path (Req 10.3).
  - a "missing": true entry is skipped at restore with reason, never a
    dangling path write (Req 10.4).
  - a 0.2.0 snapshot (no "bundled" key) restores verbatim with a skipped
    "snapshot predates bundling" item (Req 14.2).
  - verify() reports matched when the restored path exists and failed when it
    does not (Req 10.5).

**Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 14.2**
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import FakeWinReg, _build_winreg_module

import modules.cursors as cursors
import modules.sound_scheme as sound_scheme


class _FakeUser32:
    def SystemParametersInfoW(self, *a):
        return 1


class _FakeWindll:
    user32 = _FakeUser32()


class _FakeCtypes:
    """Minimal ctypes stand-in so cursors.restore's SPI_SETCURSORS call is a
    no-op returning success."""
    windll = _FakeWindll()


# ===========================================================================
# cursors
# ===========================================================================

def _patch_cursors(monkeypatch, fake, tmp_path):
    monkeypatch.setattr(cursors, "winreg", _build_winreg_module(fake))
    monkeypatch.setattr(cursors, "ctypes", _FakeCtypes())
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))


def test_cursors_export_bundles_only_outside_default(monkeypatch, tmp_path):
    fake = FakeWinReg()
    outside = tmp_path / "MyCursors" / "arrow.cur"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"CURSOR")
    # Arrow lives outside the default dir -> bundled; Wait lives inside -> not.
    fake.values[(fake.HKEY_CURRENT_USER, cursors._PATH, "Arrow")] = (str(outside), fake.REG_SZ)
    fake.values[(fake.HKEY_CURRENT_USER, cursors._PATH, "Wait")] = (r"C:\Windows\Cursors\wait.ani", fake.REG_SZ)
    _patch_cursors(monkeypatch, fake, tmp_path)

    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    data = cursors.export(snapshot_dir)

    assert "Arrow" in data["bundled"]
    assert data["bundled"]["Arrow"]["missing"] is False
    assert data["bundled"]["Arrow"]["filename"] == "cursors/arrow.cur"
    assert "Wait" not in data["bundled"]              # inside default dir
    assert (snapshot_dir / "cursors" / "arrow.cur").exists()


def test_cursors_export_records_missing_source(monkeypatch, tmp_path):
    fake = FakeWinReg()
    fake.values[(fake.HKEY_CURRENT_USER, cursors._PATH, "Arrow")] = (
        str(tmp_path / "gone" / "arrow.cur"), fake.REG_SZ)
    _patch_cursors(monkeypatch, fake, tmp_path)

    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    data = cursors.export(snapshot_dir)

    assert data["bundled"]["Arrow"]["missing"] is True
    assert data["bundled"]["Arrow"]["filename"] is None


def test_cursors_restore_rewrites_registry_to_local_appdata(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch_cursors(monkeypatch, fake, tmp_path)

    # A bundled cursor file physically present in the snapshot.
    snapshot_dir = tmp_path / "snap"
    (snapshot_dir / "cursors").mkdir(parents=True)
    (snapshot_dir / "cursors" / "arrow.cur").write_bytes(b"CURSOR")

    snapshot = {
        "scheme": None, "scheme_source": None,
        "cursors": {"Arrow": r"C:\Users\srcuser\arrow.cur"},
        "bundled": {"Arrow": {"filename": "cursors/arrow.cur",
                              "original_path": r"C:\Users\srcuser\arrow.cur",
                              "missing": False}},
    }
    report = cursors.restore(snapshot, snapshot_dir)

    arrow_writes = fake.get_writes_for("Arrow")
    assert len(arrow_writes) == 1
    written_value = arrow_writes[0][5]
    assert "LocalAppData" in written_value and "WinSnap" in written_value
    assert r"C:\Users\srcuser" not in written_value    # source path NOT written
    assert (Path(written_value)).exists()              # file physically placed
    arrow_item = next(i for i in report["items"] if i["name"] == "Arrow")
    assert arrow_item["status"] == "matched"


def test_cursors_restore_skips_missing_entry(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch_cursors(monkeypatch, fake, tmp_path)

    snapshot = {
        "cursors": {"Arrow": r"C:\Users\srcuser\arrow.cur"},
        "bundled": {"Arrow": {"filename": None,
                              "original_path": r"C:\Users\srcuser\arrow.cur",
                              "missing": True}},
    }
    report = cursors.restore(snapshot, tmp_path)

    assert fake.get_writes_for("Arrow") == []          # no dangling path written
    arrow_item = next(i for i in report["items"] if i["name"] == "Arrow")
    assert arrow_item["status"] == "skipped"


def test_cursors_restore_020_no_bundled_writes_verbatim(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch_cursors(monkeypatch, fake, tmp_path)

    snapshot = {"cursors": {"Arrow": r"C:\Windows\Cursors\arrow.cur"}}  # no "bundled"
    report = cursors.restore(snapshot, tmp_path)

    arrow_writes = fake.get_writes_for("Arrow")
    assert arrow_writes[0][5] == r"C:\Windows\Cursors\arrow.cur"   # verbatim
    assert any(i["name"] == "bundled files" and i["status"] == "skipped"
               for i in report["items"])


def test_cursors_verify_matched_and_failed(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch_cursors(monkeypatch, fake, tmp_path)

    present = tmp_path / "present.cur"
    present.write_bytes(b"CURSOR")
    fake.values[(fake.HKEY_CURRENT_USER, cursors._PATH, "Arrow")] = (str(present), fake.REG_SZ)
    fake.values[(fake.HKEY_CURRENT_USER, cursors._PATH, "Hand")] = (
        str(tmp_path / "gone.cur"), fake.REG_SZ)

    data = {"scheme": None, "scheme_source": None,
            "cursors": {"Arrow": str(present), "Hand": str(tmp_path / "gone.cur")},
            "bundled": {}}
    report = cursors.verify(data, tmp_path)

    arrow_item = next(i for i in report["items"] if i["name"] == "Arrow")
    hand_item = next(i for i in report["items"] if i["name"] == "Hand")
    assert arrow_item["status"] == "matched"
    assert hand_item["status"] == "failed"
    assert fake.writes == []                           # verify is read-only


# ===========================================================================
# sound_scheme
# ===========================================================================

def _patch_sound(monkeypatch, fake, tmp_path):
    monkeypatch.setattr(sound_scheme, "winreg", _build_winreg_module(fake))
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))


def test_sound_restore_rewrites_registry_to_local_appdata(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch_sound(monkeypatch, fake, tmp_path)

    snapshot_dir = tmp_path / "snap"
    (snapshot_dir / "media").mkdir(parents=True)
    (snapshot_dir / "media" / "ding.wav").write_bytes(b"RIFFwav")

    snapshot = {
        "scheme": ".Custom", "beep": None,
        "event_sounds": {"Explorer/Navigating": r"C:\Users\srcuser\ding.wav"},
        "bundled": {"Explorer/Navigating": {"filename": "media/ding.wav",
                    "original_path": r"C:\Users\srcuser\ding.wav", "missing": False}},
    }
    report = sound_scheme.restore(snapshot, snapshot_dir)

    # The .Current default value is written via _set_default (name "").
    current_writes = [w for w in fake.writes if w[2] == "" and "ding.wav" in str(w[5])]
    assert current_writes, "expected a .Current write pointing at the placed wav"
    written = current_writes[0][5]
    assert "LocalAppData" in written and "WinSnap" in written
    assert r"C:\Users\srcuser" not in written
    assert Path(written).exists()
    item = next(i for i in report["items"] if i["name"] == "Explorer/Navigating")
    assert item["status"] == "matched"


def test_sound_restore_skips_missing_entry(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch_sound(monkeypatch, fake, tmp_path)

    snapshot = {
        "scheme": None, "beep": None,
        "event_sounds": {"Explorer/Navigating": r"C:\Users\srcuser\ding.wav"},
        "bundled": {"Explorer/Navigating": {"filename": None,
                    "original_path": r"C:\Users\srcuser\ding.wav", "missing": True}},
    }
    report = sound_scheme.restore(snapshot, tmp_path)

    assert not any("ding.wav" in str(w[5]) for w in fake.writes)   # no dangling write
    item = next(i for i in report["items"] if i["name"] == "Explorer/Navigating")
    assert item["status"] == "skipped"


def test_sound_restore_020_no_bundled_skips_with_reason(monkeypatch, tmp_path):
    fake = FakeWinReg()
    _patch_sound(monkeypatch, fake, tmp_path)

    snapshot = {"scheme": ".Default", "beep": None,
                "event_sounds": {"Explorer/Navigating": r"C:\Windows\Media\ding.wav"}}
    report = sound_scheme.restore(snapshot, tmp_path)

    assert any(i["name"] == "bundled files" and i["status"] == "skipped"
               for i in report["items"])
