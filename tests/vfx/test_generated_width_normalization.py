# SPDX-License-Identifier: Apache-2.0
"""Regression: image providers may return canvases a few pixels off the guide width."""
from __future__ import annotations

import json

from PIL import Image, ImageDraw

from sprite_gen.frames import extract, vfx_frames
from sprite_gen.gen import prepare
from sprite_gen.spec import vfx


def _cfg():
    return vfx.normalize_vfx({}, {"shockwave": {"frames": 8, "fps": 16, "loop": False}})


def test_normalize_generated_strip_to_nearest_frame_multiple():
    source = Image.new("RGBA", (1027, 96), (0, 0, 0, 0))
    normalized, geometry = vfx_frames.normalize_strip_width(source, 8, _cfg())
    assert normalized.width % 8 == 0
    assert abs(normalized.width - source.width) <= 4
    assert geometry["source_width_original"] == 1027
    assert geometry["source_width_normalized"] == normalized.width
    assert geometry["width_normalized"] is True
    frames, _ = vfx_frames.split_frames(normalized, 8, (64, 64), _cfg())
    assert len(frames) == 8


def test_extract_accepts_provider_width_that_is_not_divisible_by_frame_count(tmp_path):
    run = tmp_path / "run"
    request = {
        "subject": "effect",
        "vfx": {"matte": "source-alpha", "processing": "crisp", "origin": [0.5, 0.5]},
        "states": {
            "shockwave": {
                "frames": 8,
                "fps": 16,
                "loop": False,
                "action": "expand from a fixed center and dissipate",
            }
        },
    }
    assert prepare.run(
        out_dir=run,
        character_id="width-regression",
        cell_size=64,
        request_json=json.dumps(request),
    ) == 0

    width, height = 1027, 96
    strip = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(strip)
    for index in range(8):
        cx = round((index + 0.5) * width / 8)
        draw.ellipse((cx - 7, 41, cx + 7, 55), fill=(255, 180, 40, 255))
    strip.save(run / "raw/shockwave.png")

    assert extract.run(run_dir=run) == 0
    manifest = json.loads((run / "frames/frames-manifest.json").read_text(encoding="utf-8"))
    row = manifest["rows"][0]
    assert row["state"] == "shockwave"
    assert row["method"] == "vfx-fixed-slots"
    assert row["vfx"]["width_normalized"] is True
    assert row["vfx"]["source_width_original"] == width
    assert row["vfx"]["source_width_normalized"] % 8 == 0
    assert len(row["files"]) == 8
