"""
CairoSVG API test suite.

These tests can be used as deployment tests.

"""

import io
import sys

import cairocffi as cairo
import pytest

from . import SURFACES, VERSION, parser, surface, svg2pdf, svg2png
from .__main__ import main

MAGIC_NUMBERS = {
    'SVG': b'<?xml',
    'PNG': b'\211PNG\r\n\032\n',
    'PDF': b'%PDF',
    'PS': b'%!',
}

SVG_SAMPLE = b'''\
<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="4in" height="5in">
  <rect x="5" y="10" width="13" height="15"
        fill="lime" stroke="black" stroke-width="1" />
</svg>
'''


@pytest.mark.parametrize('format_name', MAGIC_NUMBERS)
def test_formats(format_name):
    """Convert to a given format and test that output looks right."""
    content = SURFACES[format_name].convert(SVG_SAMPLE)
    assert content.startswith(MAGIC_NUMBERS[format_name])


def read_file(filename):
    """Shortcut to return the whole content of a file as a byte string."""
    with open(filename, 'rb') as file_object:
        return file_object.read()


def test_api(tmp_path):
    """Test the Python API with various parameters."""
    expected_content = svg2png(SVG_SAMPLE)
    # Already tested above: just a sanity check:
    assert expected_content.startswith(MAGIC_NUMBERS['PNG'])

    # Read from a byte string
    assert svg2png(SVG_SAMPLE) == expected_content
    assert svg2png(bytestring=SVG_SAMPLE) == expected_content

    file_like = io.BytesIO(SVG_SAMPLE)
    # Read from a file-like object
    assert svg2png(file_obj=file_like) == expected_content

    file_like = io.BytesIO()
    # Write to a file-like object
    svg2png(SVG_SAMPLE, write_to=file_like)
    assert file_like.getvalue() == expected_content

    temp_0 = tmp_path / 'sample_0.svg'
    temp_0.write_bytes(SVG_SAMPLE)

    # Read from a filename
    assert svg2png(url=str(temp_0)) == expected_content
    assert svg2png(url=f'file://{temp_0}') == expected_content

    # Read from a pathlib.Path
    assert svg2png(url=temp_0) == expected_content

    with temp_0.open('rb') as file_object:
        # Read from a real file object
        assert svg2png(file_obj=file_object) == expected_content

    temp_1 = tmp_path / 'result_1.png'
    with temp_1.open('wb') as file_object:
        # Write to a real file object
        svg2png(SVG_SAMPLE, write_to=file_object)
    assert read_file(str(temp_1)) == expected_content

    temp_2 = tmp_path / 'result_2.png'
    # Write to a filename
    svg2png(SVG_SAMPLE, write_to=str(temp_2))
    assert read_file(str(temp_2)) == expected_content

    file_like = io.BytesIO()
    try:
        svg2png(write_to=file_like)
    except TypeError:
        pass
    else:  # pragma: no cover
        raise Exception('TypeError not raised')


def test_low_level_api():
    """Test the low-level Python API with various parameters."""
    expected_content = svg2png(SVG_SAMPLE)

    # Same as above, longer version
    tree = parser.Tree(bytestring=SVG_SAMPLE)
    file_like = io.BytesIO()
    png_surface = surface.PNGSurface(tree, file_like, 96)
    png_surface.finish()
    assert file_like.getvalue() == expected_content

    png_result = cairo.ImageSurface.create_from_png(
        io.BytesIO(expected_content))
    expected_width = png_result.get_width()
    expected_height = png_result.get_height()

    # Abstract surface
    png_surface = surface.PNGSurface(tree, None, 96)
    assert png_surface.width == expected_width
    assert png_surface.height == expected_height
    assert cairo.SurfacePattern(png_surface.cairo)


