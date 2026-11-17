"""
feConvolveMatrix filter tests.

These tests render a fixed 3x3 pixel image and check the colors, the alpha
channel and the edges of the convolved result.

"""

from . import parser, surface

# 3x3 image, one 1x1 rect per pixel, distinct opaque colors:
#   (255,0,0)   (0,255,0)   (0,0,255)
#   (255,255,0) (0,255,255) (255,0,255)
#   (255,255,255) (0,0,0)   (128,128,128)
RECTS = '''
    <rect x="0" y="0" width="1" height="1" fill="#ff0000"/>
    <rect x="1" y="0" width="1" height="1" fill="#00ff00"/>
    <rect x="2" y="0" width="1" height="1" fill="#0000ff"/>
    <rect x="0" y="1" width="1" height="1" fill="#ffff00"/>
    <rect x="1" y="1" width="1" height="1" fill="#00ffff"/>
    <rect x="2" y="1" width="1" height="1" fill="#ff00ff"/>
    <rect x="0" y="2" width="1" height="1" fill="#ffffff"/>
    <rect x="1" y="2" width="1" height="1" fill="#000000"/>
    <rect x="2" y="2" width="1" height="1" fill="#808080"/>
'''

# Same image with alpha variations:
#   (255,0,0,255) (0,255,0,255) (0,0,255,255)
#   (0,0,0,255)   (255,0,0,128) (255,255,255,255)
#   (0,0,0,0)     (0,255,255,255) (255,0,255,255)
RECTS_ALPHA = '''
    <rect x="0" y="0" width="1" height="1" fill="#ff0000"/>
    <rect x="1" y="0" width="1" height="1" fill="#00ff00"/>
    <rect x="2" y="0" width="1" height="1" fill="#0000ff"/>
    <rect x="0" y="1" width="1" height="1" fill="#000000"/>
    <rect x="1" y="1" width="1" height="1" fill="#ff0000" fill-opacity="0.5"/>
    <rect x="2" y="1" width="1" height="1" fill="#ffffff"/>
    <rect x="0" y="2" width="1" height="1" fill="#000000" fill-opacity="0"/>
    <rect x="1" y="2" width="1" height="1" fill="#00ffff"/>
    <rect x="2" y="2" width="1" height="1" fill="#ff00ff"/>
'''

# Grayscale image for the sharpen test (values chosen to avoid clipping):
#   100 100 100
#   100 120 100
#   100 100 100
RECTS_SHARPEN = '''
    <rect x="0" y="0" width="1" height="1" fill="#646464"/>
    <rect x="1" y="0" width="1" height="1" fill="#646464"/>
    <rect x="2" y="0" width="1" height="1" fill="#646464"/>
    <rect x="0" y="1" width="1" height="1" fill="#646464"/>
    <rect x="1" y="1" width="1" height="1" fill="#787878"/>
    <rect x="2" y="1" width="1" height="1" fill="#646464"/>
    <rect x="0" y="2" width="1" height="1" fill="#646464"/>
    <rect x="1" y="2" width="1" height="1" fill="#646464"/>
    <rect x="2" y="2" width="1" height="1" fill="#646464"/>
'''


def svg_with_filter(rects, filter_attributes='', primitive_attributes=''):
    """Build an SVG string with a feConvolveMatrix filter on a 3x3 image."""
    if not filter_attributes:
        filter_attributes = 'x="0" y="0" width="3" height="3"'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="3" height="3">
  <defs>
    <filter id="convolve" filterUnits="userSpaceOnUse" {filter_attributes}>
      <feConvolveMatrix {primitive_attributes}/>
    </filter>
  </defs>
  <g filter="url(#convolve)">
    {rects}
  </g>
