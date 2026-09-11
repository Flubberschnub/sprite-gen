# SPDX-License-Identifier: Apache-2.0
"""Prompt-contract regressions; no provider calls or generated-image quality claims."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from sprite_gen.gen.vfx_prompt import PRESET_GUIDANCE, row_prompt
from sprite_gen.spec import vfx


def request_for(preset="shockwave", matte="chroma", processing="crisp"):
    states = vfx.preset_states(preset)
    return {
        "subject": "effect",
        "vfx": {"preset": preset, "matte": matte, "processing": processing},
        "character": {"id": "library-test", "description": ""},
        "cell": {"width": 256, "height": 128, "safe_margin_x": 24, "safe_margin_y": 12},
        "chroma_key": {"hex": "#FF00FF", "rgb": [255, 0, 255]},
        "states": states,
        "style": vfx.STYLE_DEFAULT,
    }


def render(request, state=None):
    state = state or next(iter(request["states"]))
    return row_prompt(request, state, request["states"][state])


def test_every_preset_has_one_library_role():
    assert set(PRESET_GUIDANCE) == set(vfx.PRESETS)
    assert all(set(g) == {"role", "subject", "exclude"} for g in PRESET_GUIDANCE.values())


@pytest.mark.parametrize("preset", list(vfx.PRESETS))
def test_presets_generate_single_layer_neutral_asset_contract(preset):
    request = request_for(preset)
    before = deepcopy(request)
    prompt = render(request)
    guide = PRESET_GUIDANCE[preset]
    assert f"Asset role: {guide['role']}" in prompt
    assert f"Primary subject: {guide['subject']}" in prompt
    assert f"Preset-specific exclusions: {guide['exclude']}" in prompt
    assert vfx.PRESETS[preset]["action"] in prompt
    assert "One strip = one visual layer and one material behavior" in prompt
    assert "neutral white/grayscale foreground, color-agnostic and tintable" in prompt
    assert "No baked bloom, glow halos" in prompt
    assert "No rocks, rubble, glitter" in prompt
    assert "NOT its color, lighting, environment or secondary layers" in prompt
    assert "are allowed when relevant" not in prompt
    assert "for shape language, palette" not in prompt
    assert "no baked lighting" in request["style"]
    assert request == before  # Rendering never rewrites the caller's saved choices.


@pytest.mark.parametrize("matte,processing", [
    (m, p) for m in vfx.MATTES for p in vfx.PROCESSING
    if (m, p) != ("black-additive", "pixel")
])
def test_library_contract_preserves_matte_and_processing_choices(matte, processing):
    request = request_for(matte=matte, processing=processing)
    prompt = render(request)
    assert "neutral white/grayscale" in prompt
    if matte == "chroma":
        assert "perfectly flat #FF00FF chroma background" in prompt
        assert "Do not desaturate the required key background" in prompt
        assert "Only edge coverage may mix with the key" in prompt
    elif matte == "source-alpha":
        assert "output real RGBA transparency" in prompt
        assert "encode coverage in alpha" in prompt
        assert "perfectly flat #FF00FF chroma background" not in prompt
    else:
        assert "perfectly pure black (#000000)" in prompt
        assert "without a painted glow halo or bloom" in prompt
        assert "output real RGBA transparency" not in prompt
    if processing == "soft":
        assert "preserve natural translucency" in prompt
    elif processing == "pixel":
        assert "hard edges and discrete neutral values" in prompt
    else:
        assert "simple flat shapes" in prompt


def test_key_color_is_not_hardcoded_to_pink():
    request = request_for()
    request["chroma_key"] = {"hex": "#00FF00", "rgb": [0, 255, 0]}
    prompt = render(request)
    assert "perfectly flat #00FF00 chroma background" in prompt
    assert "#FF00FF" not in prompt


def test_sparks_is_an_individual_particle_without_a_baked_shower():
    request = request_for("sparks")
    prompt = render(request)
    assert "single spark streak mask" in prompt
    assert "an individual particle in engine" in prompt
    assert "particle scattering is authored in engine" in request["states"]["sparks"]["action"]
    assert "spark shower, particle swarm" in PRESET_GUIDANCE["sparks"]["exclude"]


def test_default_actions_do_not_request_embers_or_shards():
    for preset in vfx.PRESETS:
        action = vfx.preset_states(preset)[preset]["action"]
        assert "embers" not in action and "shards" not in action
        assert "glitter" not in action and "glow" not in action


def test_dust_is_non_emissive_without_forbidding_its_intrinsic_softness():
    prompt = render(request_for("dust", processing="soft"))
    assert "non-emissive dust density layer" in prompt
    assert "preserve natural translucency" in prompt
    assert "Natural softness belongs to the requested material's density/coverage" in prompt


def test_loop_blank_sparse_origin_and_padding_contracts_survive():
    request = request_for("pulse")
    request["vfx"].update({"origin": [0.25, 0.75], "allow_blank_frames": {"pulse": [0]},
                           "allow_sparse_frames": {"pulse": [11]}})
    prompt = render(request)
    assert "Seamless loop: last-to-first" in prompt
    assert "fixed origin at (0.2500, 0.7500)" in prompt
    assert "exactly 12 frames in one horizontal row" in prompt
    assert "intended frame aspect 256:128" in prompt
    assert "24 horizontal and 12 vertical pixels of padding" in prompt
    assert "Requested blank frame indices (0-based): [0]" in prompt
    assert "Requested sparse frame indices (0-based): [11]" in prompt
    assert "Never recenter or enlarge a small/fading frame" in prompt


def test_custom_motion_and_shape_notes_are_preserved_not_silently_replaced():
    request = request_for()
    request["character"]["description"] = "An elliptical rim viewed obliquely"
    request["style"] = "Graphic segmented rim"
    request["states"]["shockwave"]["action"] = "Hold the narrow ellipse for two frames, then widen it"
    before = deepcopy(request)
    prompt = render(request)
    assert request["character"]["description"] in prompt
    assert request["style"] in prompt
    assert request["states"]["shockwave"]["action"] in prompt
    assert request == before


@pytest.mark.parametrize("preset", list(vfx.PRESETS))
def test_prepare_writes_new_library_prompt_and_matching_action(tmp_path, preset):
    from sprite_gen.gen import prepare
    run = tmp_path / preset
    assert prepare.run(out_dir=run, character_id="test-library", effect_preset=preset) == 0
    request = json.loads((run / "sprite-request.json").read_text(encoding="utf-8"))
    prompt = (run / f"prompts/{preset}.txt").read_text(encoding="utf-8")
    assert request["style"] == vfx.STYLE_DEFAULT
    assert request["states"][preset]["action"] == vfx.PRESETS[preset]["action"]
    assert "One strip = one visual layer" in prompt
    assert request["vfx"]["layout"] == "content-aware"


def test_prepare_character_path_is_unchanged(tmp_path):
    from sprite_gen.gen import prepare
    run = tmp_path / "hero"
    assert prepare.run(out_dir=run, character_id="hero") == 0
    prompt = (run / "prompts/attack.txt").read_text(encoding="utf-8")
    assert "Anchor lock:" in prompt
    assert "One strip = one visual layer" not in prompt
    assert "neutral white/grayscale foreground" not in prompt


def test_prepare_library_run_does_not_rewrite_an_old_runs_prompts(tmp_path):
    from sprite_gen.gen import prepare
    old = tmp_path / "old"
    new = tmp_path / "library"
    assert prepare.run(out_dir=old, character_id="old", effect_preset="shockwave") == 0
    saved = old / "prompts/shockwave.txt"
    saved.write_text("Hand-authored old prompt: keep this unchanged.\n", encoding="utf-8")
    before = saved.read_bytes()
    assert prepare.run(out_dir=new, character_id="library", effect_preset="shockwave") == 0
    assert saved.read_bytes() == before
    assert "One strip = one visual layer" in (new / "prompts/shockwave.txt").read_text(encoding="utf-8")
