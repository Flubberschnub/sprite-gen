# SPDX-License-Identifier: Apache-2.0
"""Prompts for tintable, single-layer VFX authoring ingredients, not finished effects."""
from __future__ import annotations

from typing import Any
from sprite_gen.spec.vfx import config


# Roles describe texture contents, not additional compositing layers or runtime settings.
# Keep one specific subject per preset; 'ring or orb' invites composite interpretations.
PRESET_GUIDANCE: dict[str, dict[str, str]] = {
    "burst": {
        "role": "burst core mask",
        "subject": "one simple radial shape with a few broad expanding lobes",
        "exclude": "fire, smoke, embers, detached shards, sparks, shockwave rings",
    },
    "hit": {
        "role": "impact flash mask",
        "subject": "one compact solid impact-star silhouette with a few tapered points",
        "exclude": "victim silhouettes, sparks, debris, smoke, rings, secondary flashes",
    },
    "shockwave": {
        "role": "expanding ring mask",
        "subject": "one flat annular ring with an empty center and a clean, thinning rim",
        "exclude": "central flash, concentric secondary rings, rocks, dust, smoke, sparks, craters",
    },
    "dust": {
        "role": "non-emissive dust density layer",
        "subject": "one neutral dust puff with a few broad soft lobes and restrained erosion",
        "exclude": "rocks, grit particles, sparks, fire, glow, lit cloud rendering, ground interaction",
    },
    "slash": {
        "role": "crescent trail mask",
        "subject": "one tapered crescent ribbon with a clean leading edge and eroding tail",
        "exclude": "weapon, impact flash, sparks, detached shards, smoke, secondary arcs",
    },
    "sparks": {
        "role": "single spark streak mask",
        "subject": "one short tapered streak for emission as an individual particle in engine",
        "exclude": "spark shower, particle swarm, impact core, smoke, embers, star-shaped glitter",
    },
    "movement": {
        "role": "directional whoosh mask",
        "subject": "one simple tapered directional ribbon with clear flow",
        "exclude": "speed-line screen overlay, impact flash, particles, smoke, secondary trails",
    },
    "pulse": {
        "role": "looping ring mask",
        "subject": "one simple ring breathing through subtle radius and thickness changes",
        "exclude": "central orb, multiple rings, aura layers, glyphs, runes, decorative particles",
    },
}

LIBRARY_CONTRACT = """Purpose: a reusable real-time game VFX authoring ingredient, NOT a complete effect or showcase illustration.
One strip = one visual layer and one material behavior. Do not combine a flash, ring, smoke and particles.
Color: neutral white/grayscale foreground, color-agnostic and tintable in engine; no baked hue, rainbow or temperature gradient.
Use white for the main mask and grayscale only for intentional intensity/density variation, not surface lighting.
The chroma background is exempt from the foreground palette rule. Do not desaturate the required key background.
Keep low spatial detail, broad readable forms and clean negative space; avoid micro-noise and painterly texture.
No baked bloom, glow halos, lens flare, light rays, lighting spill, rim lighting, cast shadows or reflections.
No rocks, rubble, glitter, confetti, ornamental runes, scene context or unrelated secondary particles.
No character, anatomy, face, clothing, idle pose, scenery, ground plane, captions, borders or UI.
Color, emission strength, bloom, particle scattering, lighting and secondary layers will be authored separately in engine.
Natural softness belongs to the requested material's density/coverage, not to a separate glow layer."""


