# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for AI strips whose VFX frames are not equally spaced."""
from __future__ import annotations

from PIL import Image, ImageDraw

from sprite_gen.frames import vfx_frames
from sprite_gen.spec import vfx


def cfg(**overrides):
    return vfx.normalize_vfx({"matte": "source-alpha", **overrides}, {
        "burst": {"frames": 4, "fps": 16, "loop": False}
    })


def uneven_strip() -> Image.Image:
    image = Image.new("RGBA", (320, 64))
    draw = ImageDraw.Draw(image)
    # Same animation sequence, deliberately uneven source spacing. Equal 80px cuts
    # would pass through frame 2 and leave a huge empty part before frame 3.
    centers = [24, 91, 189, 286]
    radii = [7, 10, 16, 11]
    for index, (cx, radius) in enumerate(zip(centers, radii)):
        draw.ellipse((cx-radius, 32-radius, cx+radius, 32+radius), fill=(255, 180, 50, 255))
        # Detached fragment: projection segmentation must keep it in the surrounding
        # source region rather than treating every connected component as a frame.
        draw.rectangle((cx+radius+3, 29+index%2, cx+radius+6, 32+index%2),
                       fill=(255, 230, 120, 180))
    return image


def test_content_aware_default_recovers_uneven_ai_spacing():
    strip = uneven_strip()
    frames, geometry = vfx_frames.split_frames(strip, 4, (64, 64), cfg())
    assert geometry["method"] == "vfx-content-aware"
    assert len(geometry["source_regions"]) == 4
    assert all(frame.getchannel("A").getbbox() for frame in frames)
    # We should not have silently fallen back to naive 80/160/240 cuts.
    cuts = [region[2] for region in geometry["source_regions"][:-1]]
    assert cuts != [80, 160, 240]
    # The deliberately larger third effect stays larger after shared-canvas packing.
    visible_widths = [frame.getchannel("A").getbbox()[2] - frame.getchannel("A").getbbox()[0]
                      for frame in frames]
    assert visible_widths[2] > visible_widths[0]


def test_content_aware_does_not_require_divisible_total_width():
    strip = uneven_strip().crop((0, 0, 317, 64))
    frames, geometry = vfx_frames.split_frames(strip, 4, (64, 64), cfg())
    assert len(frames) == 4
    assert geometry["method"] == "vfx-content-aware"


def test_fixed_slots_remains_available_for_regular_authored_sheet():
    strip = Image.new("RGBA", (128, 32))
    draw = ImageDraw.Draw(strip)
    for i in range(4):
        cx = i * 32 + 16
        draw.ellipse((cx-6, 10, cx+6, 22), fill="white")
    frames, geometry = vfx_frames.split_frames(strip, 4, (64, 64), cfg(layout="fixed-slots"))
    assert len(frames) == 4
    assert geometry["method"] == "vfx-fixed-slots"


def test_content_aware_records_real_source_regions_for_edge_qa():
    strip = uneven_strip()
    _, geometry = vfx_frames.split_frames(strip, 4, (64, 64), cfg())
    counts = vfx_frames.source_edge_counts(strip, geometry, 8)
    assert len(counts) == 4
    # Synthetic frames have real transparent gutters, so recovered cuts should not
    # falsely claim that all middle frames touch their source boundaries.
    assert counts[1] == 0 and counts[2] == 0


def test_prepare_contract_defaults_to_content_aware():
    normalized = cfg()
    assert normalized["layout"] == "content-aware"
    assert "fixed-slots" in vfx.LAYOUTS
