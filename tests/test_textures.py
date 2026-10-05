from __future__ import annotations

import struct

import pytest

from unreal_mcp.tools.textures import _encode_png


def test_png_signature_and_chunk_order():
    png = _encode_png(2, 2, [255, 0, 0, 255] * 4)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[12:16] == b"IHDR"
    assert b"IDAT" in png
    assert png[-8:-4] == b"IEND"


def test_ihdr_carries_the_requested_size():
    png = _encode_png(3, 5, [0, 0, 0, 255] * 15)
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (3, 5)


def test_bit_depth_and_colour_type_are_rgba_8bit():
    png = _encode_png(1, 1, [1, 2, 3, 4])
    assert png[24] == 8, "bit depth must be 8"
    assert png[25] == 6, "colour type 6 is RGBA"


def test_pixel_buffer_must_be_exactly_width_times_height_rgba():
    with pytest.raises(ValueError, match="expected"):
        _encode_png(2, 2, [255, 0, 0, 255] * 3)
    with pytest.raises(ValueError, match="expected"):
        _encode_png(0, 2, [])
    with pytest.raises(ValueError, match="expected"):
        _encode_png(-1, -1, [])


def test_payload_is_zlib_compressed_not_stored_raw():
    # A large gradient compresses well, so a correct encoder produces a file far
    # smaller than the 4 * w * h raw bytes it was handed.
    size = 64
    pixels = [(x * 3 + y * 5) % 256 for y in range(size) for x in range(size) for _ in range(3)]
    pixels += [255] * (size * size)
    png = _encode_png(size, size, pixels)
    assert len(png) < size * size * 4


def test_different_pixels_produce_different_files():
    red = _encode_png(2, 2, [255, 0, 0, 255] * 4)
    blue = _encode_png(2, 2, [0, 0, 255, 255] * 4)
    assert red != blue, "the payload must actually encode the colours given"
