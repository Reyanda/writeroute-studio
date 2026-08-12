"""Lossless Vector Codec — bit-exact pure-vector encoding of raster pixels.

The codec expresses an arbitrary set of source pixels as integer-aligned,
axis-parallel vector geometry that a conforming SVG renderer reproduces without
any loss. It contains no raster image, performs no quantisation, dithering,
smoothing or colour transformation, and never reorders overlapping coverage.

Design
------
1. **Run extraction.** Each row is reduced to maximal horizontal runs of one
   exact RGBA value using vectorised comparisons.
2. **Vertical merging.** Runs with identical ``(x, length, colour)`` in
   consecutive rows collapse into a single rectangle, so flat regions cost one
   rectangle rather than one per row.
3. **Colour grouping.** Rectangles sharing an exact RGBA value are emitted as
   subpaths of one compound ``<path>``, which removes per-element attribute
   overhead. Subpath moves are relative and coordinates are integers.
4. **Symbol mining.** Repeated identical pixel blocks — glyphs, icons, table
   cells, window chrome — are emitted once as a ``<symbol>`` and referenced with
   ``<use>``. This is a pure compression stage over already-exact output.

Exactness relies on integer geometry plus ``shape-rendering="crispEdges"``: every
rectangle edge falls on a pixel boundary, so no antialiasing is introduced and
each covered pixel receives exactly one fill.

Fully transparent pixels are never painted. A conforming renderer leaves them at
``(0, 0, 0, 0)``, which is why parity must be asserted on premultiplied RGBA —
the source may carry arbitrary colour channels beneath zero alpha.
"""

from __future__ import annotations

import math
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
from PIL import Image

SVG_NS = "http://www.w3.org/2000/svg"

#: Above this pixel count the encoder processes the source in horizontal bands
#: so peak memory stays proportional to the band rather than the whole canvas.
DEFAULT_TILE_PIXELS = 8_000_000

#: Symbol mining is only attempted when the payload is large enough for the
#: definition overhead to pay for itself.
MIN_RECTS_FOR_MINING = 2_000

#: Contour tracing chains boundary edges in Python, so it is skipped when the
#: total boundary length would make it slower than the rectangle encoder for a
#: payload it cannot beat anyway.
MAX_CONTOUR_EDGES = 6_000_000

#: Rectangles per covered pixel — how fragmented the image is. Flat and UI
#: content measures around 0.01–0.03; noise, gradients and antialiased artwork
#: measure 0.5–1.0. Contour tracing and component mining have never won above
#: the low band, and running them anyway costs multiples of the encode time for
#: nothing, so both are gated on this cheaply known ratio.
FRAGMENTATION_LIMIT = 0.25


@dataclass
class LVCStats:
    """Measured cost of one encode, reported rather than estimated."""

    width: int = 0
    height: int = 0
    covered_pixels: int = 0
    rectangles: int = 0
    colours: int = 0
    symbols: int = 0
    symbol_instances: int = 0
    rectangles_deduplicated: int = 0
    contours: int = 0
    geometry: str = "rectangle"  # rectangle | contour | block-symbol | component
    svg_bytes: int = 0
    tiles: int = 1

    @property
    def coverage(self) -> float:
        total = self.width * self.height
        return float(self.covered_pixels) / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "covered_pixels": self.covered_pixels,
            "coverage": self.coverage,
            "rectangles": self.rectangles,
            "colours": self.colours,
            "symbols": self.symbols,
            "symbol_instances": self.symbol_instances,
            "rectangles_deduplicated": self.rectangles_deduplicated,
            "contours": self.contours,
            "geometry": self.geometry,
            "svg_bytes": self.svg_bytes,
            "tiles": self.tiles,
        }


