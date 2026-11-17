"""
Externally defined elements managers.

This module handles clips, gradients, masks, patterns and external nodes.

"""

from math import ceil, floor

from .bounding_box import calculate_bounding_box, is_non_empty_bounding_box
from .features import match_features
from .helpers import paint, size, transform
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

# Maximum number of pixels of the offscreen surfaces used by the feMerge
# raster pipeline. Bigger filter regions fall back to unfiltered rendering.
MAX_FILTER_SURFACE_PIXELS = 4096 * 4096


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


def filter_has_merge(surface, name):
    """Tell whether the filter ``name`` contains an ``feMerge`` primitive."""
    if name in surface.filters:
        return any(
            child.tag == 'feMerge'
            for child in surface.filters[name].children)
    return False


def filter_device_region(surface, node, filter_node):
    """Return the filter region in device pixels, or ``None`` if it is empty.

    A non-positive region disables the rendering of the filtered element.

    """
    if filter_node.get('filterUnits') == 'userSpaceOnUse':
        x = size(surface, filter_node.get('x', '-10%'), 'x')
        y = size(surface, filter_node.get('y', '-10%'), 'y')
        width = size(surface, filter_node.get('width', '120%'), 'x')
        height = size(surface, filter_node.get('height', '120%'), 'y')
    else:
        bounding_box = calculate_bounding_box(surface, node)
        if not is_non_empty_bounding_box(bounding_box):
            return None
        box_x, box_y, box_width, box_height = bounding_box
        x = box_x + size(surface, filter_node.get('x', '-10%'), 1) * box_width
        y = box_y + size(surface, filter_node.get('y', '-10%'), 1) * box_height
        width = size(surface, filter_node.get('width', '120%'), 1) * box_width
        height = (
            size(surface, filter_node.get('height', '120%'), 1) * box_height)
    if width <= 0 or height <= 0:
        return None
    # Transform the region to device space, keeping the axis-aligned
    # bounding box of the transformed corners
    corners = [
        surface.context.user_to_device(x + corner_x, y + corner_y)
        for corner_x, corner_y in (
            (0, 0), (width, 0), (0, height), (width, height))]
    x_coordinates, y_coordinates = zip(*corners)
    device_x = floor(min(x_coordinates))
    device_y = floor(min(y_coordinates))
    device_width = ceil(max(x_coordinates)) - device_x
    device_height = ceil(max(y_coordinates)) - device_y
    if device_width <= 0 or device_height <= 0:
        return None
    return device_x, device_y, device_width, device_height


def apply_merge_filter(surface, node, name, pattern, opacity):
    """Apply a filter containing ``feMerge`` as a raster pipeline.

    ``pattern`` is the painted content of ``node``, used as the filter's
    ``SourceGraphic``. Primitives are applied in document order, storing
    named ``result`` images, and the output of the last primitive is
    composited back onto the surface.

    Unknown primitives are ignored, and unknown or forward ``in``
    references resolve to transparent black. A result name can only be
    read after being defined, so circular references resolve to
    transparent black too.

    """
    filter_node = surface.filters[name]
    region = filter_device_region(surface, node, filter_node)
    if region is None:
        # Empty or non-positive filter region: nothing is rendered
        return
    device_x, device_y, device_width, device_height = region
    if device_width * device_height > MAX_FILTER_SURFACE_PIXELS:
        # The offscreen surface would be too large: degrade gracefully by
        # painting the source graphic without applying the filter
        surface.context.set_source(pattern)
        surface.context.paint_with_alpha(opacity)
        return

    matrix = surface.context.get_matrix()

    def create_surface():
        # Offscreen surfaces share the device space of the main surface:
        # pixel (0, 0) corresponds to device pixel (device_x, device_y)
        image = cairo.ImageSurface(
            cairo.FORMAT_ARGB32, device_width, device_height)
        image.set_device_offset(-device_x, -device_y)
        return image

    # Rasterize the source graphic into the filter region
    source_graphic = create_surface()
    context = cairo.Context(source_graphic)
    context.set_matrix(matrix)
    context.set_source(pattern)
    context.paint()

    source_alpha = None

    def get_source_alpha():
        nonlocal source_alpha
        if source_alpha is None:
            source_alpha = create_surface()
            context = cairo.Context(source_alpha)
            context.set_source_rgba(0, 0, 0, 1)
            context.mask_surface(source_graphic, 0, 0)
        return source_alpha

    results = {'SourceGraphic': source_graphic}

    def resolve(input_name, default):
        """Resolve an ``in`` attribute to an offscreen surface or ``None``."""
        if input_name is None:
            return default
        if input_name == 'SourceAlpha':
            return get_source_alpha()
        # Unknown and forward references are transparent black
        return results.get(input_name)

    bounding_box = None
    last = source_graphic
    for child in filter_node.children:
        output = None
        if child.tag == 'feOffset':
            input_ = resolve(child.get('in'), last)
            if input_ is not None:
                dx = size(surface, child.get('dx', 0), 1)
                dy = size(surface, child.get('dy', 0), 1)
                if filter_node.get('primitiveUnits') == 'objectBoundingBox':
                    if bounding_box is None:
                        bounding_box = calculate_bounding_box(surface, node)
                    if is_non_empty_bounding_box(bounding_box):
                        dx *= bounding_box[2]
                        dy *= bounding_box[3]
                    else:
                        dx = dy = 0
                device_dx, device_dy = (
                    surface.context.user_to_device_distance(dx, dy))
                output = create_surface()
                context = cairo.Context(output)
                context.set_source_surface(input_, device_dx, device_dy)
                context.paint()
        elif child.tag == 'feFlood':
            output = create_surface()
            context = cairo.Context(output)
            context.set_source_rgba(*surface.map_color(
                paint(child.get('flood-color'))[1],
                float(child.get('flood-opacity', 1))))
            context.paint()
        elif child.tag == 'feBlend':
            input_ = resolve(child.get('in'), last)
            input2 = resolve(child.get('in2'), None)
            output = create_surface()
            context = cairo.Context(output)
            if input2 is not None:
                context.set_source_surface(input2, 0, 0)
                context.paint()
            if input_ is not None:
                context.set_operator(BLEND_OPERATORS.get(
                    child.get('mode', 'normal'), BLEND_OPERATORS['normal']))
                context.set_source_surface(input_, 0, 0)
                context.paint()
        elif child.tag == 'feMerge':
            output = create_surface()
            context = cairo.Context(output)
            # Composite the inputs in node order, each one alpha-over the
            # previous ones, so that later nodes never erase earlier ones
            for merge_node in child.children:
                if merge_node.tag != 'feMergeNode':
                    continue
                input_ = resolve(merge_node.get('in'), source_graphic)
                if input_ is None:
                    continue
                context.set_source_surface(input_, 0, 0)
                context.paint()
        else:
            # Unsupported primitives are ignored; references to their
            # result resolve to transparent black
            continue
        result_name = child.get('result')
        if result_name:
            results[result_name] = output
        last = output

    if last is None:
        return
    # Composite the result back, aligned with the device space it was
    # rendered in
    surface.context.save()
    surface.context.identity_matrix()
    surface.context.set_source_surface(last, 0, 0)
    if opacity < 1:
        surface.context.paint_with_alpha(opacity)
    else:
        surface.context.paint()
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