def row_prompt(request: dict[str, Any], state: str, entry: dict[str, Any]) -> str:
    cfg = config(request)
    assert cfg is not None
    guide = PRESET_GUIDANCE[cfg["preset"]]
    cell = request["cell"]
    w, h = cell["width"], cell["height"]
    n = entry["frames"]
    x, y = cfg["origin"]
    key = request["chroma_key"]
    background = {
        "chroma": (f"Background: use a perfectly flat {key['hex']} chroma background. "
                   "The foreground is neutral white/grayscale before compositing onto this key. "
                   "Only edge coverage may mix with the key; do not tint the effect with it. "
                   "No background gradient, vignette, checkerboard or painted transparency."),
        "source-alpha": ("Background: output real RGBA transparency, not a checkerboard or opaque white, "
                         "gray or colored background. Use neutral foreground RGB; encode coverage in alpha."),
        "black-additive": ("Background: neutral white/grayscale intensity on perfectly pure black (#000000). "
                           "Black is zero emission. Render only the primary element's intensity, "
                           "without a painted glow halo or bloom. Emission strength and bloom are added in engine."),
    }[cfg["matte"]]
    processing = {
        "crisp": ("Crisp graphic mask: simple flat shapes, clean deliberate edges, minimal interior detail; "
                  "edge antialiasing is allowed. No glossy shading or bevels."),
        "soft": ("Soft density layer: broad smooth coverage and restrained edge erosion; preserve natural "
                 "translucency without a separate glow halo, imposed outline or binary-alpha appearance."),
        "pixel": ("Intentional low-detail pixel mask: hard edges and discrete neutral values; "
                  "no antialiasing, blur, dithering noise or painted glow."),
    }[cfg["processing"]]
    blanks = cfg["allow_blank_frames"].get(state, [])
    sparse = cfg["allow_sparse_frames"].get(state, [])
    ending = ("Seamless loop: last-to-first motion must remain continuous; do not add an undeclared blank ending."
              if entry.get("loop", False) else
              "One-shot: a clear onset, readable main motion, and erosion/fade of that same layer. "
              "Do not loop back or replace the fading shape with new secondary particles.")
    return f"""Create one horizontal VFX flipbook strip for `{request['character']['id']}`, state `{state}`.
{LIBRARY_CONTRACT}

Asset role: {guide['role']}.
Primary subject: {guide['subject']}.
Preset-specific exclusions: {guide['exclude']}.
Shape notes: {request['character'].get('description') or cfg['preset']}.
Style notes: {request['style']}. {processing}
Interpret shape/style notes within this single-layer, neutral-palette contract, not as instructions to add a finished effect stack.
Use any attached effect reference for primary shape language only, NOT its color, lighting, environment or secondary layers.
The layout guide is geometry guidance, not a style or palette reference.

Animation: {entry['action']}.
{ending}
Custom animation notes describe this layer's motion; they do not authorize extra materials or decorative sublayers.
Detached sparks belong only in a spark-layer request; do not add them as garnish to other presets.
Breakup may erode the primary shape, but must not spawn an unrelated particle layer.
Keep fragments of the intended material in their own frame; never connect neighboring frames.
Keep the camera, zoom, orientation, scale and coordinate system fixed for the whole animation.
Use a fixed origin at ({x:.4f}, {y:.4f}) of EVERY frame, measured from its top-left.
Expansion, upward drift and directional travel must happen relative to this origin.
Never recenter or enlarge a small/fading frame to fill its cell. Leave intentional negative space intact.

Layout: exactly {n} frames in one horizontal row, ordered left to right, with intended frame aspect {w}:{h}.
Aim for evenly spaced frames with clear empty gutters; do not compose a multi-row contact sheet.
The layout guide establishes slots and safe padding only; do not draw its lines, centers or labels.
Keep the complete primary element inside each frame, including its soft edges; do not overlap adjacent frames.
Reserve at least {cell['safe_margin_x']} horizontal and {cell['safe_margin_y']} vertical pixels of padding at {w}x{h}.
Requested blank frame indices (0-based): {blanks}. Only these slots may be entirely empty.
Requested sparse frame indices (0-based): {sparse}. These may hold a tiny remnant of the same layer but must not be empty.
Every other slot must contain a readable stage of the primary element, without embellishments added merely to fill space.
{background}
Output only the strip image. Prefer a simple reusable texture over a spectacular finished effect."""
