"""Tests for the feTurbulence filter primitive."""

import math

import pytest

from . import svg2png
from .turbulence import (
    MAX_PIXELS, parse_base_frequency, turbulence_image, turbulence_rgba)


def pixels_to_rows(pixels, width, height):
    """Split an RGBA byte string into rows of per-pixel tuples."""
    return [
        [tuple(pixels[(y * width + x) * 4: (y * width + x) * 4 + 4])
         for x in range(width)]
        for y in range(height)]


def test_repeatable_with_fixed_seed():
    """Same seed and size give identical pixels."""
    first = turbulence_rgba(
        32, 24, 0.1, 0.05, 2, 42, fractal_sum=True, stitch=False)
    second = turbulence_rgba(
        32, 24, 0.1, 0.05, 2, 42, fractal_sum=True, stitch=False)
    assert first == second
    assert len(first) == 32 * 24 * 4


def test_different_seeds_differ():
    """Different seeds give different pixels."""
    first = turbulence_rgba(
        32, 24, 0.1, 0.1, 2, 1, fractal_sum=True, stitch=False)
    second = turbulence_rgba(
        32, 24, 0.1, 0.1, 2, 2, fractal_sum=True, stitch=False)
    assert first != second


def test_types_differ():
    """fractalNoise and turbulence types give different pixels."""
    fractal = turbulence_rgba(
        32, 24, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=False)
    turbulence = turbulence_rgba(
        32, 24, 0.1, 0.1, 2, 42, fractal_sum=False, stitch=False)
    assert fractal != turbulence


@pytest.mark.parametrize('octaves', (1, 2, 3))
def test_octaves(octaves):
    """1 to 3 octaves are supported and change the result."""
    pixels = turbulence_rgba(
        32, 24, 0.1, 0.1, octaves, 42, fractal_sum=True, stitch=False)
    assert pixels is not None
    reference = turbulence_rgba(
        32, 24, 0.1, 0.1, 1, 42, fractal_sum=True, stitch=False)
    if octaves == 1:
        assert pixels == reference
    else:
        assert pixels != reference


def test_channels_are_noisy():
    """All four channels contain actual noise, not a flat color."""
    pixels = turbulence_rgba(
        32, 32, 0.15, 0.15, 2, 42, fractal_sum=True, stitch=False)
    for channel in range(4):
        values = {pixels[index * 4 + channel]
                  for index in range(32 * 32)}
        assert len(values) > 1


def test_stitch_tiles_seam():
    """With stitchTiles=stitch, a tile's right edge wraps to its left."""
    width, height = 32, 16
    pixels = turbulence_rgba(
        width + 1, height, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=True,
        tile_x=0, tile_y=0, tile_width=width, tile_height=height)
    rows = pixels_to_rows(pixels, width + 1, height)
    # The column just past the tile boundary repeats the first column.
    for row in rows:
        assert row[0] == row[width]
    pixels = turbulence_rgba(
        width, height + 1, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=True,
        tile_x=0, tile_y=0, tile_width=width, tile_height=height)
    rows = pixels_to_rows(pixels, width, height + 1)
    assert rows[0] == rows[height]


def test_stitch_tiles_adjacent():
    """Adjacent tiles of the same stitched tile are identical."""
    width, height = 32, 16
    left = turbulence_rgba(
        width, height, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=True,
        tile_x=0, tile_y=0, tile_width=width, tile_height=height,
        offset_x=0, offset_y=0)
    right = turbulence_rgba(
        width, height, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=True,
        tile_x=0, tile_y=0, tile_width=width, tile_height=height,
        offset_x=width, offset_y=0)
    # The noise is periodic across the tile boundary: the right tile
    # repeats the left one exactly.
    assert left == right


def test_no_stitch_tiles_differ():
    """Without stitching, adjacent tiles are not periodic."""
    width, height = 32, 16
    left = turbulence_rgba(
        width, height, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=False,
        tile_x=0, tile_y=0, tile_width=width, tile_height=height,
        offset_x=0, offset_y=0)
    right = turbulence_rgba(
        width, height, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=False,
        tile_x=0, tile_y=0, tile_width=width, tile_height=height,
        offset_x=width, offset_y=0)
    assert left != right


def test_zero_frequency_is_constant():
    """Zero base frequency gives a constant image."""
    pixels = turbulence_rgba(
        16, 16, 0, 0, 2, 42, fractal_sum=True, stitch=False)
    assert pixels is not None
    assert len(set(pixels)) == 1
    # fractalNoise maps zero noise to 0.5 in every channel.
    assert pixels[0] == 128
    turbulence = turbulence_rgba(
        16, 16, 0, 0, 2, 42, fractal_sum=False, stitch=False)
    assert turbulence is not None
    assert set(turbulence) == {0}


