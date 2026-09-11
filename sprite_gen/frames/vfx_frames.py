# SPDX-License-Identifier: Apache-2.0
"""VFX source recovery, shared-canvas placement and QA.

AI image generators often draw requested frames at uneven horizontal positions. The
default path therefore reuses sprite-gen's projection/DP segmentation to recover
content-aware source regions before repacking them onto one shared VFX canvas. Strict
fixed-slot slicing remains available for hand-authored or already regular strips.
"""
from __future__ import annotations

import argparse
from typing import Any

from sprite_gen._deps import np
from PIL import Image

from sprite_gen.frames.segment import segment_boundaries
from sprite_gen.spec.vfx import frame_floor


def black_additive(image: Image.Image) -> Image.Image:
    """Unassociate a black-composited emission plate for Blend SrcAlpha One.

    A=max(R,G,B), RGB=emission/A. Thus RGB*A reconstructs the original plate
    (within 8-bit rounding), instead of multiplying its brightness twice.
    This is intentionally NOT a general-purpose black-background cutout.
    """
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float64)
    emission = rgba[:, :, :3] * (rgba[:, :, 3:4] / 255.0)
    alpha = emission.max(axis=2)
    rgb = np.divide(emission * 255.0, alpha[:, :, None],
                    out=np.zeros_like(emission), where=alpha[:, :, None] > 0)
    out = np.dstack((np.rint(rgb), np.rint(alpha))).clip(0, 255).astype(np.uint8)
    out[out[:, :, 3] == 0] = 0
    return Image.fromarray(out)


def matte_source(image: Image.Image, cfg: dict[str, Any], key: tuple[int, int, int],
                 args: argparse.Namespace, *, chroma_mode: str = "rgb", unmix_reach: int = 4,
                 spill_max_fraction: float = 0.005) -> Image.Image:
    if cfg["matte"] == "source-alpha":
        if "A" not in image.getbands() and "transparency" not in image.info:
            raise ValueError("source-alpha requires an image with an alpha channel (or palette transparency)")
        rgba = np.array(image.convert("RGBA"))
        rgba[rgba[:, :, 3] == 0] = 0
        return Image.fromarray(rgba)
    if cfg["matte"] == "black-additive":
        return black_additive(image)
    # Lazy import keeps the geometry/matte helper independent of the stage runner.
    from sprite_gen.frames.extract import remove_chroma_background, remove_chroma_background_ycbcr
    if chroma_mode == "ycbcr":
        return remove_chroma_background_ycbcr(image, key)
    return remove_chroma_background(image, key, args.key_threshold, args.fringe_key_threshold,
                                    args.fringe_delta, unmix_reach=unmix_reach,
                                    spill_max_fraction=spill_max_fraction)


def _resample_mode(cfg: dict[str, Any], resample: str | None) -> Image.Resampling:
    return (Image.Resampling.NEAREST
            if cfg["processing"] == "pixel" or resample == "nearest"
            else Image.Resampling.LANCZOS)