def test_script(tmp_path):
    """Test the ``cairosvg`` script and the ``main`` function."""
    expected_png = svg2png(SVG_SAMPLE)[:100]
    expected_pdf = svg2pdf(SVG_SAMPLE)[:100]

    def test_main(args, exit_=False, input_=None, full=False):
        """Test main called with given ``args``.

        If ``exit_`` is ``True``, check that ``SystemExit`` is raised. We then
        assume that the program output is an unicode string.

        If ``input_`` is given, use this stream as input stream.

        """
        sys.argv = ['cairosvg'] + args
        old_stdin, old_stdout = sys.stdin, sys.stdout

        output_buffer = io.BytesIO()
        sys.stdout = io.TextIOWrapper(output_buffer)

        if input_:
            sys.stdin = open(input_, 'rb')
            sys.stdin.buffer = sys.stdin

        if exit_:
            try:
                main()
            except SystemExit:
                pass
            else:  # pragma: no cover
                raise Exception('CairoSVG did not exit')
        else:
            main()

        sys.stdout.flush()
        output = output_buffer.getvalue()
        sys.stdin, sys.stdout = old_stdin, old_stdout
        return output if full else output[:100]

    svg_path = tmp_path / "test.svg"
    svg_path.write_bytes(SVG_SAMPLE)
    svg_filename = str(svg_path)

    assert test_main(['--help'], exit_=True).startswith(b'usage: ')
    assert test_main(['--version'], exit_=True).strip() == (
        VERSION.encode('ascii'))

    assert test_main([svg_filename]) == expected_pdf
    assert test_main([svg_filename, '-d', '96', '-f', 'pdf']) == expected_pdf
    assert test_main([svg_filename, '-f', 'png']) == expected_png
    assert test_main(['-'], input_=svg_filename) == expected_pdf

    # Test DPI
    output = test_main([svg_filename, '-d', '10', '-f', 'png'], full=True)
    image = cairo.ImageSurface.create_from_png(io.BytesIO(output))
    assert image.get_width() == 40
    assert image.get_height() == 50

    temp_1 = tmp_path / 'result_1'
    # Default to PDF
    assert not test_main([svg_filename, '-o', str(temp_1)])
    assert read_file(temp_1)[:100] == expected_pdf

    temp_2 = tmp_path / 'result_2.png'
    # Guess from the file extension
    assert not test_main([svg_filename, '-o', str(temp_2)])
    assert read_file(temp_2)[:100] == expected_png

    temp_3 = tmp_path / 'result_3.png'
    # Explicit -f wins
    assert not test_main([svg_filename, '-o', str(temp_3), '-f', 'pdf'])
    assert read_file(str(temp_3))[:100] == expected_pdf


def png_pixels(content):
    """Read a PNG document as a dict of ``(x, y): (b, g, r, a)`` pixels."""
    image = cairo.ImageSurface.create_from_png(io.BytesIO(content))
    width, height = image.get_width(), image.get_height()
    data = image.get_data()
    return {
        (x, y): tuple(data[(y * width + x) * 4:(y * width + x) * 4 + 4])
        for y in range(height) for x in range(width)}


def test_filter_merge_order():
    """feMerge composites its nodes in order, with alpha-over."""
    template = b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="2" height="1">
  <defs>
    <filter id="merge" x="0" y="0" width="2" height="1"
            filterUnits="userSpaceOnUse">
      <feFlood flood-color="red" flood-opacity="0.5" result="a"/>
      <feFlood flood-color="blue" flood-opacity="0.5" result="b"/>
      <feMerge>%s</feMerge>
    </filter>
  </defs>
  <rect width="2" height="1" filter="url(#merge)"/>
</svg>'''
    # Blue 0.5 over red 0.5: premultiplied (b, g, r, a) = (.5, 0, .25, .75)
    pixels = png_pixels(svg2png(
        template % b'<feMergeNode in="a"/><feMergeNode in="b"/>'))
    blue, _, red, alpha = pixels[0, 0]
    assert pixels[0, 0] == pixels[1, 0]
    assert abs(blue - 128) <= 1
    assert abs(red - 64) <= 1
    assert abs(alpha - 191) <= 1
    # Reversed order swaps red and blue
    pixels = png_pixels(svg2png(
        template % b'<feMergeNode in="b"/><feMergeNode in="a"/>'))
    blue, _, red, alpha = pixels[0, 0]
    assert abs(blue - 64) <= 1
    assert abs(red - 128) <= 1
    assert abs(alpha - 191) <= 1


def test_filter_merge_shadow():
    """feMerge composites a shadow layer and the source graphic."""
    pixels = png_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12">
  <defs>
    <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">
      <feOffset in="SourceAlpha" dx="2" dy="2" result="offset"/>
      <feMerge>
        <feMergeNode in="offset"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
  </defs>
  <rect x="4" y="4" width="4" height="4" fill="red" filter="url(#shadow)"/>
</svg>'''))
    red = (0, 0, 255, 255)
    black = (0, 0, 0, 255)
    transparent = (0, 0, 0, 0)
    # The source graphic is painted over the shadow
    assert pixels[4, 4] == pixels[7, 7] == red
    # The shadow is the offset alpha of the source graphic
    assert pixels[8, 8] == pixels[9, 9] == black
    # The source graphic covers the shadow where they overlap
    assert pixels[6, 6] == red
    # Nothing is painted outside the filter region
    assert pixels[1, 1] == pixels[10, 10] == transparent


def test_filter_merge_transform():
    """The filter region and offsets follow the element's transform."""
    pixels = png_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">
  <defs>
    <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">
      <feOffset in="SourceAlpha" dx="1" dy="1" result="offset"/>
      <feMerge>
        <feMergeNode in="offset"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
  </defs>
  <g transform="translate(10 10) scale(2)">
    <rect x="1" y="1" width="2" height="2" fill="red" filter="url(#shadow)"/>
  </g>
