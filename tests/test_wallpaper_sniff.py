"""
Unit tests for modules/winutil.py's image-sniffing and hashing helpers.

Covers the magic-byte table from design Decision D4 (Req 5.2, 5.3) —
jpg/png/bmp/gif recognition, extensionless "TranscodedWallpaper"-style
filenames, unknown/short/empty files — plus a sanity check for
sha256_file (Req 5.7 relies on it for wallpaper-verify content hashing).

Task 6 extends this file with wallpaper-module-level tests (style/tile
capture, SPI ordering, verify) once modules/wallpaper.py is hardened.
"""

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules import winutil


# ---------------------------------------------------------------------------
# sniff_image_type — magic-byte table
# ---------------------------------------------------------------------------

# (header bytes, expected format) for each recognized image type. Filenames
# used in the tests below are extensionless, mirroring the real-world case
# this function exists for: Windows' TranscodedWallpaper cache file.
MAGIC_BYTE_CASES = [
    pytest.param(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01" + b"\x00" * 10, "jpg", id="jpeg"),
    pytest.param(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10, "png", id="png"),
    pytest.param(b"BM" + b"\x00" * 20, "bmp", id="bmp"),
    pytest.param(b"GIF89a" + b"\x00" * 10, "gif", id="gif89a"),
    pytest.param(b"GIF87a" + b"\x00" * 10, "gif", id="gif87a"),
]


@pytest.mark.parametrize("header, expected_format", MAGIC_BYTE_CASES)
def test_sniff_image_type_recognizes_known_formats(tmp_path, header, expected_format):
    path = tmp_path / "TranscodedWallpaper"
    path.write_bytes(header)
    assert winutil.sniff_image_type(path) == expected_format


def test_sniff_image_type_unknown_bytes_returns_none(tmp_path):
    path = tmp_path / "TranscodedWallpaper"
    path.write_bytes(b"NOT_AN_IMAGE_HEADER_AT_ALL_")
    assert winutil.sniff_image_type(path) is None


def test_sniff_image_type_short_file_returns_none(tmp_path):
    """A file shorter than any signature must not raise and must return None."""
    path = tmp_path / "tiny"
    path.write_bytes(b"\xFF")
    assert winutil.sniff_image_type(path) is None


def test_sniff_image_type_empty_file_returns_none(tmp_path):
    path = tmp_path / "empty"
    path.write_bytes(b"")
    assert winutil.sniff_image_type(path) is None


def test_sniff_image_type_missing_file_returns_none(tmp_path):
    path = tmp_path / "does_not_exist"
    assert winutil.sniff_image_type(path) is None


# ---------------------------------------------------------------------------
# sha256_file — sanity
# ---------------------------------------------------------------------------

def test_sha256_file_matches_hashlib_reference(tmp_path):
    content = b"some wallpaper bytes, not actually an image but hashable" * 100
    path = tmp_path / "wallpaper.bin"
    path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert winutil.sha256_file(path) == expected


def test_sha256_file_empty_file(tmp_path):
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert winutil.sha256_file(path) == hashlib.sha256(b"").hexdigest()


def test_sha256_file_differs_for_different_content(tmp_path):
    path_a = tmp_path / "a.bin"
    path_b = tmp_path / "b.bin"
    path_a.write_bytes(b"content A")
    path_b.write_bytes(b"content B")
    assert winutil.sha256_file(path_a) != winutil.sha256_file(path_b)
