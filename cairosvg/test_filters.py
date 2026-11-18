"""
Tests for the raster filter primitives, mainly ``feImage``.

"""

import base64
from io import BytesIO
from urllib.parse import quote_from_bytes

import pytest
from PIL import Image

from . import svg2png


def _png(width=4, height=4, red_rows=2):
    """Build a small RGBA PNG with opaque red top ``red_rows`` rows."""
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    for x in range(width):
        for y in range(red_rows):
            image.putpixel((x, y), (255, 0, 0, 255))
    output = BytesIO()
    image.save(output, 'PNG')
    return output.getvalue()


# 4x4 PNG: an opaque red 2x2 square in the top-left corner, the rest being
# fully transparent.  It exercises both colored and alpha pixels.
PNG_BYTES = _png()
PNG_DATA_URI = 'data:image/png;base64,' + base64.b64encode(PNG_BYTES).decode()
PNG_PERCENT_URI = 'data:image/png,' + quote_from_bytes(PNG_BYTES)
# Fully opaque red square, used where resampling an alpha edge would
# otherwise pollute the sampled colors.
SOLID_PNG_URI = 'data:image/png;base64,' + base64.b64encode(
    _png(width=4, height=4, red_rows=4)).decode()


def render(svg, size=100):
    """Render an SVG and return the Pillow RGBA image."""
    output = svg2png(svg.encode(), output_width=size, output_height=size)
    return Image.open(BytesIO(output)).convert('RGBA')


def filter_svg(body, **attrs):
    attributes = ' '.join(f'{key}="{value}"' for key, value in attrs.items())
    return f'''<svg xmlns="http://www.w3.org/2000/svg"
  xmlns:xlink="http://www.w3.org/1999/xlink" width="100" height="100">
<filter id="f" {attributes}>{body}</filter>
<rect width="100" height="100" fill="white" filter="url(#f)"/>
</svg>'''


def test_fe_image_data_uri_position():
    """feImage places the image at x/y with the requested width/height."""
    image = render(filter_svg(
        f'<feImage href="{PNG_DATA_URI}" x="10" y="20" width="40" '
        'height="40"/>'))
    # The 4x4 image is scaled 10x in the 40x40 rectangle: the red quadrant
    # covers the top-left 20x20 area starting at (10, 20).
    assert image.getpixel((20, 30)) == (255, 0, 0, 255)
    # A terminal feImage replaces the source graphic: transparent parts of
    # the image and areas outside it are fully transparent.
    assert image.getpixel((40, 50)) == (0, 0, 0, 0)
    assert image.getpixel((80, 80)) == (0, 0, 0, 0)


def test_fe_image_xlink_href():
    """The legacy xlink:href attribute is supported."""
    image = render(filter_svg(
        f'<feImage xlink:href="{PNG_DATA_URI}" x="10" y="20" width="40" '
        'height="40"/>'))
    assert image.getpixel((20, 30)) == (255, 0, 0, 255)


def test_fe_image_percent_encoded_data_uri():
    """Non-base64 data URIs are decoded as percent-encoded bytes."""
    image = render(filter_svg(
        f'<feImage href="{PNG_PERCENT_URI}" x="10" y="20" width="40" '
        'height="40"/>'))
    assert image.getpixel((20, 30)) == (255, 0, 0, 255)


def test_fe_image_transparent_layer():
    """The feImage layer is transparent outside the referenced image."""
    image = render(filter_svg(
        f'<feImage href="{PNG_DATA_URI}" x="0" y="0" width="100" '
        'height="100"/>'))
    # Red quadrant.
    assert image.getpixel((10, 10))[0] == 255
    # Bottom-right quadrant is fully transparent.
    assert image.getpixel((80, 80)) == (0, 0, 0, 0)


@pytest.mark.parametrize('alignment, expected_x', (
    ('xMinYMin', 0),
    ('xMidYMid', 20),
    ('xMaxYMax', 40),
))
def test_fe_image_preserve_aspect_ratio_meet(alignment, expected_x):
    """Horizontal position follows preserveAspectRatio in a wide rectangle."""
    image = render(filter_svg(
        f'<feImage href="{SOLID_PNG_URI}" x="0" y="0" width="80" height="40" '
        f'preserveAspectRatio="{alignment} meet"/>'))
    # Square 4x4 image scaled by 10 -> 40x40, vertically filling the
    # rectangle; horizontal placement depends on the alignment.
    assert image.getpixel((expected_x + 8, 8)) == (255, 0, 0, 255)
    # Outside the 40x40 fitted image the replaced output is transparent.
    assert image.getpixel((expected_x + 30, 30)) == (0, 0, 0, 0)


