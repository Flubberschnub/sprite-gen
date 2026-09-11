# VFX flipbooks

> Owns: effect presets, content-aware/fixed-slot extraction, fixed-origin repacking, VFX alpha/QA policies and uniform-grid export.

This is the **image-row VFX path**, built on the existing prepare, generation, extraction,
curation and composition stages. It generates reusable effect ingredients, not Unity
prefabs or complete multi-layer spells. No extra image provider or paid service is added.

## Start an effect run

Commands assume the repository is installed in its project venv. Select an available
provider explicitly; the normal authentication and billing rules still apply.

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen prepare \
  --out-dir /absolute/runs/shockwave --character-id altair-shockwave \
  --effect-preset shockwave --cell-size 256 --vfx-processing crisp
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen gen-set \
  --run-dir /absolute/runs/shockwave --provider codex
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen extract --run-dir /absolute/runs/shockwave
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen inspect --run-dir /absolute/runs/shockwave
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen export-flipbook \
  --run-dir /absolute/runs/shockwave --columns 4
```

`--effect-preset` implies `--subject effect`. The retained `--character-id` flag is just
the run's asset identifier; no character is required. Add `--base-image /absolute/effect.png`
for an approved effect reference. Without one, `gen-set` uses text plus the layout guide.
Provider `grok` can also be selected for image generation. No model is called by prepare,
extract, inspect, curation or export.

Presets: `burst`, `hit`, `shockwave`, `dust`, `slash`, `sparks`, `movement`, `pulse`.
Each supplies a frame count, fps, loop flag, action and fixed origin. Pulse loops; the
others are one-shots. Custom `states` in `--request` override the animation recipe.
`--subject effect` without a preset starts with `burst`, not character idle/attack states.

Before export, optional curation uses the existing tool:

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen curation --run-dir /absolute/runs/shockwave
```

Re-export after editing. Selection, ordering, clones, pixel edits and transforms are baked
by the existing atlas composer; export does not read uncurated raw frames. Use **clones**
for held frames: duplicate indices in `selected` are deduplicated by the curation contract.
Unlike the deduplicating runtime atlas, a flipbook repeats each held instance in a distinct
cell, preserving its duration in a regular texture-grid player.

## Source layout recovery and fixed frame coordinates

A VFX sequence can have many disconnected pieces, change size dramatically and disappear.
The final game flipbook still needs uniform cells, but an AI-generated source strip is **not
required to have perfectly equal source slots**.

New VFX runs default to:

```json
"vfx": { "layout": "content-aware" }
```

Content-aware extraction reuses sprite-gen's existing horizontal projection / dynamic-
programming segmentation machinery. After alpha/matte extraction, it measures horizontal
content mass and chooses the requested number of low-cost cuts instead of blindly slicing
at `strip_width / frame_count`. This is the same family of protection that keeps character
rows usable when generated poses drift away from the requested grid.

The recovered regions are then **repacked**, not independently normalized. Every region:

- keeps its original pixels and relative visible size;
- is padded to one shared source-canvas width;
- aligns the same configured normalized VFX origin;
- receives one shared aspect-preserving scale into the runtime cell.

That distinction is important: a small ignition remains smaller than a peak explosion, and
a directional effect is not enlarged merely because its visible bounds are narrow. The
content-aware cut discovers source boundaries; it does not make every frame fill its cell.
The final atlas/flipbook remains a regular grid even when the generated source spacing was
irregular or its total width was not divisible by the frame count.

For a known regular hand-authored sheet, opt back into strict slicing:

```bash
sprite-gen prepare ... --vfx-layout fixed-slots
```

or request JSON:

```json
"vfx": { "layout": "fixed-slots" }
```

`fixed-slots` requires regular source slots. Provider outputs that are only a few pixels off
the requested total width are normalized as one whole strip to the nearest divisible width
before slicing; the low-level fixed-slot splitter itself stays strict. This mode is useful
when source geometry is already authoritative and content-aware inference is undesirable.

