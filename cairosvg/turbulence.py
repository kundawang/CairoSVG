"""
feTurbulence filter primitive: Perlin noise generation.

This module implements the algorithm described in the SVG 1.1
specification (chapter 15, feTurbulence), including the pseudo-random
number generator, the lattice/gradient initialization, the Perlin noise
function, the octave accumulation for the ``fractalNoise`` and
``turbulence`` types, and the ``stitchTiles`` handling.

"""

import math

import cairocffi as cairo

# Pseudo-random number generator constants (SVG 1.1, section 15.25).
RAND_M = 2147483647  # 2**31 - 1
RAND_A = 16807  # 7**5, primitive root of RAND_M
RAND_Q = 127773  # RAND_M // RAND_A
RAND_R = 2836  # RAND_M % RAND_A

B_SIZE = 0x100  # Lattice size.
B_MASK = 0xFF
PERLIN_N = 0x1000  # Offset keeping lattice coordinates positive.

# Resource limit: refuse to generate noise for regions whose pixel count
# exceeds this value, to keep rendering time and memory bounded.
MAX_PIXELS = 2 ** 22  # e.g. 2048 x 2048


def _setup_seed(seed):
    """Normalize a seed into the [1, RAND_M - 1] range."""
    if seed <= 0:
        # fmod matches the C '%' operator for negative operands.
        seed = -int(math.fmod(seed, RAND_M - 1)) + 1
    if seed > RAND_M - 1:
        seed = RAND_M - 1
    return seed