def test_fe_image_preserve_aspect_ratio_none():
    """preserveAspectRatio="none" stretches the image."""
    wide_uri = 'data:image/png;base64,' + base64.b64encode(
        _png(width=8, height=2, red_rows=1)).decode()
    image = render(filter_svg(
        f'<feImage href="{wide_uri}" x="0" y="0" width="100" height="100" '
        'preserveAspectRatio="none"/>'))
    # The red top half is stretched to the top half of the rectangle.
    assert image.getpixel((50, 10))[:3] == (255, 0, 0)
    # The transparent bottom half replaces the source graphic.
    assert image.getpixel((50, 90)) == (0, 0, 0, 0)


def test_fe_image_preserve_aspect_ratio_slice():
    """slice scales to cover and clips the overflowing part."""
    wide_uri = 'data:image/png;base64,' + base64.b64encode(
        _png(width=8, height=2, red_rows=2)).decode()
    image = render(filter_svg(
        f'<feImage href="{wide_uri}" x="10" y="10" width="40" height="40" '
        'preserveAspectRatio="xMidYMid slice"/>'))
    # The clipped image stays inside the rectangle.
    assert image.getpixel((30, 30))[:3] == (255, 0, 0)
    # Outside the rectangle the replaced output is transparent.
    assert image.getpixel((90, 90)) == (0, 0, 0, 0)


def test_fe_image_element_reference():
    """feImage can reference a same-document element."""
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
<defs>
  <rect id="r" x="30" y="30" width="40" height="20" fill="green"/>
  <filter id="f" x="0" y="0" width="100%" height="100%">
    <feImage href="#r" result="img"/>
    <feMerge>
      <feMergeNode in="SourceGraphic"/>
      <feMergeNode in="img"/>
    </feMerge>
  </filter>
</defs>
<rect width="100" height="100" fill="white" filter="url(#f)"/>
</svg>'''
    image = render(svg)
    assert image.getpixel((50, 40)) == (0, 128, 0, 255)
    assert image.getpixel((10, 10)) == (255, 255, 255, 255)


def test_fe_image_result_consumed_by_composite():
    """The feImage result feeds feComposite as in2."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
<filter id="f" x="0" y="0" width="100%" height="100%">
  <feImage href="{PNG_DATA_URI}" x="10" y="20" width="40" height="40"
    result="tex"/>
  <feComposite in="SourceGraphic" in2="tex" operator="in"/>
</filter>
<rect width="100" height="100" fill="blue" filter="url(#f)"/>
</svg>'''
    image = render(svg)
    # In the overlap, operator "in" shows the source (in2 = texture) color
    # clipped by the source graphic's alpha.
    assert image.getpixel((20, 30)) == (255, 0, 0, 255)
    # Outside the texture the source graphic is removed.
    assert image.getpixel((80, 80)) == (0, 0, 0, 0)


def test_fe_merge_stacks_layers():
    """feMerge paints the referenced layers in order."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
<filter id="f" x="0" y="0" width="100%" height="100%">
  <feImage href="{PNG_DATA_URI}" x="10" y="20" width="40" height="40"
    result="tex"/>
  <feMerge>
    <feMergeNode in="SourceGraphic"/>
    <feMergeNode in="tex"/>
  </feMerge>
</filter>
<rect width="100" height="100" fill="blue" filter="url(#f)"/>
</svg>'''
    image = render(svg)
    assert image.getpixel((20, 30)) == (255, 0, 0, 255)
    assert image.getpixel((80, 80)) == (0, 0, 255, 255)


def test_fe_composite_arithmetic():
    """The arithmetic operator applies the channel-wise formula."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
<filter id="f" x="0" y="0" width="100%" height="100%">
  <feImage href="{PNG_DATA_URI}" x="10" y="20" width="40" height="40"
    result="tex"/>
  <feComposite in="tex" in2="SourceGraphic" operator="arithmetic"
    k1="0" k2="0.5" k3="0" k4="0"/>
</filter>
<rect width="100" height="100" fill="blue" filter="url(#f)"/>
</svg>'''
    image = render(svg)
    red, green, blue, alpha = image.getpixel((20, 30))
    # 0.5 * 255 = 127.5 applied to both the red and alpha channels.
    assert red in (127, 128)
    assert green == blue == 0
    assert alpha in (127, 128)
    # Outside the texture nothing is contributed by the half-weight term.
    assert image.getpixel((80, 80)) == (0, 0, 0, 0)


def test_filter_region_clips_fe_image():
    """Primitives cannot paint outside the filter region."""
    image = render(filter_svg(
        f'<feImage href="{SOLID_PNG_URI}" x="30" y="30" width="20" '
        'height="20"/>', x=20, y=20, width=60, height=60))
    assert image.getpixel((40, 40))[:3] == (255, 0, 0)
    # Inside the filter region but outside the image, the replaced result is
    # transparent.
    assert image.getpixel((75, 75)) == (0, 0, 0, 0)
    # Outside the filter region the primitive is clipped, leaving the source
    # graphic untouched.
    assert image.getpixel((5, 5)) == (255, 255, 255, 255)


def test_fe_image_missing_reference():
    """A missing same-document reference gives an empty transparent layer."""
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
<filter id="f" x="0" y="0" width="100%" height="100%">
  <feImage href="#missing" result="img"/>
</filter>
<rect width="100" height="100" fill="teal" filter="url(#f)"/>
</svg>'''
    image = render(svg)
    assert image.getpixel((50, 50)) == (0, 128, 128, 255)


