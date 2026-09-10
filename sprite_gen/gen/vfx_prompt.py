# SPDX-License-Identifier: Apache-2.0
"""Effect-specific row prompts, without character identity or connected-body rules."""
from __future__ import annotations

from typing import Any
from sprite_gen.spec.vfx import config


def row_prompt(request: dict[str, Any], state: str, entry: dict[str, Any]) -> str:
    cfg = config(request)
    assert cfg is not None
    cell = request["cell"]
    w, h = cell["width"], cell["height"]
    n = entry["frames"]
    x, y = cfg["origin"]
    key = request["chroma_key"]
    background = {
        "chroma": (f"Use a perfectly flat {key['hex']} chroma background. Never use this hue or "
                   "adjacent hues in the effect. No checkerboard or painted transparency."),
        "source-alpha": "Output real RGBA transparency, not a checkerboard, white or colored background.",
        "black-additive": ("Render luminous energy on perfectly pure black (#000000). Black is zero "
                           "emission, not opaque material. No environmental lighting or scenery."),
    }[cfg["matte"]]
    processing = {
        "crisp": "Crisp graphic anime shapes, deliberate edges and controlled shading; antialiasing is allowed.",
        "soft": "Preserve wispy translucent edges and smooth density falloff; no imposed outlines or binary alpha.",
        "pixel": "Intentional pixel art, hard edges, discrete pixels and no antialiasing or blur.",
    }[cfg["processing"]]
    blanks = cfg["allow_blank_frames"].get(state, [])
    sparse = cfg["allow_sparse_frames"].get(state, [])
    ending = ("Seamless loop: last-to-first motion must remain continuous; do not add an undeclared blank ending."
              if entry.get("loop", False) else
              "One-shot: readable onset, expansion/peak and dissipation. Do not loop back or resurrect the effect.")
    return f"""Create one horizontal VFX flipbook strip for `{request['character']['id']}`, state `{state}`.
Effect: {request['character'].get('description') or cfg['preset']}.
Style: {request['style']}. {processing}
Use any attached effect reference only for shape language, palette and rendering style, NOT a frozen silhouette.
No character, anatomy, face, clothing, idle pose, scenery, captions or UI.

Animation: {entry['action']}.
{ending}
Detached sparks, dust, smoke fragments, energy arcs and changing silhouettes are allowed when relevant.
Keep fragments in their own frame; never connect effects between adjacent cells.
Keep the camera, zoom, orientation, scale and coordinate system fixed for the whole animation.
Use a fixed origin at ({x:.4f}, {y:.4f}) of EVERY frame, measured from its top-left.
Expansion, upward drift and directional travel must happen relative to this origin.
Never recenter or enlarge a small/fading frame to fill its cell. Leave intentional negative space intact.

Layout: exactly {n} equal frame slots, left to right, each with aspect {w}:{h}.
The layout guide establishes slots and safe padding only; do not draw its lines, centers or labels.
Keep the complete effect inside each slot, including faint wisps and all detached fragments.
Reserve at least {cell['safe_margin_x']} horizontal and {cell['safe_margin_y']} vertical pixels of padding at {w}x{h}.
Requested blank frame indices (0-based): {blanks}. Only these slots may be entirely empty.
Requested sparse frame indices (0-based): {sparse}. These may hold a tiny residual fragment but must not be empty.
Every other slot must contain a clearly readable stage of the animation.
{background}
Output only the strip image."""
