"""
Externally defined elements managers.

This module handles clips, gradients, masks, patterns and external nodes.

"""

import math

from .bounding_box import calculate_bounding_box, is_non_empty_bounding_box
from .features import match_features
from .helpers import normalize, paint, size, transform
from .parser import Tree
from .shapes import rect
from .surface import cairo
from .url import parse_url

BLEND_OPERATORS = {
    'darken': cairo.OPERATOR_DARKEN,
    'lighten': cairo.OPERATOR_LIGHTEN,
    'multiply': cairo.OPERATOR_MULTIPLY,
    'normal': cairo.OPERATOR_OVER,
    'screen': cairo.OPERATOR_SCREEN,
}

EXTEND_OPERATORS = {
    'none': cairo.EXTEND_NONE,
    'pad': cairo.EXTEND_PAD,
    'reflect': cairo.EXTEND_REFLECT,
    'repeat': cairo.EXTEND_REPEAT,
}

# Maximum kernel order (width or height, in pixels) accepted for
# feConvolveMatrix. Larger kernels are ignored to bound the CPU and memory
# cost of the convolution, which is O(width * height * orderX * orderY).
MAX_CONVOLVE_ORDER = 15


def update_def_href(surface, def_name, def_dict, chain=None):
    """Update the attributes of the def according to its href attribute."""
    def_node = def_dict[def_name]
    href = parse_url(def_node.get_href()).fragment
    if chain is None:
        chain = set()
    if href in def_dict and href not in chain:
        chain.add(href)
        update_def_href(surface, href, def_dict, chain)
        href_node = def_dict[href]
        def_dict[def_name] = Tree(
            url=f'#{def_name}', url_fetcher=def_node.url_fetcher,
            parent=href_node, parent_children=(not def_node.children),
            tree_cache=surface.tree_cache, unsafe=def_node.unsafe)
        # Inherit attributes generally not inherited
        for key, value in href_node.items():
            if key not in def_dict[def_name]:
                def_dict[def_name][key] = value


def parse_all_defs(surface, node):
    """Recursively visit all child nodes and process definition elements."""

    # Handle node
    parse_def(surface, node)

    # Visit all children recursively
    if node.children:
        for child in node.children:
            parse_all_defs(surface, child)


def parse_def(surface, node):
    """Parse the SVG definitions."""
    for def_type in (
            'marker', 'gradient', 'pattern', 'path', 'mask', 'filter',
            'image'):
        if def_type in node.tag.lower() and 'id' in node:
            getattr(surface, f'{def_type}s')[node['id']] = node


def gradient_or_pattern(surface, node, name, opacity):
    """Gradient or pattern color."""
    if name in surface.gradients:
        update_def_href(surface, name, surface.gradients)
        return draw_gradient(surface, node, name, opacity)
    elif name in surface.patterns:
        update_def_href(surface, name, surface.patterns)
        return draw_pattern(surface, node, name, opacity)


def marker(surface, node):
    """Store a marker definition."""
    parse_def(surface, node)


def mask(surface, node):
    """Store a mask definition."""
    parse_def(surface, node)


def filter_(surface, node):
    """Store a filter definition."""
    parse_def(surface, node)


def linear_gradient(surface, node):
    """Store a linear gradient definition."""
    parse_def(surface, node)


def radial_gradient(surface, node):
    """Store a radial gradient definition."""
    parse_def(surface, node)


def pattern(surface, node):
    """Store a pattern definition."""
    parse_def(surface, node)


def clip_path(surface, node):
    """Store a clip path definition."""
    if 'id' in node:
        surface.paths[node['id']] = node