A declared intentionally blank source frame has no visible content from which a separator
can be inferred, so states using `allow_blank_frames` automatically use the fixed-slot path
for that extraction and record the fallback in VFX geometry metadata. Sparse-but-nonempty
frames can still use content-aware recovery.

Edge QA runs on the **actual recovered source regions before repacking**. Therefore source
edge contact can mean either genuine clipping at the outer image boundary or a proposed
internal cut crossing visible content. It is no longer evidence merely that an imaginary
equal-width slot boundary happened to pass through an otherwise complete effect. Inspect
the extracted result before accepting `edge_policy: "warn"`.

Origins are normalized **top-left** coordinates. Defaults include `(0.5, 0.5)` for radial
effects and `(0.5, 0.85)` for dust. Override with `--vfx-origin 0.25,0.5`. The JSON export
also records a bottom-left `unity_pivot`. This is metadata, not an automatically installed
Unity importer: configure the material, texture-sheet playback and particle/mesh origin
in the consuming project.

## Matte and processing are separate choices

| `--vfx-matte` | Source and output contract |
|---|---|
| `chroma` (default) | A flat key background, removed with the existing chroma engine. Choose a key away from effect hues. Partial edge alpha is retained. Soft smoke matting remains heuristic, not guaranteed ground-truth coverage. |
| `source-alpha` | An existing RGBA PNG or palette image with transparency. Preserve its alpha and dark material; clear invisible RGB. Reject RGB plates rather than inventing alpha. A generation provider must actually return alpha for this route. |
| `black-additive` | Emissive energy composited over pure black. Unassociate emission into straight RGBA for **Blend SrcAlpha One**. This is not a black-background smoke cutout. |

Black-additive conversion uses `A = max(R,G,B)` and `RGB = emission/A`, so `RGB*A`
reconstructs the black-composited source within 8-bit rounding. Simply using brightness
as alpha while retaining the original RGB would dim the effect twice. Export names both
blend factors explicitly. Do not feed these straight-alpha textures into a premultiplied
or `One One` shader without the corresponding conversion. Previews are diagnostic 8-bit
composites, not a substitute for verifying the engine's color space, bloom and material.

| `--vfx-processing` | Behavior |
|---|---|
| `crisp` (default) | Graphic anime prompt; preserve antialiased edges. Shared canvas resampling, no forced outline. |
| `soft` | Translucent/wispy prompt; preserve continuous alpha. No palette reduction or outline. |
| `pixel` | Nearest-neighbor canvas sampling and explicit binary alpha. No per-frame grid detection or body alignment. |

Pixel processing is intentionally incompatible with black-additive's continuous-alpha
encoding. The character `fit.pixel_unfake` path and body-specific fit keys are rejected for
VFX rather than silently erasing a smoke gradient or real motion. `fit.resample` may select
`nearest` or `lanczos`. Plain curation variants are available; for pixel effects they retain
the source coverage before binarization.

## Intentional blank and sparse frames

New VFX runs write a validated `vfx` block in `sprite-request.json`. Policies are stored in
the request, not one-off flags, so re-extraction and healing reproduce them. Example input
for `prepare --request /absolute/effect-request.json`:

```json
{
  "subject": "effect",
  "vfx": {
    "preset": "burst",
    "layout": "content-aware",
    "matte": "chroma",
    "processing": "crisp",
    "origin": [0.5, 0.5],
    "allow_blank_frames": {"burst": [0, 7]},
    "allow_sparse_frames": {"burst": [6]},
    "edge_policy": "error",
    "edge_alpha": 8
  },
  "states": {
    "burst": {
      "frames": 8,
      "fps": 16,
      "loop": false,
      "action": "blank, ignite, expand, peak, fragment, dissipate, residual spark, blank"
    }
  }
}
```

