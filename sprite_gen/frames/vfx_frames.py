# SPDX-License-Identifier: Apache-2.0
"""Fixed-space VFX extraction and QA. No connected-component filtering or registration."""
from __future__ import annotations

import argparse
from typing import Any

from sprite_gen._deps import np
from PIL import Image

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
    """Make an AI-generated strip exactly divisible by its declared frame count.

    Image providers own their returned canvas dimensions and may differ by a few
    pixels from the layout guide. Divisibility is therefore not evidence that the
    model followed the visual slots. For VFX we preserve the fixed coordinate
    system by applying one tiny, deterministic horizontal resize to the WHOLE
    matted strip, then keep the actual slot splitter strict.

    The correction is at most half a frame-count pixels when choosing the nearest
    compatible width (for eight frames, <=4 px). Every frame receives the same
    horizontal scale, so expansion, travel and the authored origin remain coherent.
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


def split_frames(strip: Image.Image, count: int, size: tuple[int, int], cfg: dict[str, Any],
                 resample: str | None = None) -> tuple[list[Image.Image], dict[str, Any]]:
    """Cut exact equal slots; fit the SAME entire canvas for every frame.

    Letterboxing is sequence-wide and origin-relative. Source aspect differences
    never cause anisotropic stretching, content normalization or independent crops.
    Generated strips should pass through :func:`normalize_strip_width` first; this
    low-level splitter remains strict so imported/hand-authored geometry cannot be
    silently guessed.
    """
    if count <= 0 or strip.width % count:
        raise ValueError(f"strip width {strip.width} must be divisible by frame count {count}; "
                         "normalize the generated strip before fixed-slot slicing")
    sw, sh = strip.width // count, strip.height
    w, h = size
    if min(sw, sh, w, h) <= 0:
        raise ValueError("source and output frame dimensions must be positive")
    scale = min(w / sw, h / sh)
    rw, rh = max(1, round(sw * scale)), max(1, round(sh * scale))
    ox, oy = cfg["origin"]
    dx, dy = round((w - rw) * ox), round((h - rh) * oy)
    mode = _resample_mode(cfg, resample)
    frames = []
    for index in range(count):
        frame = strip.crop((index * sw, 0, (index + 1) * sw, sh)).convert("RGBA")
        frame = frame.resize((rw, rh), mode)
        if cfg["processing"] == "pixel":
            a = np.array(frame)
            a[:, :, 3] = np.where(a[:, :, 3] >= 128, 255, 0)
            a[a[:, :, 3] == 0] = 0
            frame = Image.fromarray(a)
        cell = Image.new("RGBA", size)
        cell.paste(frame, (dx, dy))  # no mask: preserve straight alpha without squaring coverage
        frames.append(cell)
    return frames, {"source_cell": [sw, sh], "scale": scale, "resized_cell": [rw, rh],
                    "offset": [dx, dy], "origin": list(cfg["origin"]), "method": "vfx-fixed-slots"}


def edge_count(frame: Image.Image, threshold: int, margin: int = 1) -> int:
    mask = np.asarray(frame.getchannel("A")) > threshold
    border = np.zeros_like(mask)
    border[:margin, :] = border[-margin:, :] = True
    border[:, :margin] = border[:, -margin:] = True
    return int(np.count_nonzero(mask & border))


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