def paint_mask(surface, node, name, opacity):
    """Paint the mask of the current surface."""
    mask_node = surface.masks[name]
    mask_node.tag = 'g'
    mask_node['opacity'] = opacity

    if mask_node.get('maskUnits') == 'userSpaceOnUse':
        width_ref, height_ref = 'x', 'y'
    else:
        x = size(surface, node.get('x'), 'x')
        y = size(surface, node.get('y'), 'y')
        width = size(surface, node.get('width'), 'x')
        height = size(surface, node.get('height'), 'y')
        width_ref = width or surface.width
        height_ref = height or surface.height

    mask_node['x'] = size(surface, mask_node.get('x', '-10%'), width_ref)
    mask_node['y'] = size(surface, mask_node.get('y', '-10%'), height_ref)
    mask_node['height'] = size(
        surface, mask_node.get('height', '120%'), height_ref)
    mask_node['width'] = size(
        surface, mask_node.get('width', '120%'), width_ref)

    if mask_node.get('maskUnits') == 'userSpaceOnUse':
        x = mask_node['x']
        y = mask_node['y']
        mask_node['viewBox'] = '{x} {y} {width} {height}'.format(**mask_node)

    from .surface import SVGSurface  # circular import
    mask_surface = SVGSurface(mask_node, None, surface.dpi, surface)
    surface.context.save()
    surface.context.translate(x, y)
    surface.context.scale(
        mask_node['width'] / mask_surface.width,
        mask_node['height'] / mask_surface.height)
    surface.context.mask_surface(mask_surface.cairo)
    surface.context.restore()


def draw_gradient(surface, node, name, opacity):
    """Gradients colors."""
    gradient_node = surface.gradients[name]

    if gradient_node.get('gradientUnits') == 'userSpaceOnUse':
        width_ref, height_ref = 'x', 'y'
        diagonal_ref = 'xy'
    else:
        bounding_box = calculate_bounding_box(surface, node)
        if not is_non_empty_bounding_box(bounding_box):
            return False
        x = size(surface, bounding_box[0], 'x')
        y = size(surface, bounding_box[1], 'y')
        width = size(surface, bounding_box[2], 'x')
        height = size(surface, bounding_box[3], 'y')
        width_ref = height_ref = diagonal_ref = 1

    if gradient_node.tag == 'linearGradient':
        x1 = size(surface, gradient_node.get('x1', '0%'), width_ref)
        x2 = size(surface, gradient_node.get('x2', '100%'), width_ref)
        y1 = size(surface, gradient_node.get('y1', '0%'), height_ref)
        y2 = size(surface, gradient_node.get('y2', '0%'), height_ref)
        gradient_pattern = cairo.LinearGradient(x1, y1, x2, y2)

    elif gradient_node.tag == 'radialGradient':
        r = size(surface, gradient_node.get('r', '50%'), diagonal_ref)
        cx = size(surface, gradient_node.get('cx', '50%'), width_ref)
        cy = size(surface, gradient_node.get('cy', '50%'), height_ref)
        fx = size(surface, gradient_node.get('fx', str(cx)), width_ref)
        fy = size(surface, gradient_node.get('fy', str(cy)), height_ref)
        gradient_pattern = cairo.RadialGradient(fx, fy, 0, cx, cy, r)

    else:
        return False

    # Apply matrix to set coordinate system for gradient
    if gradient_node.get('gradientUnits') != 'userSpaceOnUse':
        gradient_pattern.set_matrix(cairo.Matrix(
            1 / width, 0, 0, 1 / height, - x / width, - y / height))

    # Apply transform of gradient
    transform(
        surface, gradient_node.get('gradientTransform'), gradient_pattern)

    # Apply gradient (<stop> by <stop>)
    offset = 0
    for child in gradient_node.children:
        offset = max(offset, size(surface, child.get('offset'), 1))
        stop_color = surface.map_color(
            child.get('stop-color', 'black'),
            float(child.get('stop-opacity', 1)) * opacity)
        gradient_pattern.add_color_stop_rgba(offset, *stop_color)

    # Set spread method for gradient outside target bounds
    gradient_pattern.set_extend(EXTEND_OPERATORS.get(
        gradient_node.get('spreadMethod', 'pad'), EXTEND_OPERATORS['pad']))

    surface.context.set_source(gradient_pattern)
    return True


