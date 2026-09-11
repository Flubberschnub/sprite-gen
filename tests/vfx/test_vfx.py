# SPDX-License-Identifier: Apache-2.0
"""Synthetic VFX acceptance tests. No model calls, credentials or external assets."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from conftest import help_options, run_script
from sprite_gen.gen import prepare, gen_set
from sprite_gen.compose import compose_atlas, export_flipbook
from sprite_gen.frames import extract, vfx_frames
from sprite_gen.spec import vfx
from sprite_gen.spec.runio import release_run_dir_lock
from sprite_gen.curate.curation import stamp_curation
from sprite_gen.qa.inspect import inspect_run
from sprite_gen.qa.score import score_inspection

SIZE = 64


def cfg(**overrides):
    return vfx.normalize_vfx(overrides, {"burst": {"frames": 4, "fps": 16, "loop": False}})


def source_frames(count=4):
    frames = []
    for i in range(count):
        im = Image.new("RGBA", (SIZE, SIZE))
        d = ImageDraw.Draw(im)
        r = 5 + i * 4
        d.ellipse((32-r, 32-r, 32+r, 32+r), fill=(220, 140, 30, 255))
        frames.append(im)
    return frames


def make_run(path: Path, frames=None, **options):
    frames = frames or source_frames()
    n = len(frames)
    request = {"subject": "effect", "vfx": {"matte": "source-alpha", "layout": "fixed-slots", **options},
               "states": {"burst": {"frames": n, "fps": 16, "loop": False, "action": "expand and dissipate"}}}
    prepare.run(out_dir=path, character_id="test-vfx", cell_size=SIZE,
                request_json=json.dumps(request))
    raw = Image.new("RGBA", (frames[0].width * n, frames[0].height))
    for i, frame in enumerate(frames):
        raw.paste(frame, (i * frame.width, 0))
    raw.save(path / "raw/burst.png")
    return path


def frame_images(run):
    doc = json.loads((run / "frames/frames-manifest.json").read_text())
    return [Image.open(run / f).convert("RGBA") for f in doc["rows"][0]["files"]]


def read(path):
    return json.loads(path.read_text())


@pytest.mark.parametrize("preset", list(vfx.PRESETS))
def test_presets_prepare_effects_not_characters(tmp_path, preset):
    run = tmp_path / preset
    assert prepare.run(out_dir=run, character_id=preset, effect_preset=preset) == 0
    request = read(run / "sprite-request.json")
    assert request["subject"] == "effect"
    assert request["vfx"]["origin"] == vfx.PRESETS[preset]["origin"]
    assert list(request["states"]) == [preset]
    prompt = (run / f"prompts/{preset}.txt").read_text()
    assert "fixed origin" in prompt and "Detached sparks" in prompt
    assert "full-body" not in prompt and "Anchor lock:" not in prompt
    assert "Do not draw detached effects" not in prompt
    assert "body proportions" not in request["style"]


def test_character_prepare_stays_legacy(tmp_path):
    prepare.run(out_dir=tmp_path / "character", character_id="hero")
    request = read(tmp_path / "character/sprite-request.json")
    assert "vfx" not in request
    assert set(request["states"]) == set(prepare.DEFAULT_STATES)
    assert "Anchor lock:" in (tmp_path / "character/prompts/attack.txt").read_text()


@pytest.mark.parametrize("bad", [
    {"origin": [float("nan"), 0.5]}, {"origin": [2, 0]}, {"origin": [True, 0.5]},
    {"matte": "magic"}, {"processing": "unknown"}, {"version": 2},
    {"edge_policy": "ignore"}, {"edge_alpha": 255}, {"edge_alpha": True},
    {"allow_blank_frames": {"no-state": [0]}}, {"allow_blank_frames": {"burst": [4]}},
    {"allow_sparse_frames": {"burst": [-1]}}, {"allow_blank_frames": {"burst": [True]}},
    {"allow_blank_frames": []}, {"typo": 1},
    {"matte": "black-additive", "processing": "pixel"},
])
def test_invalid_vfx_policies_are_rejected(bad):
    with pytest.raises(SystemExit):
        cfg(**bad)


def test_prepare_validates_before_creating_output(tmp_path):
    run = tmp_path / "bad"
    with pytest.raises(SystemExit, match="character fit"):
        prepare.run(out_dir=run, character_id="bad", subject="effect", fit_pixel_unfake=True)
    assert not run.exists()
    with pytest.raises(SystemExit, match="subject: character"):
        prepare.run(out_dir=run, character_id="bad", subject="character", effect_preset="dust")
    assert not run.exists()


@pytest.mark.parametrize("entry", [{"frames": 2, "fps": 0}, {"frames": 2.5},
                                   {"frames": 2, "loop": "false"}])
def test_invalid_numeric_states_do_not_get_coerced(tmp_path, entry):
    with pytest.raises(SystemExit):
        prepare.run(out_dir=tmp_path / "bad", character_id="bad", subject="effect",
                    request_json=json.dumps({"states": {"bad": entry}}))
    assert not (tmp_path / "bad").exists()


def test_fixed_space_preserves_expansion_and_detached_fragments(tmp_path):
    originals = source_frames()
    for i, im in enumerate(originals):
        im.putpixel((5+i, 8), (255, 255, 255, 180))
    run = make_run(tmp_path / "run", originals)
    assert extract.run(run_dir=run) == 0
    actual = frame_images(run)
    assert all(np.array_equal(np.asarray(a), np.asarray(b)) for a, b in zip(actual, originals))
    assert read(run / "frames/frames-manifest.json")["rows"][0]["method"] == "vfx-fixed-slots"
    areas = [np.count_nonzero(np.asarray(f)[:, :, 3]) for f in actual]
    assert areas == sorted(areas)


def test_directional_motion_is_not_recentred(tmp_path):
    frames = []
    for x in (12, 24, 36, 48):
        frame = Image.new("RGBA", (64, 64))
        ImageDraw.Draw(frame).rectangle((x-4, 25, x+4, 35), fill=(200, 200, 200, 255))
        frames.append(frame)
    run = make_run(tmp_path / "run", frames, origin=[0.2, 0.5])
    assert extract.run(run_dir=run) == 0
    assert [f.getbbox() for f in frame_images(run)] == [f.getbbox() for f in frames]


def test_letterboxing_uses_shared_scale_and_origin_not_visible_bounds():
    strip = Image.new("RGBA", (128*4, 64))
    d = ImageDraw.Draw(strip)
    for i in range(4):
        d.rectangle((128*i+20, 10, 128*i+40+i*5, 30), fill="white")
    frames, geometry = vfx_frames.split_frames(strip, 4, (64, 64), cfg(origin=[0.25, 0.75], layout="fixed-slots"), "nearest")
    assert geometry["scale"] == 0.5
    assert geometry["offset"] == [0, 24]
    assert {f.getbbox()[1] for f in frames} == {29}  # 10 * 0.5 + 24, without content-dependent placement
    assert len({f.getbbox()[2] for f in frames}) == 4


def test_width_must_divide_exactly_into_slots():
    with pytest.raises(ValueError, match="divisible"):
        vfx_frames.split_frames(Image.new("RGBA", (255, 64)), 4, (64, 64), cfg(layout="fixed-slots"))


def test_declared_blank_and_sparse_frames_are_preserved(tmp_path):
    frames = [Image.new("RGBA", (64, 64)), source_frames()[0], Image.new("RGBA", (64, 64)),
              Image.new("RGBA", (64, 64))]
    frames[2].putpixel((32, 32), (255, 120, 0, 20))
    run = make_run(tmp_path / "run", frames, allow_blank_frames={"burst": [0, 3]},
                   allow_sparse_frames={"burst": [2]})
    assert extract.run(run_dir=run) == 0
    assert len(frame_images(run)) == 4
    assert frame_images(run)[2].getpixel((32, 32))[3] == 20
    assert export_flipbook.run(run_dir=run, columns=2) == 0
    meta = read(run / "flipbooks/burst.json")
    assert meta["frame_count"] == 4 and meta["loop"] is False
    assert Image.open(run / "flipbooks/burst.png").getpixel((10, 10))[3] == 0


def test_sparse_allowance_does_not_authorize_blank_and_failure_keeps_old_frames(tmp_path):
    frames = source_frames()
    run = make_run(tmp_path / "run", frames, allow_sparse_frames={"burst": [3]})
    assert extract.run(run_dir=run) == 0
    old = (run / "frames/frames-manifest.json").read_bytes()
    raw = Image.open(run / "raw/burst.png").convert("RGBA")
    raw.paste(Image.new("RGBA", (64, 64)), (192, 0))
    raw.save(run / "raw/burst.png")
    assert extract.run(run_dir=run) == 1
    assert (run / "frames/frames-manifest.json").read_bytes() == old
    assert "too sparse" in (run / "extract-failure.json").read_text()


def test_wholly_blank_sequence_fails_even_with_all_blanks_allowed(tmp_path):
    run = make_run(tmp_path / "run", [Image.new("RGBA", (64, 64)) for _ in range(4)],
                   allow_blank_frames={"burst": [0, 1, 2, 3]})
    assert extract.run(run_dir=run) == 1
    assert not (run / "frames/frames-manifest.json").exists()


def test_source_clipping_cannot_be_hidden_by_letterbox(tmp_path):
    frames = [Image.new("RGBA", (128, 64)) for _ in range(4)]
    for f in frames:
        ImageDraw.Draw(f).rectangle((10, 0, 70, 20), fill="white")
    run = make_run(tmp_path / "run", frames)
    assert extract.run(run_dir=run) == 1
    assert "source frame" in (run / "extract-failure.json").read_text()
    request = read(run / "sprite-request.json")
    request["vfx"]["edge_policy"] = "warn"
    (run / "sprite-request.json").write_text(json.dumps(request))
    assert extract.run(run_dir=run) == 0
    assert read(run / "frames/frames-manifest.json")["warnings"]


def test_source_alpha_preserves_soft_coverage_and_dark_material(tmp_path):
    frames = source_frames()
    for frame in frames:
        frame.putpixel((15, 15), (0, 0, 0, 73))
        frame.putpixel((14, 15), (100, 200, 255, 22))
        frame.putpixel((1, 1), (255, 0, 255, 0))
    run = make_run(tmp_path / "run", frames, processing="soft")
    assert extract.run(run_dir=run) == 0
    image = frame_images(run)[0]
    assert image.getpixel((15, 15)) == (0, 0, 0, 73)
    assert image.getpixel((14, 15)) == (100, 200, 255, 22)
    assert image.getpixel((1, 1)) == (0, 0, 0, 0)


def test_source_alpha_rejects_an_rgb_plate(tmp_path):
    run = make_run(tmp_path / "run")
    with Image.open(run / "raw/burst.png") as im:
        rgb = im.convert("RGB")
    rgb.save(run / "raw/burst.png")
    assert extract.run(run_dir=run) == 1
    assert "alpha channel" in (run / "extract-failure.json").read_text()


def test_additive_plate_reconstructs_emission_without_double_dimming():
    plate = np.zeros((3, 4, 4), dtype=np.uint8)
    plate[:, :, 3] = 255
    plate[1, 1] = (35, 80, 160, 255)
    plate[1, 2] = (25, 10, 0, 128)
    actual = np.asarray(vfx_frames.black_additive(Image.fromarray(plate)), dtype=float)
    recon = actual[:, :, :3] * actual[:, :, 3:4] / 255
    expected = plate[:, :, :3].astype(float) * plate[:, :, 3:4] / 255
    assert np.abs(recon - expected).max() <= 1.0
    assert (actual[0, 0] == 0).all()


def test_black_additive_end_to_end(tmp_path):
    frames = source_frames()
    for frame in frames:
        frame.putpixel((15, 15), (40, 80, 120, 255))
    run = make_run(tmp_path / "run", frames, matte="black-additive")
    assert extract.run(run_dir=run) == 0
    assert export_flipbook.run(run_dir=run) == 0
    assert read(run / "flipbooks/burst.json")["blend"] == {"mode": "additive", "src": "SrcAlpha", "dst": "One"}
    assert (run / "flipbooks/burst.preview-dark.png").is_file()


def test_pixel_mode_is_explicit_and_plain_toggle_keeps_original_alpha(tmp_path):
    frames = source_frames()
    for f in frames:
        f.putpixel((15, 15), (180, 120, 30, 73))
        f.putpixel((16, 15), (180, 120, 30, 181))
    run = make_run(tmp_path / "run", frames, processing="pixel")
    assert extract.run(run_dir=run) == 0
    assert set(np.unique(np.asarray(frame_images(run)[0])[:, :, 3])) <= {0, 255}
    plain = Image.open(run / "frames/burst/frame-0.plain.png")
    assert plain.getpixel((15, 15))[3] == 73
    cur = stamp_curation(run, {"version": 1, "kind": "sprite-gen-curation", "pixel_unfake": False})
    (run / "curation.json").write_text(json.dumps(cur))
    assert export_flipbook.run(run_dir=run) == 0
    assert read(run / "flipbooks/burst.json")["frame_variant"] == "plain"


def test_export_repeats_curated_holds_instead_of_deduplicating_cells(tmp_path):
    run = make_run(tmp_path / "run")
    assert extract.run(run_dir=run) == 0
    cur = stamp_curation(run, {"version": 1, "kind": "sprite-gen-curation", "states": {
        "burst": {"selected": [2, 0, 4, 1], "clones": {"4": 2}, "transforms": {"0": {"dx": 2}}}}})
    (run / "curation.json").write_text(json.dumps(cur))
    assert export_flipbook.run(run_dir=run, columns=2, padding=2) == 0
    meta = read(run / "flipbooks/burst.json")
    assert meta["frame_count"] == 4 and meta["curation_applied"]
    image = Image.open(run / "flipbooks/burst.png")
    assert image.crop((0, 0, 68, 68)).tobytes() == image.crop((0, 68, 68, 136)).tobytes()
    assert len(set((r["x"], r["y"]) for r in meta["grid"]["frame_rects"])) == 4


def test_export_metadata_origin_timing_unused_cells_and_grayscale(tmp_path):
    run = make_run(tmp_path / "run", origin=[0.25, 0.75])
    assert extract.run(run_dir=run) == 0
    assert export_flipbook.run(run_dir=run, columns=3, padding=2, append_blank=True, grayscale=True) == 0
    meta = read(run / "flipbooks/burst.json")
    assert meta["frame_count"] == 5 and meta["grid"]["unused_cells"] == 1
    assert meta["duration_seconds"] == 5/16
    assert meta["origin"]["value"] == [18/68, 50/68]
    assert meta["unity_pivot"] == [18/68, 1-50/68]
    assert meta["warnings"]
    a = np.asarray(Image.open(run / "flipbooks/burst.png"))
    assert np.array_equal(a[:, :, 0], a[:, :, 1]) and np.array_equal(a[:, :, 1], a[:, :, 2])


@pytest.mark.parametrize("kwargs", [{"columns": 0}, {"padding": -1}, {"max_size": 1}, {"state": "absent"}])
def test_invalid_export_does_not_publish_outputs(tmp_path, kwargs):
    run = make_run(tmp_path / "run")
    assert extract.run(run_dir=run) == 0
    with pytest.raises(SystemExit):
        export_flipbook.run(run_dir=run, **kwargs)
    assert not (run / "flipbooks").exists()


def test_append_blank_cannot_change_a_loop(tmp_path):
    run = make_run(tmp_path / "run")
    request = read(run / "sprite-request.json")
    request["states"]["burst"]["loop"] = True
    (run / "sprite-request.json").write_text(json.dumps(request))
    with pytest.raises(SystemExit, match="one-shot"):
        export_flipbook.run(run_dir=run, append_blank=True)


def test_vfx_policy_change_heals_recipe_and_keeps_origin_explicit(tmp_path):
    run = make_run(tmp_path / "run")
    assert extract.run(run_dir=run) == 0
    original = read(run / "frames/frames-manifest.json")["rows"][0]["vfx_fingerprint"]
    request = read(run / "sprite-request.json")
    request["vfx"]["origin"] = [0.2, 0.7]
    (run / "sprite-request.json").write_text(json.dumps(request))
    assert extract.heal_run(run)["healed"] == ["burst"]
    row = read(run / "frames/frames-manifest.json")["rows"][0]
    assert row["vfx_fingerprint"] != original and row["vfx"]["origin"] == [0.2, 0.7]


def test_qa_does_not_penalize_changing_effect_silhouettes(tmp_path):
    run = make_run(tmp_path / "run")
    assert extract.run(run_dir=run) == 0
    report = inspect_run(run, dhash_min=1.0, histogram_min=1.0)
    assert report["ok"] and report["rows"][0]["vfx"]
    assert not any("silhouette similarity" in w or "identity similarity" in w for w in report["warnings"])
    scored = score_inspection(report)
    assert not any("body proportions" in h or "outfit" in h for h in scored["hints"])


def test_vfx_generation_can_use_text_and_layout_without_character_anchor(tmp_path):
    run = make_run(tmp_path / "run")
    calls = []
    def fake(prompt_file, out, refs, report, **kwargs):
        calls.append(refs)
        Image.new("RGBA", (256, 64)).save(out)
        report.write_text(json.dumps({"provider": "codex"}))
        return 0
    result = gen_set.run_item(run_dir=run, request=read(run / "sprite-request.json"), state="burst",
                              provider="codex", model=None, force=True, gen_runner=fake)
    assert result["ok"] and len(calls[0]) == 1
    assert calls[0][0].name == "burst.png"


def test_cli_and_module_export_argument_surfaces_match():
    assert help_options("-m", "sprite_gen.cli", "export-flipbook") == help_options("-m", "sprite_gen.compose.export_flipbook")


def test_pipeline_commands_work_as_subprocesses(tmp_path):
    run = make_run(tmp_path / "run")
    for script in ("extract_sprite_row_frames.py", "export_flipbook.py"):
        result = run_script(script, "--run-dir", str(run))
        assert result.returncode == 0, result.stdout + result.stderr
    assert (run / "flipbooks/burst.png").is_file()


def test_chroma_mode_reuses_keying_without_component_filtering(tmp_path):
    frames = source_frames()
    run = make_run(tmp_path / "run", frames, matte="chroma")
    key = tuple(read(run / "sprite-request.json")["chroma_key"]["rgb"])
    with Image.open(run / "raw/burst.png") as raw:
        background = Image.new("RGBA", raw.size, (*key, 255))
        background.alpha_composite(raw.convert("RGBA"))
        plate = background.convert("RGB")
    plate.save(run / "raw/burst.png")
    assert extract.run(run_dir=run) == 0
    assert all(np.array_equal(np.asarray(a), np.asarray(b)) for a, b in zip(frame_images(run), frames))


def test_export_refuses_unresolved_failed_extraction(tmp_path):
    run = make_run(tmp_path / "run")
    assert extract.run(run_dir=run) == 0
    Image.new("RGBA", (256, 64)).save(run / "raw/burst.png")
    assert extract.run(run_dir=run) == 1
    with pytest.raises(SystemExit, match="unresolved failures"):
        export_flipbook.run(run_dir=run)
    assert not (run / "flipbooks").exists()


def test_inspect_reports_invalid_vfx_raw_without_character_hints(tmp_path):
    run = make_run(tmp_path / "run")
    Image.new("RGBA", (255, 64)).save(run / "raw/burst.png")
    report = inspect_run(run)
    assert not report["ok"] and report["rows"][0]["vfx"]
    assert "divisible" in report["errors"][0]
    assert not any("full-body" in h for h in score_inspection(report)["hints"])


def test_curated_empty_selection_cannot_be_composed_as_a_valid_effect(tmp_path):
    run = make_run(tmp_path / "run")
    assert extract.run(run_dir=run) == 0
    cur = stamp_curation(run, {"version": 1, "kind": "sprite-gen-curation",
                               "states": {"burst": {"deleted": [0, 1, 2, 3]}}})
    (run / "curation.json").write_text(json.dumps(cur))
    try:
        assert compose_atlas.run(run_dir=run) == 1
    finally:
        release_run_dir_lock(run)
    assert not (run / "manifest.json").exists()