def test_fe_image_circular_reference():
    """Circular element references do not hang and give an empty layer."""
    svg = '''<svg xmlns="http://www.w3.org/2000/svg"
  xmlns:xlink="http://www.w3.org/1999/xlink" width="100" height="100">
<defs>
  <g id="g1"><use href="#g2"/></g>
  <g id="g2"><use href="#g1"/></g>
  <filter id="f" x="0" y="0" width="100%" height="100%">
    <feImage href="#g1" result="img"/>
  </filter>
</defs>
<rect width="100" height="100" fill="teal" filter="url(#f)"/>
</svg>'''
    image = render(svg)
    # The empty layer is stored, never painted: the source is preserved.
    assert image.getpixel((50, 50)) == (0, 128, 128, 255)


def test_fe_image_bad_data_uri_base64():
    """Invalid base64 data gives an empty layer, not an exception."""
    image = render(filter_svg(
        '<feImage href="data:image/png;base64,@@@invalid@@@" '
        'x="0" y="0" width="40" height="40"/>'))
    assert image.getpixel((50, 50)) == (0, 0, 0, 0)


def test_fe_image_bad_data_uri_payload():
    """A data URI whose payload is not an image gives an empty layer."""
    garbage = base64.b64encode(b'this is definitely not a PNG').decode()
    image = render(filter_svg(
        f'<feImage href="data:image/png;base64,{garbage}" '
        'x="0" y="0" width="40" height="40"/>'))
    assert image.getpixel((50, 50)) == (0, 0, 0, 0)


def test_fe_image_oversized_rejected():
    """Images above the pixel limit are rejected."""
    from .filters import MAX_IMAGE_PIXELS
    edge = int(MAX_IMAGE_PIXELS ** 0.5) + 1
    big_uri = 'data:image/png;base64,' + base64.b64encode(
        _png(width=edge, height=edge, red_rows=edge)).decode()
    image = render(filter_svg(
        f'<feImage href="{big_uri}" x="0" y="0" width="100" height="100"/>'))
    assert image.getpixel((50, 50)) == (0, 0, 0, 0)


def test_fe_image_invalid_dimensions():
    """Zero width or height gives an empty layer."""
    image = render(filter_svg(
        f'<feImage href="{PNG_DATA_URI}" x="0" y="0" width="0" '
        'height="40"/>'))
    assert image.getpixel((50, 50)) == (0, 0, 0, 0)
    image = render(filter_svg(
        f'<feImage href="{PNG_DATA_URI}" x="0" y="0" width="40" '
        'height="0"/>'))
    assert image.getpixel((50, 50)) == (0, 0, 0, 0)


def test_fe_image_external_url_safe_mode():
    """External URLs are not fetched in safe mode: the layer stays empty."""
    from .parser import Tree
    from .surface import PNGSurface

    def fetcher(url, resource_type):  # pragma: no cover - must not be called
        raise AssertionError('external URL should not be fetched')

    svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
<filter id="f" x="0" y="0" width="100%" height="100%">
  <feImage href="http://example.invalid/evil.png" result="img"/>
</filter>
<rect width="100" height="100" fill="teal" filter="url(#f)"/>
</svg>'''
    # Default safe fetcher replaces external content with an empty SVG, so the
    # stored layer is empty and the source graphic is preserved.
    image = render(svg)
    assert image.getpixel((50, 50)) == (0, 128, 128, 255)
    # An explicitly restrictive fetcher is honored too.
    tree = Tree(bytestring=svg.encode(), url_fetcher=fetcher)
    with pytest.raises(AssertionError):
        PNGSurface(tree, None, 96)


def test_fe_image_primitive_units_object_bounding_box():
    """primitiveUnits=objectBoundingBox resolves relative to the bbox."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
<filter id="f" primitiveUnits="objectBoundingBox"
  x="-20%" y="-20%" width="140%" height="140%">
  <feImage href="{PNG_DATA_URI}" x="0" y="0" width="1" height="1"/>
</filter>
<rect x="20" y="20" width="60" height="60" fill="white" filter="url(#f)"/>
</svg>'''
    image = render(svg)
    # Image covers the 60x60 bounding box starting at (20, 20).
    assert image.getpixel((35, 35))[:3] == (255, 0, 0)
    assert image.getpixel((5, 5)) == (0, 0, 0, 0)


def test_fe_image_does_not_change_image_element():
    """The plain SVG image element keeps its original behavior."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
<image href="{PNG_DATA_URI}" x="10" y="20" width="40" height="40"/>
</svg>'''
    image = render(svg)
    assert image.getpixel((20, 30)) == (255, 0, 0, 255)
    assert image.getpixel((80, 80)) == (0, 0, 0, 0)