def draw_pattern(surface, node, name, opacity):
    """Draw a pattern image."""
    pattern_node = surface.patterns[name]
    pattern_node['opacity'] = float(pattern_node.get('opacity', 1)) * opacity
    pattern_node.tag = 'g'
    transform(surface, pattern_node.get('patternTransform'))

    if pattern_node.get('viewBox'):
        if not (size(surface, pattern_node.get('width', 1), 1) and
                size(surface, pattern_node.get('height', 1), 1)):
            return False
    else:
        if not (size(surface, pattern_node.get('width', 0), 1) and
                size(surface, pattern_node.get('height', 0), 1)):
            return False

    if pattern_node.get('patternUnits') == 'userSpaceOnUse':
        x = size(surface, pattern_node.get('x'), 'x')
        y = size(surface, pattern_node.get('y'), 'y')
        pattern_width = size(surface, pattern_node.get('width', 0), 1)
        pattern_height = size(surface, pattern_node.get('height', 0), 1)
    else:
        _, _, width, height = calculate_bounding_box(surface, node)
        x = size(surface, pattern_node.get('x'), 1) * width
        y = size(surface, pattern_node.get('y'), 1) * height
        pattern_width = (
            size(surface, pattern_node.pop('width', '1'), 1) * width)
        pattern_height = (
            size(surface, pattern_node.pop('height', '1'), 1) * height)
        if 'viewBox' not in pattern_node:
            pattern_node['width'] = pattern_width
            pattern_node['height'] = pattern_height
            if pattern_node.get('patternContentUnits') == 'objectBoundingBox':
                pattern_node['transform'] = f'scale({width}, {height})'

    # Fail if pattern has an invalid size
    if pattern_width == 0.0 or pattern_height == 0.0:
        return False

    from .surface import SVGSurface  # circular import
    pattern_surface = SVGSurface(pattern_node, None, surface.dpi, surface)
    pattern_pattern = cairo.SurfacePattern(pattern_surface.cairo)
    pattern_pattern.set_extend(cairo.EXTEND_REPEAT)
    pattern_pattern.set_matrix(cairo.Matrix(
        pattern_surface.width / pattern_width, 0, 0,
        pattern_surface.height / pattern_height, -x, -y))
    surface.context.set_source(pattern_pattern)
    return True


def prepare_filter(surface, node, name):
    """Apply a filter transforming the context."""
    if 'id' in node and node['id'] in surface.masks:
        return

    if name in surface.filters:
        filter_node = surface.filters[name]
        for child in filter_node.children:
            # Offset
            if child.tag == 'feOffset':
                if filter_node.get('primitiveUnits') == 'objectBoundingBox':
                    width = size(surface, node.get('width'), 'x')
                    height = size(surface, node.get('height'), 'y')
                    dx = size(surface, child.get('dx', 0), 1) * width
                    dy = size(surface, child.get('dy', 0), 1) * height
                else:
                    dx = size(surface, child.get('dx', 0), 1)
                    dy = size(surface, child.get('dy', 0), 1)
                surface.context.translate(dx, dy)


def apply_filter_before_painting(surface, node, name):
    """Apply a filter transforming the painting operations."""
    if 'id' in node and node['id'] in surface.masks:
        return

    if name in surface.filters:
        filter_node = surface.filters[name]
        for child in filter_node.children:
            # Blend
            if child.tag == 'feBlend':
                surface.context.set_operator(BLEND_OPERATORS.get(
                    child.get('mode', 'normal'), BLEND_OPERATORS['normal']))
            # Convolve matrix
            elif child.tag == 'feConvolveMatrix':
                convolve_matrix(surface, node, filter_node, child)