@dataclass
class RectangleSet:
    """Disjoint integer rectangles carrying exact RGBA keys."""

    x: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    y: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    width: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    height: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    colour: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.uint64))

    def __len__(self) -> int:
        return int(self.x.shape[0])

    @classmethod
    def concatenate(cls, parts: Iterable[RectangleSet]) -> RectangleSet:
        collected = [part for part in parts if len(part)]
        if not collected:
            return cls()
        return cls(
            x=np.concatenate([part.x for part in collected]),
            y=np.concatenate([part.y for part in collected]),
            width=np.concatenate([part.width for part in collected]),
            height=np.concatenate([part.height for part in collected]),
            colour=np.concatenate([part.colour for part in collected]),
        )


def _rgba_key(pixels: np.ndarray) -> np.ndarray:
    """Pack RGBA into one unsigned integer so comparisons are single-column."""
    values = pixels.astype(np.uint64)
    return (
        (values[..., 0] << np.uint64(24))
        | (values[..., 1] << np.uint64(16))
        | (values[..., 2] << np.uint64(8))
        | values[..., 3]
    )


def _unpack_key(key: int) -> tuple[int, int, int, int]:
    return ((key >> 24) & 0xFF, (key >> 16) & 0xFF, (key >> 8) & 0xFF, key & 0xFF)


def extract_rectangles(
    pixels: np.ndarray,
    mask: np.ndarray | None = None,
    *,
    y_offset: int = 0,
) -> RectangleSet:
    """Decompose a masked pixel array into maximal same-colour rectangles.

    ``pixels`` is an ``(h, w, 4)`` uint8 RGBA array. ``mask`` selects which
    pixels participate; when omitted every non-transparent pixel is encoded.
    """
    if pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError("Expected an (h, w, 4) RGBA array.")
    height, width = pixels.shape[:2]
    if height == 0 or width == 0:
        return RectangleSet()

    key = _rgba_key(pixels)
    if mask is None:
        active = pixels[..., 3] > 0
    else:
        active = np.asarray(mask, dtype=bool) & (pixels[..., 3] > 0)
    if not active.any():
        return RectangleSet()

    # A run continues where the previous column is active with the same colour.
    continues = np.zeros((height, width), dtype=bool)
    if width > 1:
        continues[:, 1:] = (key[:, 1:] == key[:, :-1]) & active[:, 1:] & active[:, :-1]

    starts = active & ~continues
    # A run ends where the next column does not continue it.
    ends = np.zeros((height, width), dtype=bool)
    if width > 1:
        ends[:, :-1] = active[:, :-1] & ~continues[:, 1:]
    ends[:, -1] = active[:, -1]

    start_rows, start_cols = np.nonzero(starts)
    end_cols = np.nonzero(ends)[1]
    lengths = end_cols - start_cols + 1
    colours = key[start_rows, start_cols]

    # Merge vertically: identical (x, length, colour) on consecutive rows.
    order = np.lexsort((start_rows, lengths, start_cols, colours))
    rows = start_rows[order]
    cols = start_cols[order]
    lens = lengths[order]
    cols_key = colours[order]

    boundary = np.ones(rows.shape[0], dtype=bool)
    if rows.shape[0] > 1:
        boundary[1:] = ~(
            (cols_key[1:] == cols_key[:-1])
            & (cols[1:] == cols[:-1])
            & (lens[1:] == lens[:-1])
            & (rows[1:] == rows[:-1] + 1)
        )
    group_starts = np.nonzero(boundary)[0]
    top = rows[group_starts]
    bottom = np.maximum.reduceat(rows, group_starts)

    return RectangleSet(
        x=cols[group_starts].astype(np.int64),
        y=top.astype(np.int64) + int(y_offset),
        width=lens[group_starts].astype(np.int64),
        height=(bottom - top + 1).astype(np.int64),
        colour=cols_key[group_starts].astype(np.uint64),
    )


def _format_opacity(alpha: int) -> str:
    """Emit an opacity that round-trips to the same 8-bit alpha."""
    return f"{alpha / 255.0:.6f}".rstrip("0").rstrip(".")


