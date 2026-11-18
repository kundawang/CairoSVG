"""
Filter primitives.

This module implements the raster part of the SVG filter pipeline, mainly
``feImage`` and the primitives commonly used to combine its result with the
source graphic (``feComposite``, ``feBlend`` and ``feMerge``).

Every intermediate result is stored as a full-viewport, transparent RGBA
:class:`cairocffi.ImageSurface` (in device pixels).  Layers are painted
through a pattern whose matrix maps user space onto device pixels, so the
same code works whatever the surface resolution (PNG, PDF, …).

The legacy implementations of ``feOffset``, ``feBlend`` and ``feFlood`` live
in :mod:`cairosvg.defs` and are kept untouched: primitives they fully handle
are skipped here to preserve the historical rendering pixel for pixel.

"""

import base64
import binascii
from io import BytesIO
from urllib.parse import unquote_to_bytes

from PIL import Image, ImageChops, UnidentifiedImageError

from .helpers import size
from .parser import Tree
from .surface import cairo
from .url import parse_url

# Maximum number of pixels allowed for a raster image referenced by feImage.
# Images larger than this are considered a DoS attempt and not rendered.
MAX_IMAGE_PIXELS = 32_000_000

XLINK_HREF = '{http://www.w3.org/1999/xlink}href'

# feComposite operators that map directly onto a Cairo operator.  Per the SVG
# specification, the layer referenced by ``in`` is the destination and the one
# referenced by ``in2`` the source.
COMPOSITE_OPERATORS = {
    'over': cairo.OPERATOR_OVER,
    'in': cairo.OPERATOR_IN,
    'out': cairo.OPERATOR_OUT,
    'atop': cairo.OPERATOR_ATOP,
    'xor': cairo.OPERATOR_XOR,
}

BLEND_OPERATORS = {
    'normal': cairo.OPERATOR_OVER,
    'multiply': cairo.OPERATOR_MULTIPLY,
    'screen': cairo.OPERATOR_SCREEN,
    'darken': cairo.OPERATOR_DARKEN,
    'lighten': cairo.OPERATOR_LIGHTEN,
}


def node_href(node):
    """Return the ``href`` (or legacy ``xlink:href``) of a filter node."""
    return node.get(XLINK_HREF, node.get('href'))


def filter_region(surface, filter_node, node):
    """Return the filter region ``(x, y, width, height)`` in user units."""
    if filter_node.get('filterUnits') == 'objectBoundingBox':
        # Defaults are -10% / 120% of the bounding box
        bbox_x = size(surface, node.get('x'), 'x')
        bbox_y = size(surface, node.get('y'), 'y')
        bbox_width = size(surface, node.get('width'), 'x') or 1
        bbox_height = size(surface, node.get('height'), 'y') or 1
        x = bbox_x + size(
            surface, filter_node.get('x', '-0.1'), 1) * bbox_width
        y = bbox_y + size(
            surface, filter_node.get('y', '-0.1'), 1) * bbox_height
        width = size(
            surface, filter_node.get('width', '1.2'), 1) * bbox_width
        height = size(
            surface, filter_node.get('height', '1.2'), 1) * bbox_height
    else:
        x = size(surface, filter_node.get('x', '-10%'), 'x')
        y = size(surface, filter_node.get('y', '-10%'), 'y')
        width = size(surface, filter_node.get('width', '120%'), 'x')
        height = size(surface, filter_node.get('height', '120%'), 'y')
    return x, y, width, height


def primitive_region(surface, filter_node, node, child):
    """Return the subregion ``(x, y, width, height)`` of a primitive."""
    default_x, default_y, default_width, default_height = filter_region(
        surface, filter_node, node)
    if filter_node.get('primitiveUnits') == 'objectBoundingBox':
        bbox_x = size(surface, node.get('x'), 'x')
        bbox_y = size(surface, node.get('y'), 'y')
        bbox_width = size(surface, node.get('width'), 'x') or 1
        bbox_height = size(surface, node.get('height'), 'y') or 1
        x = bbox_x + size(surface, child.get('x', 0), 1) * bbox_width
        y = bbox_y + size(surface, child.get('y', 0), 1) * bbox_height
        width = size(surface, child.get('width', 1), 1) * bbox_width
        height = size(surface, child.get('height', 1), 1) * bbox_height
    else:
        x = size(surface, child.get('x'), 'x') or default_x
        y = size(surface, child.get('y'), 'y') or default_y
        width = size(surface, child.get('width'), 'x') or default_width
        height = size(surface, child.get('height'), 'y') or default_height
    return x, y, width, height