def _filter_region(surface, node, filter_node):
    """Return the filter region as ``(x, y, width, height)`` in user units."""
    if filter_node.get('filterUnits') == 'userSpaceOnUse':
        return (
            size(surface, filter_node.get('x', '-10%'), 'x'),
            size(surface, filter_node.get('y', '-10%'), 'y'),
            size(surface, filter_node.get('width', '120%'), 'x'),
            size(surface, filter_node.get('height', '120%'), 'y'))
    bounding_box = calculate_bounding_box(surface, node)
    if not is_non_empty_bounding_box(bounding_box):
        # Region is undefined, use the whole viewport
        return (0, 0, surface.context_width, surface.context_height)
    x, y, width, height = (float(value) for value in bounding_box)
    return (
        x + size(surface, filter_node.get('x', '-10%'), 1) * width,
        y + size(surface, filter_node.get('y', '-10%'), 1) * height,
        size(surface, filter_node.get('width', '120%'), 1) * width,
        size(surface, filter_node.get('height', '120%'), 1) * height)


def _primitive_region(surface, node, filter_node, child, region):
    """Intersect ``region`` with the primitive's own subregion if given."""
    if not any(child.get(key) is not None
               for key in ('x', 'y', 'width', 'height')):
        return region
    if filter_node.get('primitiveUnits') == 'objectBoundingBox':
        bounding_box = calculate_bounding_box(surface, node)
        if not is_non_empty_bounding_box(bounding_box):
            return region
        x, y, width, height = (float(value) for value in bounding_box)
        subregion = (
            x + size(surface, child.get('x', 0), 1) * width,
            y + size(surface, child.get('y', 0), 1) * height,
            size(surface, child.get('width', '100%'), 1) * width,
            size(surface, child.get('height', '100%'), 1) * height)
    else:
        subregion = (
            size(surface, child.get('x', 0), 'x'),
            size(surface, child.get('y', 0), 'y'),
            size(surface, child.get('width', '100%'), 'x'),
            size(surface, child.get('height', '100%'), 'y'))
    x0 = max(region[0], subregion[0])
    y0 = max(region[1], subregion[1])
    x1 = min(region[0] + region[2], subregion[0] + subregion[2])
    y1 = min(region[1] + region[3], subregion[1] + subregion[3])
    return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))


def _convolve_region(group, kernel, order_x, order_y,
                     target_x, target_y, divisor, bias, edge_mode,
                     preserve_alpha, region):
    """Convolve ``region`` of the ARGB32 image surface ``group``.

    ``region`` is a ``(x0, y0, x1, y1)`` rectangle of integer pixel
    coordinates in the surface. The convolution samples the whole surface
    (the source graphic) and writes only inside the region.

    """
    width, height = group.get_width(), group.get_height()
    x0, y0, x1, y1 = region
    if x0 >= x1 or y0 >= y1:
        return
    stride = group.get_stride()
    group.flush()
    data = memoryview(group.get_data()).cast('B')
    # The convolution samples the original pixels: work on a copy
    source = bytes(data)
    # Kernel taps as (dx, dy, coefficient), skipping zero coefficients
    taps = [
        (order_x - 1 - x - target_x, order_y - 1 - y - target_y,
         kernel[y * order_x + x])
        for y in range(order_y) for x in range(order_x)
        if kernel[y * order_x + x]]
    if not taps:
        return
    bias_255 = bias * 255
    for y in range(y0, y1):
        for x in range(x0, x1):
            red = green = blue = alpha = 0.0
            if preserve_alpha:
                # Convolve the color channels with unpremultiplied values,
                # the alpha channel is copied from the source
                base = y * stride + x * 4
                alpha = source[base + 3]
                for dx, dy, coefficient in taps:
                    sx, sy = x + dx, y + dy
                    if 0 <= sx < width and 0 <= sy < height:
                        offset = sy * stride + sx * 4
                    elif edge_mode == 'duplicate':
                        sx = min(max(sx, 0), width - 1)
                        sy = min(max(sy, 0), height - 1)
                        offset = sy * stride + sx * 4
                    else:  # 'none': transparent black, no contribution
                        continue
                    alpha_source = source[offset + 3]
                    if alpha_source:
                        factor = 255 / alpha_source * coefficient
                        blue += source[offset] * factor
                        green += source[offset + 1] * factor
                        red += source[offset + 2] * factor
                # Re-premultiply by the preserved alpha
                scale = alpha / 255
                blue = blue / divisor * scale + bias_255 * scale
                green = green / divisor * scale + bias_255 * scale
                red = red / divisor * scale + bias_255 * scale
            else:
                # Convolve the premultiplied color and alpha channels
                for dx, dy, coefficient in taps:
                    sx, sy = x + dx, y + dy
                    if 0 <= sx < width and 0 <= sy < height:
                        offset = sy * stride + sx * 4
                    elif edge_mode == 'duplicate':
                        sx = min(max(sx, 0), width - 1)
                        sy = min(max(sy, 0), height - 1)
                        offset = sy * stride + sx * 4
                    else:  # 'none': transparent black, no contribution
                        continue
                    blue += source[offset] * coefficient
                    green += source[offset + 1] * coefficient
                    red += source[offset + 2] * coefficient
                    alpha += source[offset + 3] * coefficient
                alpha = alpha / divisor + bias_255
                blue = blue / divisor + bias_255 * alpha / 255
                green = green / divisor + bias_255 * alpha / 255
                red = red / divisor + bias_255 * alpha / 255
            offset = y * stride + x * 4
            data[offset] = min(255, max(0, int(blue + 0.5)))
            data[offset + 1] = min(255, max(0, int(green + 0.5)))
            data[offset + 2] = min(255, max(0, int(red + 0.5)))
            data[offset + 3] = min(255, max(0, int(alpha + 0.5)))
    group.mark_dirty()