</svg>'''))
    red = (0, 0, 255, 255)
    black = (0, 0, 0, 255)
    transparent = (0, 0, 0, 0)
    # Rect at user (1, 1) lands at device (12, 12), 4x4 pixels
    assert pixels[12, 12] == pixels[15, 15] == red
    # Offset of 1 user unit is 2 device pixels
    assert pixels[16, 16] == pixels[17, 17] == black
    assert pixels[11, 11] == pixels[18, 18] == transparent


def test_filter_merge_references():
    """Unknown, forward and circular references are transparent black."""
    template = b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2">
  <defs>
    <filter id="filter" x="0" y="0" width="2" height="2"
            filterUnits="userSpaceOnUse">%s</filter>
  </defs>
  <rect width="2" height="2" fill="lime" filter="url(#filter)"/>
</svg>'''
    lime = (0, 255, 0, 255)
    transparent = (0, 0, 0, 0)

    # Unknown references are skipped, other nodes are still merged
    pixels = png_pixels(svg2png(template % (
        b'<feMerge><feMergeNode in="unknown"/>'
        b'<feMergeNode in="SourceGraphic"/></feMerge>')))
    assert pixels[0, 0] == pixels[1, 1] == lime

    # Forward references are transparent black at merge time: the first
    # merge is transparent because "later" is not defined yet
    pixels = png_pixels(svg2png(template % (
        b'<feMerge result="early"><feMergeNode in="later"/></feMerge>'
        b'<feFlood result="later" flood-color="lime"/>'
        b'<feMerge><feMergeNode in="early"/></feMerge>')))
    assert pixels[0, 0] == pixels[1, 1] == transparent

    # Circular references resolve to transparent black
    pixels = png_pixels(svg2png(template % (
        b'<feOffset in="cycle" result="cycle" dx="1"/>'
        b'<feMerge><feMergeNode in="cycle"/></feMerge>')))
    assert pixels[0, 0] == pixels[1, 1] == transparent

    # An empty feMerge paints nothing
    pixels = png_pixels(svg2png(template % b'<feMerge></feMerge>'))
    assert pixels[0, 0] == pixels[1, 1] == transparent


def test_filter_merge_region():
    """Non-positive filter regions disable rendering of the element."""
    template = b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2">
  <defs>
    <filter id="filter" x="0" y="0" width="%s" height="%s"
            filterUnits="userSpaceOnUse">
      <feMerge><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <rect width="2" height="2" fill="lime" filter="url(#filter)"/>
</svg>'''
    for width, height in ((b'0', b'2'), (b'2', b'0'), (b'-1', b'2')):
        pixels = png_pixels(svg2png(template % (width, height)))
        assert pixels[0, 0] == pixels[1, 1] == (0, 0, 0, 0)
    # A positive region keeps the source graphic
    pixels = png_pixels(svg2png(template % (b'2', b'2')))
    assert pixels[0, 0] == pixels[1, 1] == (0, 255, 0, 255)


def test_filter_merge_huge_region(monkeypatch):
    """Huge offscreen surfaces fall back to unfiltered rendering."""
    monkeypatch.setattr('cairosvg.defs.MAX_FILTER_SURFACE_PIXELS', 4)
    pixels = png_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">
  <defs>
    <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">
      <feOffset in="SourceAlpha" dx="2" dy="2" result="offset"/>
      <feMerge>
        <feMergeNode in="offset"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
  </defs>
  <rect x="2" y="2" width="4" height="4" fill="red" filter="url(#shadow)"/>
</svg>'''))
    # The source graphic is painted without the filter
    assert pixels[3, 3] == (0, 0, 255, 255)
    assert pixels[8, 8] == (0, 0, 0, 0)


def test_filter_without_merge_unchanged():
    """Filters without feMerge keep their historical rendering."""
    # feOffset still translates the context
    pixels = png_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">
  <defs>
    <filter id="offset">
      <feOffset dx="1" dy="2"/>
    </filter>
  </defs>
  <rect x="0" y="0" width="2" height="2" fill="lime" filter="url(#offset)"/>
</svg>'''))
    assert pixels[1, 2] == (0, 255, 0, 255)
    assert pixels[0, 0] == (0, 0, 0, 0)
    # feFlood still fills after painting
    pixels = png_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2">
  <defs>
    <filter id="flood" x="0" y="0" width="2" height="2"
            filterUnits="userSpaceOnUse">
      <feFlood width="2" height="2" flood-color="red"/>
    </filter>
  </defs>
  <rect width="2" height="2" fill="lime" filter="url(#flood)"/>
</svg>'''))
    assert pixels[0, 0] == pixels[1, 1] == (0, 0, 255, 255)
