# SPDX-License-Identifier: Apache-2.0
"""Export curated VFX into uniform, non-deduplicated flipbook grids.

The existing composer owns selection, clones, pixel edits and transforms. This
exporter consumes its frame_layout rectangles in playback order; repeated holds
must occupy repeated cells for engines that advance a regular texture grid.
"""
from __future__ import annotations

import argparse
import io
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from sprite_gen.compose import compose_atlas
from sprite_gen.frames.extract import heal_run, load_failure_evidence
from sprite_gen.spec.runio import (acquire_run_dir_lock, atomic_write_set, load_request,
                                   release_run_dir_lock)
from sprite_gen.spec.vfx import config


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--state", help="one state; omit to export all states")
    parser.add_argument("--out-dir", type=Path, help="default: <run-dir>/flipbooks")
    parser.add_argument("--columns", type=int, default=None,
                        help="fixed grid columns; default is a near-square exact factor of the frame count")
    parser.add_argument("--padding", type=int, default=0,
                        help="transparent border added uniformly to each complete frame canvas")
    parser.add_argument("--append-blank", action="store_true",
                        help="append one transparent terminal frame (one-shots only)")
    parser.add_argument("--grayscale", action="store_true", help="bake tintable grayscale RGB, preserving alpha")
    parser.add_argument("--max-size", type=int, default=8192, help="maximum width/height of the exported texture")


def png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def grid_columns(count: int) -> int:
    # Exact factors avoid unused cells by default; primes become a strip.
    return next(c for c in range(math.ceil(math.sqrt(count)), count + 1) if count % c == 0)