@pytest.mark.parametrize('kwargs', (
    {'base_frequency_x': -1},
    {'base_frequency_y': -0.5},
    {'base_frequency_x': math.inf},
    {'base_frequency_y': math.nan},
    {'seed': math.inf},
    {'seed': math.nan},
    {'num_octaves': 0},
    {'num_octaves': -2},
    {'num_octaves': math.nan},
))
def test_invalid_parameters_are_noop(kwargs):
    """Invalid parameters make the primitive a no-op."""
    arguments = dict(
        width=8, height=8, base_frequency_x=0.1, base_frequency_y=0.1,
        num_octaves=2, seed=42, fractal_sum=True, stitch=False)
    arguments.update(kwargs)
    pixels = turbulence_rgba(**arguments)
    assert pixels is None
    assert turbulence_image(**arguments) is None


def test_empty_region_is_noop():
    """Empty regions generate nothing."""
    assert turbulence_rgba(
        0, 8, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=False) is None
    assert turbulence_rgba(
        8, 0, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=False) is None
    assert turbulence_image(
        0, 0, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=False) is None


def test_huge_region_raises():
    """Regions beyond the resource limit are rejected."""
    width = MAX_PIXELS + 1
    with pytest.raises(ValueError):
        turbulence_rgba(
            width, 1, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=False)
    with pytest.raises(ValueError):
        turbulence_image(
            width, 1, 0.1, 0.1, 2, 42, fractal_sum=True, stitch=False)


def test_image_surface_premultiplied():
    """The generated cairo surface is legal premultiplied ARGB32."""
    width, height = 16, 16
    image = turbulence_image(
        width, height, 0.2, 0.2, 2, 42, fractal_sum=True, stitch=False)
    assert image is not None
    assert image.get_width() == width
    assert image.get_height() == height
    data = memoryview(image.get_data())
    for index in range(width * height):
        blue, green, red, alpha = (
            data[index * 4 + channel] for channel in range(4))
        # Premultiplied color channels never exceed alpha.
        assert 0 <= red <= alpha
        assert 0 <= green <= alpha
        assert 0 <= blue <= alpha
        assert 0 <= alpha <= 255


def test_parse_base_frequency():
    """baseFrequency accepts one or two numbers."""
    assert parse_base_frequency(None) == (0.0, 0.0)
    assert parse_base_frequency('0.1') == (0.1, 0.1)
    assert parse_base_frequency('0.1 0.2') == (0.1, 0.2)
    assert parse_base_frequency('0.1,0.2') == (0.1, 0.2)
    assert parse_base_frequency('0.1 0.2 0.3') is None
    assert parse_base_frequency('abc') is None


def render_filtered_rect(filter_attributes, extra=''):
    """Render a rect using an feTurbulence filter and read back pixels."""
    import cairocffi as cairo

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg"
            width="40" height="30">
      <filter id="noise">
        <feTurbulence {filter_attributes}/>
        {extra}
      </filter>
      <rect x="0" y="0" width="40" height="30" fill="white"
            filter="url(#noise)"/>
    </svg>'''
    png = svg2png(svg)
    image = cairo.ImageSurface.create_from_png(__import__('io').BytesIO(png))
    assert image.get_width() == 40
    assert image.get_height() == 30
    # Copy the pixel data: the buffer dangles once the surface is freed.
    return bytes(memoryview(image.get_data()))


def test_svg_filter_renders_noise():
    """An SVG using feTurbulence renders noise, not a flat color."""
    data = render_filtered_rect(
        'type="fractalNoise" baseFrequency="0.2" numOctaves="2" seed="7"')
    values = {data[index] for index in range(len(data))}
    assert len(values) > 1


def test_svg_filter_repeatable():
    """Rendering the same SVG twice gives identical output."""
    attributes = (
        'type="fractalNoise" baseFrequency="0.2" numOctaves="2" seed="7"')
    assert render_filtered_rect(attributes) == render_filtered_rect(
        attributes)


def test_svg_filter_seed_changes_output():
    """Different seeds in the SVG give different renderings."""
    first = render_filtered_rect(
        'type="fractalNoise" baseFrequency="0.2" seed="1"')
    second = render_filtered_rect(
        'type="fractalNoise" baseFrequency="0.2" seed="2"')
    assert first != second


def test_svg_filter_invalid_is_noop():
    """An invalid feTurbulence leaves the filtered element unchanged."""
    data = render_filtered_rect('baseFrequency="-1"')
    # The white rect is painted untouched.
    assert set(data) == {255}


def test_svg_filter_result_referenced_by_blend():
    """A turbulence result can be referenced by a later primitive."""
    data = render_filtered_rect(
        'type="fractalNoise" baseFrequency="0.2" seed="7" result="noise"',
        extra='<feBlend in2="noise" mode="multiply"/>')
    values = {data[index] for index in range(len(data))}
    assert len(values) > 1


def test_png_output_format():
    """The filtered SVG still exports to a valid PNG."""
    png = svg2png('''<svg xmlns="http://www.w3.org/2000/svg"
            width="20" height="20">
      <filter id="noise">
        <feTurbulence type="turbulence" baseFrequency="0.3" seed="3"/>
      </filter>
      <rect width="20" height="20" filter="url(#noise)"/>
    </svg>''')
    assert png.startswith(b'\211PNG\r\n\032\n')