def new_layer(surface):
    """Return a transparent RGBA layer covering the whole viewport."""
    return cairo.ImageSurface(
        cairo.FORMAT_ARGB32, max(round(surface.width), 1),
        max(round(surface.height), 1))


def paint_layer(surface, layer):
    """Paint a full-viewport raster ``layer`` on the main context."""
    if layer is None:
        return
    pattern = cairo.SurfacePattern(layer)
    # The layer is stored in device pixels: map one user unit onto
    # ``device_units_per_user_units`` pattern pixels.
    dpu = surface.device_units_per_user_units
    pattern.set_matrix(cairo.Matrix(xx=dpu, yy=dpu))
    surface.context.save()
    surface.context.set_source(pattern)
    surface.context.paint()
    surface.context.restore()


def snapshot(surface):
    """Rasterize the painted source graphic into a full-viewport layer.

    The source graphic is available as the group pattern stashed by
    :meth:`Surface.draw`, which works for both raster and vector (PDF, PS)
    output surfaces.

    """
    layer = new_layer(surface)
    context = cairo.Context(layer)
    # The popped group pattern uses an identity matrix, i.e. its coordinates
    # are already device pixels, matching the layer's coordinate system.
    context.set_source(surface.source_pattern)
    context.paint()
    return layer


def decode_data_uri(href):
    """Decode the payload of a ``data:`` URI, or return ``None`` on error."""
    if not href or not href.startswith('data:'):
        return None
    comma = href.find(',')
    if comma < 0:
        return None
    header = href[5:comma]
    payload = href[comma + 1:]
    try:
        if ';base64' in header.lower():
            return base64.b64decode(payload, validate=True)
        return unquote_to_bytes(payload)
    except (binascii.Error, ValueError):
        return None


def raster_layer(surface, image_bytes, x, y, width, height,
                 preserve_aspect_ratio):
    """Build a full-viewport layer containing a raster image.

    The image is fitted into the ``(x, y, width, height)`` rectangle, honoring
    ``preserveAspectRatio``.  ``None`` is returned for empty or invalid data,
    and for images exceeding :data:`MAX_IMAGE_PIXELS`.

    """
    if not image_bytes:
        return None
    try:
        image = Image.open(BytesIO(image_bytes))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    if image.width <= 0 or image.height <= 0:
        return None
    if image.width * image.height > MAX_IMAGE_PIXELS:
        return None
    if image.mode != 'RGBA':
        image = image.convert('RGBA')

    dpu = surface.device_units_per_user_units
    rect_x, rect_y = round(x * dpu), round(y * dpu)
    rect_width = round(width * dpu)
    rect_height = round(height * dpu)
    if rect_width <= 0 or rect_height <= 0:
        return None
    if rect_width * rect_height > MAX_IMAGE_PIXELS:
        return None

    align, *meet_or_slice = preserve_aspect_ratio.split()
    if align == 'none':
        scaled = image.resize((rect_width, rect_height))
        offset_x = offset_y = 0
    else:
        scale = (max if meet_or_slice == ['slice'] else min)(
            rect_width / image.width, rect_height / image.height)
        scaled_width = max(round(image.width * scale), 1)
        scaled_height = max(round(image.height * scale), 1)
        scaled = image.resize((scaled_width, scaled_height))
        # "xMidYMid" -> ("mid", "mid"), "xMaxYMax" -> ("max", "max")
        x_position = align[1:4].lower()
        y_position = align[5:8].lower()
        if x_position == 'mid':
            offset_x = (rect_width - scaled_width) // 2
        elif x_position == 'max':
            offset_x = rect_width - scaled_width
        else:
            offset_x = 0
        if y_position == 'mid':
            offset_y = (rect_height - scaled_height) // 2
        elif y_position == 'max':
            offset_y = rect_height - scaled_height
        else:
            offset_y = 0

    png_file = BytesIO()
    scaled.save(png_file, 'PNG')
    png_file.seek(0)
    image_surface = cairo.ImageSurface.create_from_png(png_file)
    layer = new_layer(surface)
    context = cairo.Context(layer)
    if align != 'none' and meet_or_slice == ['slice']:
        context.rectangle(rect_x, rect_y, rect_width, rect_height)
        context.clip()
    # Under an identity CTM, set_source_surface places the image origin at
    # the given device-pixel position (the context current point is (0, 0)).
    context.set_source_surface(
        image_surface, rect_x + offset_x, rect_y + offset_y)
    context.paint()
    return layer