def pack_frames(frames: list[Image.Image], *, columns: int | None, padding: int,
                max_size: int) -> tuple[Image.Image, dict[str, Any]]:
    if not frames:
        raise ValueError("cannot export an empty selection")
    if padding < 0 or max_size <= 0 or (columns is not None and columns <= 0):
        raise ValueError("padding must be nonnegative; columns and max-size must be positive")
    w, h = frames[0].size
    if any(f.size != (w, h) for f in frames):
        raise ValueError("flipbook frames must share one canvas size")
    columns = columns or grid_columns(len(frames))
    rows = math.ceil(len(frames) / columns)
    tw, th = w + 2 * padding, h + 2 * padding
    if max(columns * tw, rows * th) > max_size:
        raise ValueError(f"flipbook would exceed --max-size {max_size}; reduce cell size, padding or frame count")
    sheet = Image.new("RGBA", (columns * tw, rows * th))
    rectangles = []
    for index, frame in enumerate(frames):
        x, y = (index % columns) * tw, (index // columns) * th
        sheet.paste(frame, (x + padding, y + padding))
        rectangles.append({"x": x, "y": y, "w": tw, "h": th})
    return sheet, {"columns": columns, "rows": rows, "cell_width": tw, "cell_height": th,
                   "capacity": columns * rows, "unused_cells": columns * rows - len(frames),
                   "order": "left-to-right, top-to-bottom", "frame_rects": rectangles}


def preview(sheet: Image.Image, background: tuple[int, int, int], additive: bool) -> Image.Image:
    if not additive:
        base = Image.new("RGBA", sheet.size, (*background, 255))
        return Image.alpha_composite(base, sheet).convert("RGB")
    from sprite_gen._deps import np
    rgba = np.asarray(sheet, dtype=np.float64)
    out = np.asarray(background) + rgba[:, :, :3] * (rgba[:, :, 3:4] / 255.0)
    return Image.fromarray(np.rint(out).clip(0, 255).astype(np.uint8))


def run(*, run_dir: Path, state: str | None = None, out_dir: Path | None = None,
        columns: int | None = None, padding: int = 0, append_blank: bool = False,
        grayscale: bool = False, max_size: int = 8192) -> int:
    run_dir = Path(run_dir).expanduser().resolve()
    request = load_request(run_dir)
    cfg = config(request)
    if cfg is None:
        raise SystemExit("export-flipbook requires a VFX run; prepare --subject effect or add a documented vfx block")
    states = [state] if state is not None else list(request["states"])
    if any(s not in request["states"] for s in states):
        raise SystemExit(f"unknown VFX state: {state}")
    if padding < 0 or max_size <= 0 or (columns is not None and columns <= 0):
        raise SystemExit("padding must be nonnegative; columns and max-size must be positive")
    if append_blank and any(request["states"][s].get("loop", False) for s in states):
        raise SystemExit("--append-blank is only valid for one-shot states, not loops")
    out_dir = Path(out_dir).expanduser().resolve() if out_dir else run_dir / "flipbooks"
    try:
        # Compose may heal raw frames before taking its writer lock. Do not hold
        # a parent-process lock while a heal subprocess needs to acquire it.
        heal_run(run_dir)
        failure = load_failure_evidence(run_dir / "extract-failure.json")
        if failure.get("errors"):
            raise ValueError("the latest extraction has unresolved failures; fix and re-extract before exporting")
        if compose_atlas.run(run_dir=run_dir) != 0:
            return 1
        acquire_run_dir_lock(run_dir, "export_flipbook")
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        with Image.open(run_dir / manifest["game_input"]) as im:
            atlas = im.convert("RGBA")
        payloads: dict[Path, bytes | str] = {}
        exported = []
        for name in states:
            animation = manifest["animation"]["rows"][name]
            rectangles = manifest["frame_layout"]["rows"][name]
            frames = [atlas.crop((r["x"], r["y"], r["x"]+r["w"], r["y"]+r["h"])) for r in rectangles]
            if not frames or not any(f.getchannel("A").getbbox() for f in frames):
                raise ValueError(f"{name}: selected effect is entirely empty")
            if grayscale:
                for frame in frames:
                    luminance = ImageOps.grayscale(frame.convert("RGB"))
                    frame.paste(Image.merge("RGBA", (luminance, luminance, luminance, frame.getchannel("A"))))
            if append_blank:
                frames.append(Image.new("RGBA", frames[0].size))
            sheet, grid = pack_frames(frames, columns=columns, padding=padding, max_size=max_size)
            w, h = frames[0].size
            ox, oy = cfg["origin"]
            origin = [(ox*w + padding)/grid["cell_width"], (oy*h + padding)/grid["cell_height"]]
            fps = animation["fps"]
            additive = cfg["matte"] == "black-additive"
            metadata = {
                "version": 1, "kind": "sprite-gen-flipbook", "state": name,
                "texture": f"{name}.png", "width": sheet.width, "height": sheet.height,
                "grid": grid, "frame_count": len(frames), "fps": fps,
                "duration_seconds": len(frames)/fps, "frame_duration_ms": 1000.0/fps,
                "loop": animation["loop"], "append_blank": append_blank, "grayscale": grayscale,
                "origin": {"space": "normalized, top-left", "value": origin},
                "unity_pivot": [origin[0], 1-origin[1]],
                "alpha_representation": "straight",
                "blend": {"mode": "additive" if additive else "alpha", "src": "SrcAlpha",
                          "dst": "One" if additive else "OneMinusSrcAlpha"},
                "source_matte": cfg["matte"], "processing": cfg["processing"],
                "frame_variant": animation["frame_variant"],
                "valid_frame_indices": [0, len(frames)-1],
                "curation_applied": manifest["curation_applied"],
                "warnings": (["Unused grid cells are transparent. Limit playback to frame_count; do not animate all cells."]
                             if grid["unused_cells"] else []),
            }
            payloads[out_dir / f"{name}.png"] = png_bytes(sheet)
            payloads[out_dir / f"{name}.json"] = json.dumps(metadata, indent=2) + "\n"
            payloads[out_dir / f"{name}.preview-dark.png"] = png_bytes(preview(sheet, (24, 24, 32), additive))
            payloads[out_dir / f"{name}.preview-light.png"] = png_bytes(preview(sheet, (220, 220, 220), additive))
            exported.append({"state": name, "texture": f"{name}.png", "metadata": f"{name}.json"})
        payloads[out_dir / "export.report.json"] = json.dumps({"ok": True, "exports": exported}, indent=2) + "\n"
        out_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_set(payloads)  # stage the entire batch before publishing any deliverable
    except (OSError, ValueError) as exc:
        raise SystemExit(f"export-flipbook: {exc}") from exc
    finally:
        release_run_dir_lock(run_dir)
    print(json.dumps({"ok": True, "out_dir": str(out_dir), "exports": exported}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