def convolve_matrix(surface, node, filter_node, child):
    """Apply a feConvolveMatrix filter primitive to the painted group.

    The convolution is computed on the rasterized source graphic (the group
    painted by the node), inside the filter region. Pixels outside the
    region are left untouched, and the rendering is left unchanged when the
    parameters are invalid or unsupported.

    """
    pattern = surface.context.get_source()
    if not isinstance(pattern, cairo.SurfacePattern):
        return
    group = pattern.get_surface()
    if not isinstance(group, cairo.ImageSurface):
        # Not a raster surface (e.g. PDF or SVG output): nothing to convolve
        return
    if group.get_format() != cairo.FORMAT_ARGB32:
        return

    # Parse and validate the parameters: invalid values make the primitive
    # inoperable, the source graphic is rendered unchanged
    try:
        order = [float(value) for value in
                 normalize(child.get('order', '3')).split()]
    except ValueError:
        return
    if len(order) == 1:
        order_x = order_y = order[0]
    elif len(order) == 2:
        order_x, order_y = order
    else:
        return
    if not (order_x.is_integer() and order_y.is_integer()):
        return
    order_x, order_y = int(order_x), int(order_y)
    if order_x <= 0 or order_y <= 0:
        return
    if order_x > MAX_CONVOLVE_ORDER or order_y > MAX_CONVOLVE_ORDER:
        # Kernel too large: bound the resource consumption
        return

    try:
        kernel = [float(value) for value in
                  normalize(child.get('kernelMatrix', '')).split()]
    except ValueError:
        return
    if len(kernel) != order_x * order_y:
        return  # Matrix length does not match order
    if not all(math.isfinite(value) for value in kernel):
        return  # Non-finite coefficients

    if 'divisor' in child:
        try:
            divisor = float(child['divisor'])
        except ValueError:
            return
        if divisor == 0 or not math.isfinite(divisor):
            return  # Divisor of zero is an error, ignore the primitive
    else:
        divisor = sum(kernel) or 1.0

    try:
        bias = float(child.get('bias', 0))
    except ValueError:
        return
    if not math.isfinite(bias):
        return

    try:
        target_x = (
            int(child['targetX']) if 'targetX' in child else order_x // 2)
        target_y = (
            int(child['targetY']) if 'targetY' in child else order_y // 2)
    except ValueError:
        return
    if not (0 <= target_x < order_x and 0 <= target_y < order_y):
        return

    edge_mode = child.get('edgeMode', 'duplicate')
    if edge_mode not in ('duplicate', 'none'):
        return  # 'wrap' and unknown modes are not supported

    preserve_alpha = child.get('preserveAlpha', 'false') == 'true'

    # Compute the region where the convolution applies, in user units
    region = _filter_region(surface, node, filter_node)
    region = _primitive_region(surface, node, filter_node, child, region)
    if region[2] <= 0 or region[3] <= 0:
        return

    # Map the region to device coordinates, then to group surface pixels
    corners = (
        surface.context.user_to_device(region[0], region[1]),
        surface.context.user_to_device(region[0] + region[2], region[1]),
        surface.context.user_to_device(region[0], region[1] + region[3]),
        surface.context.user_to_device(
            region[0] + region[2], region[1] + region[3]))
    offset_x, offset_y = group.get_device_offset()
    x0 = max(0, int(math.floor(min(c[0] for c in corners) + offset_x)))
    y0 = max(0, int(math.floor(min(c[1] for c in corners) + offset_y)))
    x1 = min(group.get_width(),
             int(math.ceil(max(c[0] for c in corners) + offset_x)))
    y1 = min(group.get_height(),
             int(math.ceil(max(c[1] for c in corners) + offset_y)))

    _convolve_region(
        group, kernel, order_x, order_y, target_x, target_y,
        divisor, bias, edge_mode, preserve_alpha, (x0, y0, x1, y1))


def apply_filter_after_painting(surface, node, name):
    """Apply a filter using the painted surface to transform the image."""
    if 'id' in node and node['id'] in surface.masks:
        return

    if name in surface.filters:
        filter_node = surface.filters[name]
        for child in filter_node.children:
            # Flood
            if child.tag == 'feFlood':
                surface.context.save()
                surface.context.new_path()
                if filter_node.get('primitiveUnits') == 'objectBoundingBox':
                    x = size(surface, node.get('x'), 'x')
                    y = size(surface, node.get('y'), 'y')
                    width = size(surface, node.get('width'), 'x')
                    height = size(surface, node.get('height'), 'y')
                else:
                    x, y, width, height = 0, 0, 1, 1
                x += size(surface, child.get('x', 0), 1)
                y += size(surface, child.get('y', 0), 1)
                width *= size(surface, child.get('width', 0), 1)
                height *= size(surface, child.get('height', 0), 1)
                rect(surface, dict(x=x, y=y, width=width, height=height))
                surface.context.set_source_rgba(*surface.map_color(
                    paint(child.get('flood-color'))[1],
                    float(child.get('flood-opacity', 1))))
                surface.context.fill()
                surface.context.restore()


def use(surface, node):
    """Draw the content of another SVG node."""
    surface.reference_count += 1
    if not node.unsafe and surface.reference_count > 100_000:
        raise ValueError(
            'Abort rendering: more than 100 000 referenced elements')
    surface.context.save()
    surface.context.translate(
        size(surface, node.get('x'), 'x'), size(surface, node.get('y'), 'y'))
    if 'x' in node:
        del node['x']
    if 'y' in node:
        del node['y']
    if 'viewBox' in node:
        del node['viewBox']
    if 'mask' in node:
        del node['mask']
    href = parse_url(node.get_href()).geturl()
    try:
        tree = Tree(
            url=href, url_fetcher=node.url_fetcher, parent=node,
            tree_cache=surface.tree_cache, unsafe=node.unsafe)
    except TypeError:
        surface.context.restore()
        return

    if not match_features(tree.xml_tree):
        surface.context.restore()
        return

    if tree.tag in ('svg', 'symbol'):
        # Explicitely specified
        # http://www.w3.org/TR/SVG11/struct.html#UseElement
        tree.tag = 'svg'
        if 'width' in node and 'height' in node:
            tree['width'], tree['height'] = node['width'], node['height']

    surface.draw(tree)
    node.get('fill', None)
    node.get('stroke', None)
    surface.context.restore()