def normalize_strip_width(strip: Image.Image, count: int, cfg: dict[str, Any],
                          resample: str | None = None) -> tuple[Image.Image, dict[str, Any]]:
    """Make a fixed-slot strip exactly divisible by its declared frame count.

    This helper is intentionally for ``vfx.layout=fixed-slots``. Content-aware VFX
    does not need a divisible source width because it recovers its actual regions.
    """
    if count <= 0:
        raise ValueError("frame count must be positive")
    if strip.width < count or strip.height <= 0:
        raise ValueError(
            f"source strip {strip.width}x{strip.height} is too small for {count} frame slots"
        )

    original_width = strip.width
    if original_width % count == 0:
        return strip, {
            "source_width_original": original_width,
            "source_width_normalized": original_width,
            "width_normalized": False,
            "width_delta_pixels": 0,
            "width_scale": 1.0,
            "width_policy": "nearest-frame-multiple",
        }

    # Integer half-up rounding of original_width / count. This picks the nearest
    # legal slot width without Python's tie-to-even round() behavior.
    slot_width = max(1, (2 * original_width + count) // (2 * count))
    normalized_width = slot_width * count
    normalized = strip.resize((normalized_width, strip.height), _resample_mode(cfg, resample))
    return normalized, {
        "source_width_original": original_width,
        "source_width_normalized": normalized_width,
        "width_normalized": True,
        "width_delta_pixels": normalized_width - original_width,
        "width_scale": normalized_width / original_width,
        "width_policy": "nearest-frame-multiple",
    }


def _shared_canvas_frames(crops: list[Image.Image], source_width: int,
                          size: tuple[int, int], cfg: dict[str, Any],
                          resample: str | None = None) -> tuple[list[Image.Image], dict[str, Any]]:
    """Place variable-width recovered regions on one common source canvas.

    No frame is independently normalized to its visible bounds. Each crop keeps its
    original pixels and is padded to a shared width with its configured origin aligned.
    A single scale is then applied to every frame, preserving real growth/travel.
    """
    source_height = crops[0].height if crops else 0
    w, h = size
    if min(source_width, source_height, w, h) <= 0:
        raise ValueError("source and output frame dimensions must be positive")
    if any(crop.height != source_height for crop in crops):
        raise ValueError("VFX source regions must share one source height")
    scale = min(w / source_width, h / source_height)
    rw, rh = max(1, round(source_width * scale)), max(1, round(source_height * scale))
    ox, oy = cfg["origin"]
    dx, dy = round((w - rw) * ox), round((h - rh) * oy)
    mode = _resample_mode(cfg, resample)
    frames: list[Image.Image] = []
    source_offsets: list[int] = []
    for crop in crops:
        source = Image.new("RGBA", (source_width, source_height))
        # Align the same normalized origin for every recovered region without
        # changing the crop's scale. This is padding/repacking, not recentering.
        px = round(source_width * ox - crop.width * ox)
        source_offsets.append(px)
        source.paste(crop.convert("RGBA"), (px, 0))
        frame = source.resize((rw, rh), mode)
        if cfg["processing"] == "pixel":
            a = np.array(frame)
            a[:, :, 3] = np.where(a[:, :, 3] >= 128, 255, 0)
            a[a[:, :, 3] == 0] = 0
            frame = Image.fromarray(a)
        cell = Image.new("RGBA", size)
        cell.paste(frame, (dx, dy))
        frames.append(cell)
    return frames, {
        "source_cell": [source_width, source_height],
        "scale": scale,
        "resized_cell": [rw, rh],
        "offset": [dx, dy],
        "source_offsets": source_offsets,
        "origin": list(cfg["origin"]),
    }


def content_aware_regions(strip: Image.Image, count: int) -> tuple[list[tuple[int, int, int, int]], int]:
    """Recover exactly ``count`` ordered source regions using projection/DP cuts."""
    boundaries, natural = segment_boundaries(strip, count)
    if boundaries is None:
        raise ValueError(
            f"content-aware VFX segmentation could not recover {count} frame regions "
            f"(natural estimate {natural}); regenerate with clearer horizontal separation "
            "or use vfx.layout=fixed-slots for a known regular sheet"
        )
    edges = [0, *boundaries, strip.width]
    regions = [(edges[i], 0, edges[i + 1], strip.height) for i in range(count)]
    return regions, natural


def split_frames(strip: Image.Image, count: int, size: tuple[int, int], cfg: dict[str, Any],
                 resample: str | None = None) -> tuple[list[Image.Image], dict[str, Any]]:
    """Recover VFX frames and repack them onto one shared output canvas.

    ``content-aware`` (default) uses the repository's projection/DP segmentation to
    find low-mass cuts rather than assuming equal source slots. ``fixed-slots`` keeps
    the strict old VFX behavior for already regular sheets.
    """
    if count <= 0:
        raise ValueError("frame count must be positive")
    layout = cfg.get("layout", "content-aware")
    if layout == "content-aware":
        regions, natural = content_aware_regions(strip, count)
        crops = [strip.crop(region).convert("RGBA") for region in regions]
        source_width = max(region[2] - region[0] for region in regions)
        frames, geometry = _shared_canvas_frames(crops, source_width, size, cfg, resample)
        geometry.update({
            "method": "vfx-content-aware",
            "natural_frame_estimate": natural,
            "source_regions": [list(region) for region in regions],
            "source_region_widths": [region[2] - region[0] for region in regions],
        })
        return frames, geometry
    if layout != "fixed-slots":
        raise ValueError(f"unknown VFX layout mode: {layout!r}")
    if strip.width % count:
        raise ValueError(
            f"strip width {strip.width} must be divisible by frame count {count}; "
            "normalize the strip first or use vfx.layout=content-aware"
        )
    sw = strip.width // count
    regions = [(i * sw, 0, (i + 1) * sw, strip.height) for i in range(count)]
    crops = [strip.crop(region).convert("RGBA") for region in regions]
    frames, geometry = _shared_canvas_frames(crops, sw, size, cfg, resample)
    geometry.update({
        "method": "vfx-fixed-slots",
        "source_regions": [list(region) for region in regions],
        "source_region_widths": [sw] * count,
    })
    return frames, geometry


def edge_count(frame: Image.Image, threshold: int, margin: int = 1) -> int:
    mask = np.asarray(frame.getchannel("A")) > threshold
    border = np.zeros_like(mask)
    border[:margin, :] = border[-margin:, :] = True
    border[:, :margin] = border[:, -margin:] = True
    return int(np.count_nonzero(mask & border))


def source_edge_counts(strip: Image.Image, geometry: dict[str, Any], threshold: int) -> list[int]:
    """Edge contact on the actual recovered source regions, before repacking."""
    regions = geometry.get("source_regions") or []
    return [edge_count(strip.crop(tuple(region)).convert("RGBA"), threshold) for region in regions]


def inspect_frames(frames: list[Image.Image], state: str, cfg: dict[str, Any], floor: int,
                   key: tuple[int, int, int] | None = None, args: argparse.Namespace | None = None
                   ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    errors, warnings, records = [], [], []
    for index, frame in enumerate(frames):
        count = int(np.count_nonzero(np.asarray(frame.getchannel("A"))))
        edge = edge_count(frame, cfg["edge_alpha"])
        bbox = frame.getchannel("A").getbbox()
        record = {"index": index, "nontransparent_pixels": count, "bbox": list(bbox) if bbox else None,
                  "edge_pixels": edge, "chroma_adjacent_pixels": 0}
        records.append(record)
        minimum = frame_floor(cfg, state, index, floor)
        if count < minimum:
            errors.append(f"frame {index:02d} is empty or too sparse ({count} < {minimum}); "
                          "declare this frame in vfx.allow_blank_frames or vfx.allow_sparse_frames only if intentional")
        if edge:
            (errors if cfg["edge_policy"] == "error" else warnings).append(
                f"frame {index:02d} has {edge} edge-contact pixels; increase source padding or explicitly set vfx.edge_policy=warn")
        if cfg["matte"] == "chroma" and key is not None and args is not None:
            from sprite_gen.frames.extract import chroma_adjacent_count
            adjacent = chroma_adjacent_count(frame, key, args.chroma_adjacent_threshold)
            record["chroma_adjacent_pixels"] = adjacent
            if adjacent > args.chroma_adjacent_pixel_threshold:
                errors.append(f"frame {index:02d} has {adjacent} chroma-adjacent pixels")
    if not any(r["nontransparent_pixels"] for r in records):
        errors.append("the entire VFX sequence is empty; blank-frame allowances do not authorize an empty effect")
    return errors, warnings, records