def _compound_path_data(
    xs: np.ndarray,
    ys: np.ndarray,
    widths: np.ndarray,
    heights: np.ndarray,
    *,
    presorted: bool = False,
) -> str:
    """Build one ``d`` attribute of relative rectangle subpaths.

    Coordinates are emitted as deltas from the previous subpath origin, which
    keeps the numbers small and highly compressible. Deltas are computed with
    numpy and the join runs over Python lists, because per-element numpy scalar
    access dominates the cost at hundreds of thousands of rectangles.
    """
    if not presorted:
        order = np.lexsort((xs, ys))
        xs, ys, widths, heights = xs[order], ys[order], widths[order], heights[order]
    delta_x = np.diff(xs, prepend=np.int64(0)).tolist()
    delta_y = np.diff(ys, prepend=np.int64(0)).tolist()
    span = widths.tolist()
    rise = heights.tolist()
    body = "".join(
        [
            f"m{dx} {dy}h{w}v{h}h{-w}z"
            for dx, dy, w, h in zip(delta_x, delta_y, span, rise)
        ]
    )
    return "M0 0" + body


def _colour_groups(
    rectangles: RectangleSet, keep: np.ndarray | None = None
) -> Iterable[tuple[int, np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Yield ``(colour, x, y, width, height)`` sorted once for the whole set.

    One global lexsort replaces a per-colour sort plus a per-colour boolean scan
    over every rectangle, which is quadratic in the number of distinct colours.
    """
    x, y = rectangles.x, rectangles.y
    width, height, colour = rectangles.width, rectangles.height, rectangles.colour
    if keep is not None:
        x, y = x[keep], y[keep]
        width, height, colour = width[keep], height[keep], colour[keep]
    if not x.size:
        return
    order = np.lexsort((x, y, colour))
    x, y = x[order], y[order]
    width, height, colour = width[order], height[order], colour[order]
    boundaries = np.nonzero(np.r_[True, colour[1:] != colour[:-1]])[0]
    edges = np.r_[boundaries, colour.size]
    for index in range(boundaries.size):
        start, stop = int(edges[index]), int(edges[index + 1])
        yield (
            int(colour[start]),
            x[start:stop],
            y[start:stop],
            width[start:stop],
            height[start:stop],
        )


def _paint_fragments(rectangles: RectangleSet, keep: np.ndarray | None = None) -> list[str]:
    """Render every colour group as one compound path element."""
    fragments: list[str] = []
    for key, x, y, width, height in _colour_groups(rectangles, keep):
        red, green, blue, alpha = _unpack_key(key)
        opacity = "" if alpha == 255 else f' fill-opacity="{_format_opacity(alpha)}"'
        data = _compound_path_data(x, y, width, height, presorted=True)
        fragments.append(f'<path fill="#{red:02x}{green:02x}{blue:02x}"{opacity} d="{data}"/>')
    return fragments


def _mine_symbols(
    pixels: np.ndarray,
    rectangles: RectangleSet,
    *,
    block: int,
    minimum_repeats: int,
) -> tuple[dict[bytes, list[tuple[int, int]]], np.ndarray]:
    """Find identical pixel blocks worth emitting once and referencing.

    Returns the reusable blocks with their placements, plus a boolean array
    marking which rectangles are fully contained by a referenced block and can
    therefore be dropped from the inline geometry.
    """
    height, width = pixels.shape[:2]
    if block <= 0 or height < block or width < block:
        return {}, np.zeros(len(rectangles), dtype=bool)

    rows = height // block
    cols = width // block
    if rows * cols < minimum_repeats:
        return {}, np.zeros(len(rectangles), dtype=bool)

    trimmed = pixels[: rows * block, : cols * block]
    blocks = (
        trimmed.reshape(rows, block, cols, block, 4)
        .transpose(0, 2, 1, 3, 4)
        .reshape(rows * cols, block * block * 4)
    )
    # Ignore blocks that are entirely transparent; they cost nothing already.
    opaque_any = blocks.reshape(rows * cols, block * block, 4)[..., 3].any(axis=1)

    digests: dict[bytes, list[tuple[int, int]]] = defaultdict(list)
    payloads = blocks.tobytes()
    stride = block * block * 4
    for index in range(rows * cols):
        if not opaque_any[index]:
            continue
        digest = payloads[index * stride : (index + 1) * stride]
        digests[digest].append(((index % cols) * block, (index // cols) * block))

    reusable = {
        digest: placements
        for digest, placements in digests.items()
        if len(placements) >= minimum_repeats
    }
    if not reusable:
        return {}, np.zeros(len(rectangles), dtype=bool)

    covered = np.zeros((height, width), dtype=bool)
    for placements in reusable.values():
        for x, y in placements:
            covered[y : y + block, x : x + block] = True

    # Only drop rectangles lying wholly inside a referenced block region.
    contained = np.zeros(len(rectangles), dtype=bool)
    if len(rectangles):
        block_x = rectangles.x // block
        block_y = rectangles.y // block
        same_block = (
            ((rectangles.x + rectangles.width - 1) // block == block_x)
            & ((rectangles.y + rectangles.height - 1) // block == block_y)
            & (rectangles.y + rectangles.height <= rows * block)
            & (rectangles.x + rectangles.width <= cols * block)
        )
        candidates = np.nonzero(same_block)[0]
        for index in candidates:
            if covered[int(rectangles.y[index]), int(rectangles.x[index])]:
                contained[index] = True
    return reusable, contained


def _symbol_geometry(payload: bytes, block: int) -> str:
    """Encode one reusable block as self-contained exact geometry."""
    tile = np.frombuffer(payload, dtype=np.uint8).reshape(block, block, 4)
    return "".join(_paint_fragments(extract_rectangles(tile)))


def encode_pixels(
    image: Image.Image,
    mask: np.ndarray | None = None,
    *,
    mine_symbols: bool = True,
    mine_components: bool = True,
    trace_contours: bool = True,
    component_repeats: int = 3,
    symbol_block: int = 16,
    symbol_repeats: int = 4,
    tile_pixels: int = DEFAULT_TILE_PIXELS,
    element_id: str | None = None,
    role: str | None = None,
) -> tuple[str, LVCStats]:
    """Encode selected pixels as an exact SVG fragment plus its measured cost.

    The fragment is a single ``<g>`` and is safe to place inside any Tracer
    document. It never contains an ``<image>`` element.
    """
    source = image.convert("RGBA")
    width, height = source.size
    pixels = np.asarray(source, dtype=np.uint8)
    selection = None if mask is None else np.asarray(mask, dtype=bool)
    if selection is not None and selection.shape != (height, width):
        raise ValueError("Mask shape does not match the source image.")

    band_rows = height
    if tile_pixels > 0 and width > 0:
        band_rows = max(1, min(height, int(tile_pixels // max(1, width))))
    tiles = max(1, math.ceil(height / band_rows)) if height else 1

    parts: list[RectangleSet] = []
    for top in range(0, height, band_rows):
        bottom = min(height, top + band_rows)
        band_mask = None if selection is None else selection[top:bottom]
        parts.append(extract_rectangles(pixels[top:bottom], band_mask, y_offset=top))
    rectangles = RectangleSet.concatenate(parts)

    covered = int(np.sum(rectangles.width * rectangles.height)) if len(rectangles) else 0
    stats = LVCStats(
        width=width,
        height=height,
        covered_pixels=covered,
        rectangles=len(rectangles),
        colours=int(np.unique(rectangles.colour).size) if len(rectangles) else 0,
        tiles=tiles,
    )
    if not len(rectangles):
        attributes = _group_attributes(element_id, role)
        return f"<g{attributes}/>", stats

    body = "".join(_paint_fragments(rectangles))

    # Contour tracing turns each colour region into real closed polygons rather
    # than a pile of rectangles, so a flat area costs four commands instead of
    # one rectangle per run. It loses on noisy content, where every pixel
    # contributes boundary, so the two encodings are compared on deflated size.
    key = _rgba_key(pixels)
    active = pixels[..., 3] > 0 if selection is None else selection & (pixels[..., 3] > 0)
    fragmentation = len(rectangles) / max(1, covered)
    worth_restructuring = fragmentation <= FRAGMENTATION_LIMIT

    if trace_contours and worth_restructuring:
        traced = _contour_fragments(key, active, width)
        if traced is not None:
            contour_body = "".join(traced[0])
            if contour_body and _compressed_size(contour_body) < _compressed_size(body):
                body = contour_body
                stats.contours = traced[1]
                stats.geometry = "contour"

    # Repeated shapes at arbitrary positions — glyphs, icons, table cells — are
    # emitted once and referenced. Unlike grid-aligned block mining this finds
    # repeats wherever they land, which is where real text repetition lives.
    if mine_components and worth_restructuring and len(rectangles) >= MIN_RECTS_FOR_MINING:
        mined = _mine_components(pixels, active, minimum_repeats=component_repeats)
        if mined is not None:
            shapes, covered = mined
            definitions: list[str] = []
            uses: list[str] = []
            instances = 0
            for index, shape in enumerate(shapes):
                symbol_id = f"tracer-lvc-g{index}"
                definitions.append(
                    f'<symbol id="{symbol_id}" overflow="visible">'
                    f"{_component_symbol_body(shape.patch, shape.member)}</symbol>"
                )
                uses.extend(
                    f'<use href="#{symbol_id}" x="{left}" y="{top}"/>'
                    for left, top in shape.placements
                )
                instances += len(shape.placements)

            remaining = active & ~covered
            leftover = _best_body(pixels, key, remaining, width, trace_contours)
            candidate = (
                f"<defs>{''.join(definitions)}</defs>{leftover}{''.join(uses)}"
            )
            if _compressed_size(candidate) < _compressed_size(body):
                body = candidate
                stats.symbols = len(shapes)
                stats.symbol_instances = instances
                stats.geometry = "component"

    # Symbol mining trades element count against definition overhead. Deflate
    # already exploits repeated path data very effectively, so mining is only
    # kept when it measurably reduces the compressed payload that actually
    # ships. Assuming it helps made the repeated-glyph fixture ten times larger.
    if mine_symbols and len(rectangles) >= MIN_RECTS_FOR_MINING and selection is None:
        reusable, contained = _mine_symbols(
            pixels,
            rectangles,
            block=symbol_block,
            minimum_repeats=symbol_repeats,
        )
        if reusable and np.any(contained):
            definitions: list[str] = []
            uses: list[str] = []
            instances = 0
            for index, (payload, placements) in enumerate(reusable.items()):
                symbol_id = f"tracer-lvc-s{index}"
                definitions.append(
                    f'<symbol id="{symbol_id}" overflow="visible">'
                    f"{_symbol_geometry(payload, symbol_block)}</symbol>"
                )
                uses.extend(
                    f'<use href="#{symbol_id}" x="{x}" y="{y}"/>' for x, y in placements
                )
                instances += len(placements)
            mined = (
                f"<defs>{''.join(definitions)}</defs>"
                f"{''.join(_paint_fragments(rectangles, ~contained))}"
                f"{''.join(uses)}"
            )
            if _compressed_size(mined) < _compressed_size(body):
                body = mined
                stats.symbols = len(reusable)
                stats.symbol_instances = instances
                stats.rectangles_deduplicated = int(np.count_nonzero(contained))
                stats.geometry = "block-symbol"

    attributes = _group_attributes(element_id, role)
    fragment = f"<g{attributes}>{body}</g>"
    stats.svg_bytes = len(fragment.encode("utf-8"))
    return fragment, stats


def _boundary_edges(
    key: np.ndarray, active: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return every directed unit boundary segment in the image, per colour.

    A boundary segment exists wherever a painted pixel meets a neighbour of a
    different colour, or the canvas edge. Directions are chosen so that the
    painted region always lies on the same side, which makes outer contours and
    hole contours wind oppositely — the SVG nonzero fill rule then renders holes
    correctly without any explicit hole bookkeeping.

    Segments are emitted as ``(x0, y0, x1, y1, colour)`` on the integer pixel
    lattice, so every coordinate is a pixel corner.
    """
    height, width = key.shape
    padded_key = np.zeros((height + 2, width + 2), dtype=key.dtype)
    padded_key[1:-1, 1:-1] = key
    padded_active = np.zeros((height + 2, width + 2), dtype=bool)
    padded_active[1:-1, 1:-1] = active

    inner = padded_active[1:-1, 1:-1]
    same = lambda shifted_key, shifted_active: shifted_active & (  # noqa: E731
        shifted_key == padded_key[1:-1, 1:-1]
    )
    up = same(padded_key[:-2, 1:-1], padded_active[:-2, 1:-1])
    down = same(padded_key[2:, 1:-1], padded_active[2:, 1:-1])
    left = same(padded_key[1:-1, :-2], padded_active[1:-1, :-2])
    right = same(padded_key[1:-1, 2:], padded_active[1:-1, 2:])

    segments: list[tuple[np.ndarray, ...]] = []
    # Walking each pixel's own border clockwise in screen coordinates.
    for mask, (dx0, dy0, dx1, dy1) in (
        (inner & ~up, (0, 0, 1, 0)),
        (inner & ~right, (1, 0, 1, 1)),
        (inner & ~down, (1, 1, 0, 1)),
        (inner & ~left, (0, 1, 0, 0)),
    ):
        ys, xs = np.nonzero(mask)
        if not ys.size:
            continue
        segments.append(
            (
                xs + dx0,
                ys + dy0,
                xs + dx1,
                ys + dy1,
                key[ys, xs],
            )
        )
    if not segments:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty, empty, empty, np.empty(0, dtype=key.dtype)
    return tuple(np.concatenate(parts) for parts in zip(*segments))  # type: ignore[return-value]


def _chain_contours(
    x0: np.ndarray, y0: np.ndarray, x1: np.ndarray, y1: np.ndarray, width: int
) -> list[list[tuple[int, int]]]:
    """Link directed unit segments into closed contours.

    At a pinch point several contours meet and the pairing is ambiguous. Any
    pairing is acceptable: the winding number at every point depends only on the
    set of directed edges crossed, not on how those edges were grouped into
    loops, so the rendered fill is identical either way.
    """
    stride = width + 1
    starts = (y0 * stride + x0).tolist()
    ends = (y1 * stride + x1).tolist()
    outgoing: dict[int, list[int]] = defaultdict(list)
    for index, node in enumerate(starts):
        outgoing[node].append(index)

    used = bytearray(len(starts))
    contours: list[list[tuple[int, int]]] = []
    for index in range(len(starts)):
        if used[index]:
            continue
        loop: list[tuple[int, int]] = []
        cursor = index
        while True:
            used[cursor] = 1
            node = starts[cursor]
            loop.append((node % stride, node // stride))
            following = outgoing.get(ends[cursor])
            if not following:
                break
            nxt = -1
            while following:
                candidate = following[-1]
                if used[candidate]:
                    following.pop()
                    continue
                nxt = candidate
                break
            if nxt < 0:
                break
            cursor = nxt
        if len(loop) < 4:
            # A well-formed boundary edge set is Eulerian — every lattice node
            # has equal in and out degree — so every chain closes and the
            # shortest possible contour is the four edges of one pixel. A
            # shorter chain means the edge set was malformed, and silently
            # dropping it would emit geometry that is missing pixels. Abandon
            # contour tracing instead and let the rectangle encoder answer.
            return []
        contours.append(loop)
    return contours


def _contour_path_data(contours: list[list[tuple[int, int]]]) -> str:
    """Emit closed contours as relative axis-aligned path data.

    Consecutive collinear steps are merged, so a flat rectangular region costs
    four commands regardless of how many pixels it spans.
    """
    parts: list[str] = ["M0 0"]
    previous_x = 0
    previous_y = 0
    for loop in contours:
        start_x, start_y = loop[0]
        parts.append(f"m{start_x - previous_x} {start_y - previous_y}")
        current_x, current_y = start_x, start_y
        pending_x = 0
        pending_y = 0
        for point_x, point_y in loop[1:]:
            step_x = point_x - current_x
            step_y = point_y - current_y
            if step_x and pending_y:
                parts.append(f"v{pending_y}")
                pending_y = 0
            if step_y and pending_x:
                parts.append(f"h{pending_x}")
                pending_x = 0
            pending_x += step_x
            pending_y += step_y
            current_x, current_y = point_x, point_y
        if pending_x:
            parts.append(f"h{pending_x}")
        if pending_y:
            parts.append(f"v{pending_y}")
        parts.append("z")
        previous_x, previous_y = start_x, start_y
    return "".join(parts)


def _contour_fragments(
    key: np.ndarray, active: np.ndarray, width: int
) -> tuple[list[str], int] | None:
    """Encode every colour region as exact closed contours.

    Returns ``None`` when the boundary is too long for this representation to be
    worth chaining, leaving the rectangle encoder as the result.
    """
    x0, y0, x1, y1, colours = _boundary_edges(key, active)
    if not x0.size or x0.size > MAX_CONTOUR_EDGES:
        return None

    order = np.argsort(colours, kind="stable")
    x0, y0, x1, y1, colours = (
        x0[order],
        y0[order],
        x1[order],
        y1[order],
        colours[order],
    )
    boundaries = np.nonzero(np.r_[True, colours[1:] != colours[:-1]])[0]
    edges = np.r_[boundaries, colours.size]

    fragments: list[str] = []
    contour_total = 0
    for index in range(boundaries.size):
        start, stop = int(edges[index]), int(edges[index + 1])
        contours = _chain_contours(
            x0[start:stop], y0[start:stop], x1[start:stop], y1[start:stop], width
        )
        if not contours:
            # Malformed chain for this colour: abandon the whole contour
            # candidate rather than emit a document missing one colour layer.
            return None
        contour_total += len(contours)
        red, green, blue, alpha = _unpack_key(int(colours[start]))
        opacity = "" if alpha == 255 else f' fill-opacity="{_format_opacity(alpha)}"'
        fragments.append(
            f'<path fill="#{red:02x}{green:02x}{blue:02x}"{opacity} '
            f'd="{_contour_path_data(contours)}"/>'
        )
    return fragments, contour_total


@dataclass
class ReusableShape:
    """One repeated connected shape and every position it occurs at."""

    patch: np.ndarray
    member: np.ndarray
    placements: list[tuple[int, int]]


def _best_body(
    pixels: np.ndarray,
    key: np.ndarray,
    selection: np.ndarray,
    width: int,
    trace_contours: bool,
) -> str:
    """Encode a pixel selection with whichever geometry compresses better."""
    if not selection.any():
        return ""
    rectangle_body = "".join(_paint_fragments(extract_rectangles(pixels, selection)))
    if not trace_contours:
        return rectangle_body
    traced = _contour_fragments(key, selection, width)
    if traced is None or not traced[0]:
        return rectangle_body
    contour_body = "".join(traced[0])
    return (
        contour_body
        if _compressed_size(contour_body) < _compressed_size(rectangle_body)
        else rectangle_body
    )


def _mine_components(
    pixels: np.ndarray,
    active: np.ndarray,
    *,
    minimum_repeats: int,
    max_components: int = 200_000,
) -> tuple[list[ReusableShape], np.ndarray] | None:
    """Find repeated connected shapes at arbitrary positions.

    Grid-aligned block mining only catches repeats that happen to land on a
    fixed lattice, which real glyphs and icons never do. This stage labels
    connected regions of painted pixels, hashes each one by its exact shape and
    colours relative to its own bounding box, and reports the groups that recur.

    Returns the reusable shapes with their placements and a mask of the pixels
    they cover, or ``None`` when the content is unsuitable.
    """
    try:
        from scipy import ndimage
    except ImportError:  # pragma: no cover - scipy is a declared dependency
        return None

    # The dominant colour is the page or panel background. Including it would
    # connect the whole canvas into one component and find no repeats at all,
    # so it is excluded and left to the regular encoder.
    key = _rgba_key(pixels)
    active_keys = key[active]
    if not active_keys.size:
        return None
    values, counts = np.unique(active_keys, return_counts=True)
    background = values[int(np.argmax(counts))]
    foreground = active & (key != background)
    if not foreground.any():
        return None

    structure = np.ones((3, 3), dtype=bool)
    labels, count = ndimage.label(foreground, structure=structure)
    if count < minimum_repeats or count > max_components:
        return None

    boxes = ndimage.find_objects(labels)
    groups: dict[bytes, list[tuple[int, int]]] = defaultdict(list)
    payloads: dict[bytes, tuple[np.ndarray, np.ndarray]] = {}
    for index, box in enumerate(boxes, start=1):
        if box is None:
            continue
        rows, columns = box
        patch_labels = labels[rows, columns]
        member = patch_labels == index
        patch = pixels[rows, columns].copy()
        patch[~member] = 0
        digest = b"%d:%d:" % member.shape + patch.tobytes() + member.tobytes()
        groups[digest].append((int(columns.start), int(rows.start)))
        if digest not in payloads:
            payloads[digest] = (patch, member)

    shapes = [
        ReusableShape(payloads[digest][0], payloads[digest][1], placements)
        for digest, placements in groups.items()
        if len(placements) >= minimum_repeats
    ]
    if not shapes:
        return None

    covered = np.zeros(foreground.shape, dtype=bool)
    for shape in shapes:
        height, width = shape.member.shape
        for left, top in shape.placements:
            covered[top : top + height, left : left + width] |= shape.member
    return shapes, covered


def _component_symbol_body(patch: np.ndarray, member: np.ndarray) -> str:
    """Encode one reusable shape as self-contained exact geometry."""
    key = _rgba_key(patch)
    traced = _contour_fragments(key, member, patch.shape[1])
    if traced is not None and traced[0]:
        contour_body = "".join(traced[0])
        rectangle_body = "".join(_paint_fragments(extract_rectangles(patch, member)))
        if _compressed_size(contour_body) < _compressed_size(rectangle_body):
            return contour_body
        return rectangle_body
    return "".join(_paint_fragments(extract_rectangles(patch, member)))


def _compressed_size(fragment: str) -> int:
    """Return the deflate size that determines the shipped payload cost."""
    return len(zlib.compress(fragment.encode("utf-8"), 6))


def _group_attributes(element_id: str | None, role: str | None) -> str:
    attributes = ' shape-rendering="crispEdges"'
    if element_id:
        attributes = f' id="{element_id}"' + attributes
    if role:
        attributes += f' data-tracer-role="{role}"'
    return attributes


def encode_document(
    image: Image.Image,
    mask: np.ndarray | None = None,
    **options: Any,
) -> tuple[str, LVCStats]:
    """Encode pixels as a complete standalone exact SVG document."""
    source = image.convert("RGBA")
    width, height = source.size
    fragment, stats = encode_pixels(source, mask, **options)
    svg = (
        f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" data-tracer-mode="absolute_parity" '
        f'data-tracer-codec="lvc">{fragment}</svg>'
    )
    stats.svg_bytes = len(svg.encode("utf-8"))
    return svg, stats