Indices are **0-based source-frame indices**, even after curation reordering or cloning.
An allowed sparse frame must contain at least one nontransparent pixel; it is not permission
to drop the frame. Blank allowances may retain real content, but authorize zero coverage at
those indices. An entirely empty sequence always fails. Unknown fields, invalid indices,
nonpositive frame counts/fps and incompatible character options are rejected explicitly.

Default edge policy is `error` for any boundary pixel above `edge_alpha` (default 8/255).
`edge_policy: "warn"` is an explicit acceptance of edge contact, not a repair. Expansion
and fragmentation are not penalized by character silhouette/identity metrics; motion and
extraction diagnostics remain available through `inspect` and `score`.

Extraction uses the existing staged generation publish and failure evidence. Failed work
does not replace the previous valid frames. Fix and re-extract unresolved failures before
export. VFX policy changes invalidate the VFX recipe stamp and trigger healing; frozen rows
must be unfrozen when changing their recipe. After replacing a raw strip, run `extract`
explicitly, as in the normal pipeline.

## Export contract

Each state produces these files under `<run-dir>/flipbooks/`:

- `<state>.png`: regular RGBA grid, left-to-right then top-to-bottom.
- `<state>.json`: frame count, fps/duration, grid rectangles, origin/pivot, loop flag,
  straight-alpha/blend contract, curation and processing information.
- `<state>.preview-dark.png` and `<state>.preview-light.png`: background-composited
  contact sheets for checking fringes and density. Additive previews use additive blending.

`export.report.json` lists the current export batch. `--state` selects one state from a
complete run; omit it to export every state. `--out-dir` relocates the deliverables.
`--max-size` (default 8192) rejects oversized output before allocating that grid.

Without `--columns`, packing chooses a near-square exact factor of the frame count, so
there are no unused cells (prime counts become strips). Explicit column counts can leave
transparent unused cells; the metadata reports these and the valid frame-index range.
For Unity Texture Sheet Animation, use the recorded columns/rows, one cycle for a one-shot,
and limit sampling to the actual frame count when there are unused cells. A shader that
assumes every cell is a valid frame will otherwise play unintended transparent frames.

`--padding N` adds transparent padding to **every complete canvas** and adjusts the exported
pivot; it is not per-frame trimming or alpha dilation. Do not use tight packing or rotation
when importing a regular flipbook. Mipmaps, filtering and texture compression still need
validation at gameplay size; gutters alone do not guarantee zero mip-level bleeding.

`--append-blank` deterministically appends one transparent terminal frame to a one-shot,
without asking the model to draw it. It adds one frame to the reported duration. Looping
states reject this option. `--grayscale` bakes tintable grayscale RGB while keeping alpha.
The grid remains a PNG asset, not a Unity prefab, normal map or HDR texture.

## Compatibility and limits

Character runs are unchanged. Existing hand-authored `subject: effect` requests **without**
a `vfx` block retain legacy component extraction and the old sparse profile. To migrate,
prepare a new effect run (preserving the old raw/curation as a backup), or add a validated
`vfx` block and re-extract. New VFX requests default to content-aware source recovery;
`fixed-slots` is available when exact source slot geometry is already known. Preparing a
new effect run also replaces the character prompt rules with effect rules.

Content-aware segmentation is deterministic but not omniscient. If an effect has no usable
horizontal separation between consecutive frames, the requested frame count cannot be
recovered safely and extraction fails rather than silently falling back to arbitrary cuts.
An interactive/manual boundary editor is not part of this version; such a tool can be added
later for genuinely ambiguous source art.

The video-to-loop route is not modified by this feature. Its rest-pose detector, cleanup
and loop assumptions should not be used as an automatic one-shot VFX extractor. Character
rig layers/directional anchors, VFX takes, automatic multi-layer spell authoring, optical
flow and Unity prefab/importer generation are outside this version.

The tests in `tests/vfx/` are deterministic synthetic acceptance tests, not evidence of an
AI model's animation quality. Judge generated motion in playback and test the exported
textures with the actual game's shader before treating an effect as production-ready.