</svg>'''


def render(svg_string):
    """Render an SVG string and return its pixels as (r, g, b, a) tuples."""
    tree = parser.Tree(bytestring=svg_string.encode())
    png_surface = surface.PNGSurface(tree, None, 96)
    cairo_surface = png_surface.cairo
    data = bytes(cairo_surface.get_data())  # pixels read before finish()
    stride = cairo_surface.get_stride()
    width, height = cairo_surface.get_width(), cairo_surface.get_height()
    pixels = []
    for y in range(height):
        for x in range(width):
            blue, green, red, alpha = data[y * stride + x * 4:
                                           y * stride + x * 4 + 4]
            pixels.append((red, green, blue, alpha))
    png_surface.finish()
    return pixels


def test_identity():
    """A 3x3 identity kernel leaves the image unchanged."""
    svg_string = svg_with_filter(
        RECTS, primitive_attributes=(
            'order="3" kernelMatrix="0 0 0 0 1 0 0 0 0"'))
    expected = [
        (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255),
        (255, 255, 0, 255), (0, 255, 255, 255), (255, 0, 255, 255),
        (255, 255, 255, 255), (0, 0, 0, 255), (128, 128, 128, 255)]
    assert render(svg_string) == expected
    # The identity filter is a no-op: same result as without any filter
    assert render(svg_string) == render(
        svg_string.replace('filter="url(#convolve)"', ''))


def test_blur_default_divisor():
    """A 3x3 box blur with the default divisor (sum of the kernel values)."""
    svg_string = svg_with_filter(
        RECTS, primitive_attributes=(
            'order="3" kernelMatrix="1 1 1 1 1 1 1 1 1"'))
    # Center pixel: average of the 3x3 neighborhood
    # (1148/9 = 127.56 -> 128 for each channel)
    assert render(svg_string)[4] == (128, 128, 128, 255)
    # Corner pixel with edgeMode=duplicate (default):
    # R: 255*4 + 0*2 + 255*2 + 0 = 1530 -> 170
    # G: 0*4 + 255*2 + 255*2 + 255 = 1275 -> 142
    # B: 0*4 + 0*2 + 0*2 + 255 = 255 -> 28
    assert render(svg_string)[0] == (170, 142, 28, 255)


def test_edge_mode_none():
    """edgeMode=none extends the image with transparent black pixels."""
    svg_string = svg_with_filter(
        RECTS, primitive_attributes=(
            'order="3" kernelMatrix="1 1 1 1 1 1 1 1 1" edgeMode="none"'))
    # Corner pixel: 5 of the 9 samples are transparent black
    # R: (255 + 0 + 255 + 0) / 9 = 56.67 -> 57
    # G: (0 + 255 + 255 + 255) / 9 = 85
    # B: (0 + 0 + 0 + 255) / 9 = 28.33 -> 28
    # A: (255 * 4) / 9 = 113.33 -> 113
    assert render(svg_string)[0] == (57, 85, 28, 113)
    # Center pixel is not affected by the edges
    assert render(svg_string)[4] == (128, 128, 128, 255)


def test_sharpen():
    """A 3x3 sharpening kernel enhances the center pixel."""
    svg_string = svg_with_filter(
        RECTS_SHARPEN,
        primitive_attributes=(
            'order="3" kernelMatrix="0 -1 0 -1 5 -1 0 -1 0"'))
    # Center: 5*120 - 4*100 = 200
    assert render(svg_string)[4] == (200, 200, 200, 255)
    # Sides: 5*100 - (100 + 100 + 100 + 120) = 80
    for index in (1, 3, 5, 7):
        assert render(svg_string)[index] == (80, 80, 80, 255)
    # Corners are unchanged (their neighbors are all 100)
    for index in (0, 2, 6, 8):
        assert render(svg_string)[index] == (100, 100, 100, 255)


def test_divisor_and_bias():
    """divisor scales the result, bias is added after the division."""
    svg_string = svg_with_filter(
        RECTS_SHARPEN,
        primitive_attributes=(
            'order="3" kernelMatrix="0 0 0 0 1 0 0 0 0" '
            'divisor="2" bias="0.2"'))
    # Alpha: 255 / 2 + 0.2 * 255 = 178.5 -> 179
    # Color: 100 / 2 + 0.2 * 178.5 = 85.7 -> 86 (bias applies to the
    # premultiplied color channels, scaled by the resulting alpha)
    assert render(svg_string)[0] == (86, 86, 86, 179)


def test_target():
    """targetX and targetY define the anchor of the kernel."""
    # Anchor at (0, 0): each pixel takes the value 2 pixels down-right,
    # clamped to the bottom-right pixel (128, 128, 128)
    svg_string = svg_with_filter(
        RECTS, primitive_attributes=(
            'order="3" kernelMatrix="1 0 0 0 0 0 0 0 0" '
            'targetX="0" targetY="0"'))
    assert render(svg_string) == [(128, 128, 128, 255)] * 9
    # Anchor at (2, 2): the kernel picks the pixel itself
    svg_string = svg_with_filter(
        RECTS, primitive_attributes=(
            'order="3" kernelMatrix="1 0 0 0 0 0 0 0 0" '
            'targetX="2" targetY="2"'))
    assert render(svg_string) == [
        (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255),
        (255, 255, 0, 255), (0, 255, 255, 255), (255, 0, 255, 255),
        (255, 255, 255, 255), (0, 0, 0, 255), (128, 128, 128, 255)]


def test_preserve_alpha():
    """preserveAlpha keeps the alpha channel and convolves colors."""
    attributes = 'order="3" kernelMatrix="1 1 1 1 1 1 1 1 1"'
    # With preserveAlpha=false (default), the alpha channel is convolved
    # too. Center pixel, premultiplied values:
    # R: (255+0+0+0+128+255+0+0+255)/9 = 99.2 -> 99
    # G: (0+255+0+0+0+255+0+255+0)/9 = 85
    # B: (0+0+255+0+0+255+0+255+255)/9 = 113.3 -> 113
    # A: (255*7 + 128 + 0)/9 = 212.6 -> 213
    assert render(svg_with_filter(RECTS_ALPHA, primitive_attributes=(
        attributes)))[4] == (99, 85, 113, 213)
    # With preserveAlpha=true, the alpha of the source pixel is preserved
    # and the colors are convolved as unpremultiplied values, then
    # re-premultiplied by the preserved alpha (128):
    # R: (255+0+0+0+255+255+0+0+255)/9 * 128/255 = 56.9 -> 57
    # G: (0+255+0+0+0+255+0+255+0)/9 * 128/255 = 42.7 -> 43
    # B: (0+0+255+0+0+255+0+255+255)/9 * 128/255 = 56.9 -> 57
    assert render(svg_with_filter(RECTS_ALPHA, primitive_attributes=(
        f'{attributes} preserveAlpha="true"')))[4] == (57, 43, 57, 128)


def test_filter_region():
    """The convolution only applies inside the SVG filter region."""
    # Region limited to the center pixel
    svg_string = svg_with_filter(
        RECTS, filter_attributes='x="1" y="1" width="1" height="1"',
        primitive_attributes='order="3" kernelMatrix="1 1 1 1 1 1 1 1 1"')
    expected = [
        (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255),
        (255, 255, 0, 255), (128, 128, 128, 255), (255, 0, 255, 255),
        (255, 255, 255, 255), (0, 0, 0, 255), (128, 128, 128, 255)]
    assert render(svg_string) == expected


def test_invalid_parameters():
    """Invalid parameters make the primitive inoperable (render unchanged)."""
    svg_string = svg_with_filter(
        RECTS, primitive_attributes=(
            'order="3" kernelMatrix="0 0 0 0 1 0 0 0 0"'))
    expected = render(svg_string.replace('filter="url(#convolve)"', ''))
    for attributes in (
            # Matrix length does not match order
            'order="3" kernelMatrix="0 0 0 0 1 0"',
            # Zero divisor
            'order="3" kernelMatrix="0 0 0 0 1 0 0 0 0" divisor="0"',
            # Non-finite coefficient
            'order="3" kernelMatrix="0 0 0 0 NaN 0 0 0 0"',
            'order="3" kernelMatrix="0 0 0 0 inf 0 0 0 0"',
            # Kernel too large
            f'order="100" kernelMatrix="{" ".join(["1"] * 10000)}"',
            # Invalid order
            'order="1.5" kernelMatrix="1"',
            # Invalid target
            'order="3" kernelMatrix="0 0 0 0 1 0 0 0 0" targetX="3"',
            # Unknown edge mode
            'order="3" kernelMatrix="0 0 0 0 1 0 0 0 0" edgeMode="wrap"',
    ):
        assert render(svg_with_filter(
            RECTS, primitive_attributes=attributes)) == expected