def element_layer(surface, filter_node, fragment):
    """Build a full-viewport layer rendering the referenced element.

    ``fragment`` is the element identifier found after the ``#``.  Returns
    ``None`` when the reference is missing or circular.

    """
    if not fragment:
        return None
    try:
        tree = Tree(
            url=f'#{fragment}', url_fetcher=filter_node.url_fetcher,
            parent=filter_node, tree_cache=surface.tree_cache,
            unsafe=filter_node.unsafe)
    except (TypeError, RecursionError):
        # Missing element or circular reference
        return None
    from .features import match_features
    if not match_features(tree.xml_tree):
        return None

    # Render the referenced element in an offscreen surface using the same
    # viewport, so that user coordinates are preserved.
    from .surface import SVGSurface  # circular import
    wrapper = Tree(
        bytestring=(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{surface.context_width}" '
            f'height="{surface.context_height}"/>').encode(),
        url_fetcher=filter_node.url_fetcher,
        tree_cache=surface.tree_cache, unsafe=filter_node.unsafe)
    wrapper.children = [tree]
    try:
        sub_surface = SVGSurface(wrapper, None, surface.dpi, surface)
    except (RecursionError, ValueError):
        return None
    sub_surface.cairo.flush()
    layer = new_layer(surface)
    context = cairo.Context(layer)
    dpu = surface.device_units_per_user_units
    context.scale(dpu, dpu)
    context.set_source_surface(sub_surface.cairo, 0, 0)
    context.paint()
    return layer


def fe_image(surface, filter_node, node, child, results):
    """Implement ``feImage``: store or paint a transparent image layer."""
    x, y, width, height = primitive_region(
        surface, filter_node, node, child)
    layer = None
    if width > 0 and height > 0:
        href = node_href(child)
        if href:
            parsed = parse_url(href, filter_node.url)
            if href.startswith('#'):
                layer = element_layer(surface, filter_node, parsed.fragment)
            elif href.startswith('data:'):
                image_bytes = decode_data_uri(href)
                if image_bytes is not None:
                    layer = raster_layer(
                        surface, image_bytes, x, y, width, height,
                        child.get('preserveAspectRatio', 'xMidYMid'))
            elif parsed.fragment and not any(parsed[:-1]):
                # Bare fragment with a relative URL
                layer = element_layer(
                    surface, filter_node, parsed.fragment)
            else:
                # External references: the configured URL fetcher enforces
                # the safe/unsafe policy (safe mode returns an empty SVG).
                try:
                    image_bytes = filter_node.fetch_url(parsed, 'image/*')
                except (OSError, ValueError):
                    image_bytes = b''
                layer = raster_layer(
                    surface, image_bytes, x, y, width, height,
                    child.get('preserveAspectRatio', 'xMidYMid'))

    store_or_paint(surface, child, results, layer)


def resolve_input(surface, name, results, source_layer):
    """Resolve an ``in`` / ``in2`` attribute to a raster layer."""
    if name in (None, 'SourceGraphic'):
        return source_layer
    if name == 'SourceAlpha':
        # Black silhouette with the alpha of the source graphic.
        if source_layer is None:
            return new_layer(surface)
        alpha = cairo.ImageSurface(
            cairo.FORMAT_ARGB32, source_layer.get_width(),
            source_layer.get_height())
        context = cairo.Context(alpha)
        context.set_source_rgba(0, 0, 0, 1)
        context.mask_surface(source_layer, 0, 0)
        return alpha
    if name in ('BackgroundImage', 'BackgroundAlpha', 'FillPaint',
                'StrokePaint'):
        return None
    return results.get(name)


def store_or_paint(surface, child, results, layer):
    result = child.get('result')
    if result:
        results[result] = layer
    else:
        # A terminal primitive replaces the source graphic inside the
        # filter region: clear the painted source before compositing the
        # layer.  Layers are fully transparent outside their own content.
        surface.context.save()
        surface.context.set_operator(cairo.OPERATOR_CLEAR)
        surface.context.paint()
        surface.context.restore()
        paint_layer(surface, layer)


def fe_composite(surface, node, child, results, source_layer):
    """Implement the Cairo-supported operators of ``feComposite``."""
    operator = child.get('operator', 'over')
    in_layer = resolve_input(
        surface, child.get('in'), results, source_layer)
    in2_layer = resolve_input(
        surface, child.get('in2'), results, source_layer)
    if operator == 'arithmetic':
        layer = composite_arithmetic(
            surface, in_layer, in2_layer,
            size(surface, child.get('k1', 0), 1),
            size(surface, child.get('k2', 0), 1),
            size(surface, child.get('k3', 0), 1),
            size(surface, child.get('k4', 0), 1))
    else:
        cairo_operator = COMPOSITE_OPERATORS.get(operator)
        if cairo_operator is None:
            return
        layer = new_layer(surface)
        context = cairo.Context(layer)
        if in_layer is not None:
            context.set_source_surface(in_layer, 0, 0)
            context.paint()
        if in2_layer is not None:
            context.set_operator(cairo_operator)
            context.set_source_surface(in2_layer, 0, 0)
            context.paint()
    store_or_paint(surface, child, results, layer)