def _random(seed):
    """Generate the next pseudo-random number from ``seed``."""
    result = RAND_A * (seed % RAND_Q) - RAND_R * (seed // RAND_Q)
    if result <= 0:
        result += RAND_M
    return result


def _init_tables(seed):
    """Build the lattice selector and gradient tables for ``seed``."""
    seed = _setup_seed(seed)
    lattice = list(range(B_SIZE))
    gradients = []
    for channel in range(4):
        channel_gradients = []
        for i in range(B_SIZE):
            seed = _random(seed)
            g0 = ((seed % (2 * B_SIZE)) - B_SIZE) / B_SIZE
            seed = _random(seed)
            g1 = ((seed % (2 * B_SIZE)) - B_SIZE) / B_SIZE
            norm = math.sqrt(g0 * g0 + g1 * g1)
            if norm:
                g0 /= norm
                g1 /= norm
            channel_gradients.append((g0, g1))
        gradients.append(channel_gradients)
    i = B_SIZE
    while i > 1:
        i -= 1
        seed = _random(seed)
        j = seed % B_SIZE
        lattice[i], lattice[j] = lattice[j], lattice[i]
    # Duplicate the tables to avoid overflow when indexing: the first
    # B_SIZE + 2 entries are repeated after the initial B_SIZE ones.
    lattice += lattice
    for channel in range(4):
        gradients[channel] += gradients[channel]
    return lattice, gradients


def _s_curve(t):
    return t * t * (3 - 2 * t)


def _stitch_frequency(base_frequency, tile_size):
    """Adjust ``base_frequency`` so that tiles stitch continuously."""
    if base_frequency == 0:
        return 0.0
    lo = math.floor(tile_size * base_frequency) / tile_size
    hi = math.ceil(tile_size * base_frequency) / tile_size
    if lo == 0:
        return hi
    if base_frequency / lo < hi / base_frequency:
        return lo
    return hi


def turbulence_rgba(width, height, base_frequency_x, base_frequency_y,
                    num_octaves, seed, fractal_sum, stitch,
                    tile_x=0, tile_y=0, tile_width=None, tile_height=None,
                    offset_x=None, offset_y=None):
    """Generate feTurbulence pixels as unpremultiplied RGBA bytes.

    ``width`` and ``height`` are the pixel dimensions of the region to
    generate. ``offset_x`` and ``offset_y`` give the region's origin in
    user units. ``tile_x``, ``tile_y``, ``tile_width`` and
    ``tile_height`` describe the tile used for stitching; they default
    to the generated region.

    Return a ``bytearray`` of ``width * height * 4`` bytes, or ``None``
    when the primitive is a no-op (empty region or invalid parameters).
    Raise ``ValueError`` when the region exceeds the resource limit.

    """
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        # Empty region: nothing to draw.
        return None
    if width * height > MAX_PIXELS:
        raise ValueError(
            'Abort rendering: feTurbulence region is too large '
            f'({width} x {height} pixels, limit is {MAX_PIXELS})')

    parameters = (base_frequency_x, base_frequency_y, num_octaves, seed)
    if not all(math.isfinite(value) for value in parameters):
        # Non-finite parameters make the primitive a no-op.
        return None
    if base_frequency_x < 0 or base_frequency_y < 0:
        # Negative frequencies are unsupported.
        return None
    num_octaves = int(num_octaves)
    if num_octaves < 1:
        return None

    if tile_width is None:
        tile_width = width
    if tile_height is None:
        tile_height = height
    if offset_x is None:
        offset_x = tile_x
    if offset_y is None:
        offset_y = tile_y

    if stitch:
        base_frequency_x = _stitch_frequency(base_frequency_x, tile_width)
        base_frequency_y = _stitch_frequency(base_frequency_y, tile_height)
        stitch_width = int(tile_width * base_frequency_x + 0.5)
        stitch_height = int(tile_height * base_frequency_y + 0.5)
        wrap_x = int(tile_x * base_frequency_x + PERLIN_N + stitch_width)
        wrap_y = int(tile_y * base_frequency_y + PERLIN_N + stitch_height)
    else:
        stitch_width = stitch_height = wrap_x = wrap_y = 0

    lattice, gradients = _init_tables(int(seed))

    pixels = bytearray(width * height * 4)
    for channel in range(4):
        gradient = gradients[channel]
        accumulator = [0.0] * (width * height)
        frequency_x = base_frequency_x
        frequency_y = base_frequency_y
        octave_stitch_width = stitch_width
        octave_stitch_height = stitch_height
        octave_wrap_x = wrap_x
        octave_wrap_y = wrap_y
        ratio = 1.0
        for _octave in range(num_octaves):
            # Precompute the per-column and per-row lattice data, shared
            # by the four color channels. When stitching, lattice
            # coordinates are wrapped into the canonical tile range with
            # a modulo, making the noise periodic everywhere.
            base_x = octave_wrap_x - octave_stitch_width
            base_y = octave_wrap_y - octave_stitch_height
            columns = []
            for px in range(width):
                t = (offset_x + px) * frequency_x + PERLIN_N
                bx0 = int(t)
                rx0 = t - bx0
                bx1 = bx0 + 1
                if stitch and octave_stitch_width:
                    bx0 = (bx0 - base_x) % octave_stitch_width + base_x
                    bx1 = (bx1 - base_x) % octave_stitch_width + base_x
                columns.append((
                    bx0 & B_MASK, bx1 & B_MASK, rx0, rx0 - 1,
                    _s_curve(rx0)))
            rows = []
            for py in range(height):
                t = (offset_y + py) * frequency_y + PERLIN_N
                by0 = int(t)
                ry0 = t - by0
                by1 = by0 + 1
                if stitch and octave_stitch_height:
                    by0 = (by0 - base_y) % octave_stitch_height + base_y
                    by1 = (by1 - base_y) % octave_stitch_height + base_y
                rows.append((by0 & B_MASK, by1 & B_MASK, ry0, ry0 - 1,
                             _s_curve(ry0)))
            for py, (by0, by1, ry0, ry1, sy) in enumerate(rows):
                offset = py * width
                for px, (bx0, bx1, rx0, rx1, sx) in enumerate(columns):
                    i = lattice[bx0]
                    j = lattice[bx1]
                    b00 = lattice[i + by0]
                    b10 = lattice[j + by0]
                    b01 = lattice[i + by1]
                    b11 = lattice[j + by1]
                    g = gradient[b00]
                    u = rx0 * g[0] + ry0 * g[1]
                    g = gradient[b10]
                    v = rx1 * g[0] + ry0 * g[1]
                    a = u + sx * (v - u)
                    g = gradient[b01]
                    u = rx0 * g[0] + ry1 * g[1]
                    g = gradient[b11]
                    v = rx1 * g[0] + ry1 * g[1]
                    b = u + sx * (v - u)
                    noise = a + sy * (b - a)
                    if not fractal_sum:
                        noise = abs(noise)
                    accumulator[offset + px] += noise / ratio
            frequency_x *= 2
            frequency_y *= 2
            ratio *= 2
            if stitch:
                octave_stitch_width *= 2
                octave_wrap_x = 2 * octave_wrap_x - PERLIN_N
                octave_stitch_height *= 2
                octave_wrap_y = 2 * octave_wrap_y - PERLIN_N
        # Map the accumulated noise to a byte channel.
        for index in range(width * height):
            value = accumulator[index]
            if fractal_sum:
                value = value * 0.5 + 0.5
            if value < 0:
                value = 0
            elif value > 1:
                value = 1
            pixels[index * 4 + channel] = int(value * 255 + 0.5)
    return pixels


def turbulence_image(width, height, base_frequency_x, base_frequency_y,
                     num_octaves, seed, fractal_sum, stitch,
                     tile_x=0, tile_y=0, tile_width=None, tile_height=None,
                     offset_x=None, offset_y=None):
    """Generate feTurbulence pixels as a premultiplied ARGB32 surface.

    Return a ``cairo.ImageSurface``, or ``None`` when the primitive is a
    no-op. Raise ``ValueError`` when the region exceeds the resource
    limit.

    """
    pixels = turbulence_rgba(
        width, height, base_frequency_x, base_frequency_y, num_octaves,
        seed, fractal_sum, stitch, tile_x, tile_y, tile_width, tile_height,
        offset_x, offset_y)
    if pixels is None:
        return None
    width = int(width)
    height = int(height)
    # Cairo's ARGB32 format stores premultiplied B, G, R, A bytes on
    # little-endian machines. Premultiply the color channels so that the
    # color and alpha ranges stay legal (color <= alpha).
    premultiplied = bytearray(width * height * 4)
    for index in range(width * height):
        rgba = index * 4
        alpha = pixels[rgba + 3]
        premultiplied[rgba] = (pixels[rgba + 2] * alpha + 127) // 255
        premultiplied[rgba + 1] = (pixels[rgba + 1] * alpha + 127) // 255
        premultiplied[rgba + 2] = (pixels[rgba] * alpha + 127) // 255
        premultiplied[rgba + 3] = alpha
    return cairo.ImageSurface.create_for_data(
        premultiplied, cairo.FORMAT_ARGB32, width, height)


def parse_base_frequency(value):
    """Parse a baseFrequency attribute into a ``(x, y)`` float tuple.

    Return ``None`` when the value is invalid, making the primitive a
    no-op.

    """
    if value is None:
        return 0.0, 0.0
    parts = value.replace(',', ' ').split()
    if len(parts) > 2:
        return None
    try:
        frequencies = [float(part) for part in parts]
    except ValueError:
        return None
    if not frequencies:
        return 0.0, 0.0
    if len(frequencies) == 1:
        frequencies *= 2
    return tuple(frequencies)