def composite_arithmetic(surface, in_layer, in2_layer, k1, k2, k3, k4):
    """Arithmetic feComposite: ``k1*i1*i2 + k2*i1 + k3*i2 + k4``.

    The formula is applied channel-wise (including alpha) with values in the
    0–255 range and the result clamped to 0–255.

    """
    width = max(round(surface.width), 1)
    height = max(round(surface.height), 1)
    fallback = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    image_in = layer_to_image(in_layer, width, height, fallback)
    image_in2 = layer_to_image(in2_layer, width, height, fallback)
    constant = max(round(255 * k4), 0)
    channels = []
    for channel in range(4):
        c1 = image_in.getchannel(channel)
        c2 = image_in2.getchannel(channel)
        # i1 * i2 / 255, then scaled by k1
        product = ImageChops.multiply(c1, c2).point(
            lambda value, k=k1: value * k / 255)
        term12 = c1.point(lambda value, k=k2: value * k)
        term3 = c2.point(lambda value, k=k3: value * k)
        result = ImageChops.add(product, term12)
        result = ImageChops.add(result, term3)
        if constant:
            result = ImageChops.add(
                result, Image.new('L', (width, height), constant))
        channels.append(result)
    return image_to_layer(Image.merge('RGBA', channels))


def layer_to_image(layer, width, height, fallback):
    """Convert a Cairo layer to a Pillow RGBA image."""
    if layer is None:
        return fallback
    layer.flush()
    return Image.frombuffer(
        'RGBA', (width, height), bytes(layer.get_data()), 'raw',
        'BGRA', 0, 1)


def image_to_layer(image):
    """Convert a Pillow RGBA image to a Cairo layer."""
    png_file = BytesIO()
    image.save(png_file, 'PNG')
    png_file.seek(0)
    return cairo.ImageSurface.create_from_png(png_file)


def fe_blend(surface, child, results, source_layer):
    """Implement ``feBlend`` against a named filter result."""
    operator = BLEND_OPERATORS.get(
        child.get('mode', 'normal'), cairo.OPERATOR_OVER)
    in_layer = resolve_input(
        surface, child.get('in'), results, source_layer)
    in2_layer = resolve_input(
        surface, child.get('in2'), results, source_layer)
    layer = new_layer(surface)
    context = cairo.Context(layer)
    if in_layer is not None:
        context.set_source_surface(in_layer, 0, 0)
        context.paint()
    if in2_layer is not None:
        context.set_operator(operator)
        context.set_source_surface(in2_layer, 0, 0)
        context.paint()
    store_or_paint(surface, child, results, layer)


def fe_merge(surface, child, results, source_layer):
    """Implement ``feMerge``: stack referenced layers in order."""
    layer = new_layer(surface)
    context = cairo.Context(layer)
    for merge_node in child.children:
        if merge_node.tag != 'feMergeNode':
            continue
        input_layer = resolve_input(
            surface, merge_node.get('in'), results, source_layer)
        if input_layer is not None:
            context.set_source_surface(input_layer, 0, 0)
            context.paint()
    store_or_paint(surface, child, results, layer)


def has_new_primitives(filter_node):
    """Check whether a filter uses primitives handled by this module."""
    for child in filter_node.children:
        if child.tag in ('feImage', 'feComposite', 'feMerge'):
            return True
        if child.tag == 'feBlend' and (
                child.get('in') not in (
                    None, 'SourceGraphic', 'BackgroundImage') or
                child.get('in2') not in (
                    None, 'SourceGraphic', 'BackgroundImage')):
            return True
    return False


def apply_filter_pipeline(surface, filter_node, node):
    """Run the filter primitives after the source graphic was painted."""
    results = {}
    # Snapshot the source graphic, which includes the legacy primitives
    # already applied by defs.apply_filter_after_painting.
    source_layer = snapshot(surface)

    for child in filter_node.children:
        tag = child.tag
        if tag == 'feImage':
            fe_image(surface, filter_node, node, child, results)
        elif tag == 'feComposite':
            fe_composite(surface, node, child, results, source_layer)
        elif tag == 'feMerge':
            fe_merge(surface, child, results, source_layer)
        elif tag == 'feBlend':
            # The legacy implementation in defs.py already handles feBlend
            # against SourceGraphic / BackgroundImage; only handle the cases
            # it cannot express.
            if (child.get('in') in results or
                    child.get('in2') in results):
                fe_blend(surface, child, results, source_layer)
        # feOffset, feFlood and unsupported primitives: keep the legacy
        # rendering untouched.
