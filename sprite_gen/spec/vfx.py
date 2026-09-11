# SPDX-License-Identifier: Apache-2.0
"""VFX request contract: source recovery, fixed frame space, matte, processing and QA.

An existing effect run WITHOUT a ``vfx`` block keeps legacy component extraction.
New ``prepare --subject effect`` runs write this block. Character requests never do.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from typing import Any

PRESETS: dict[str, dict[str, Any]] = {
    "burst": {"frames": 8, "fps": 16, "origin": [0.5, 0.5], "loop": False,
              "action": "tiny ignition, explosive expansion, sharp peak, fragmentation, dissipating embers"},
    "hit": {"frames": 8, "fps": 24, "origin": [0.5, 0.5], "loop": False,
            "action": "brief anticipation, sharp asymmetric impact star, fast outward shards, rapid dissipation"},
    "shockwave": {"frames": 8, "fps": 16, "origin": [0.5, 0.5], "loop": False,
                  "action": "small ring expands from a fixed center, thins, breaks apart and dissipates"},
    "dust": {"frames": 12, "fps": 16, "origin": [0.5, 0.85], "loop": False,
             "action": "small grounded puff expands upward and outward, curls, fragments and dissipates"},
    "slash": {"frames": 8, "fps": 24, "origin": [0.25, 0.5], "loop": False,
              "action": "thin leading crescent sweeps right, reaches a broad peak, leaves shards and dissipates"},
    "sparks": {"frames": 12, "fps": 24, "origin": [0.5, 0.5], "loop": False,
               "action": "compact ignition scatters disconnected sparks outward, which slow and fade"},
    "movement": {"frames": 8, "fps": 24, "origin": [0.2, 0.5], "loop": False,
                 "action": "directional acceleration streaks extend right from the origin, stretch, break and fade"},
    "pulse": {"frames": 12, "fps": 16, "origin": [0.5, 0.5], "loop": True,
              "action": "energy gathers, expands and contracts in a seamless pulse with a continuous wrap"},
}
MATTES = ("chroma", "source-alpha", "black-additive")
PROCESSING = ("crisp", "soft", "pixel")
LAYOUTS = ("content-aware", "fixed-slots")
STYLE_DEFAULT = "stylized anime game VFX, bold readable silhouettes, controlled palette, clean negative space"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--effect-preset", choices=tuple(PRESETS), default=None,
                        help="VFX preset (implies --subject effect); custom states may override its animation")
    parser.add_argument("--vfx-layout", choices=LAYOUTS, default=None,
                        help="source-strip recovery: content-aware (default for AI output) or strict fixed-slots")
    parser.add_argument("--vfx-matte", choices=MATTES, default=None)
    parser.add_argument("--vfx-processing", choices=PROCESSING, default=None)
    parser.add_argument("--vfx-origin", type=parse_origin, default=None, metavar="X,Y",
                        help="fixed normalized origin, top-left coordinates (default from preset)")


def parse_origin(value: str) -> list[float]:
    try:
        origin = [float(v) for v in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("origin must be X,Y in the range 0..1") from exc
    if len(origin) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in origin):
        raise argparse.ArgumentTypeError("origin must be X,Y in the range 0..1")
    return origin


def preset_states(preset: str) -> dict[str, dict[str, Any]]:
    entry = PRESETS[preset]
    return {preset: {k: entry[k] for k in ("frames", "fps", "loop", "action")}}


def normalize_vfx(raw: Any, states: dict[str, Any]) -> dict[str, Any]:
    """Filesystem-free and fail-loud; callers may not silently ignore misspelled keys."""
    if not isinstance(raw, dict):
        raise SystemExit("vfx must be an object")
    keys = {"version", "preset", "layout", "matte", "processing", "origin", "allow_blank_frames",
            "allow_sparse_frames", "edge_policy", "edge_alpha"}
    if set(raw) - keys:
        raise SystemExit(f"unknown vfx key(s): {sorted(set(raw) - keys)}")
    if type(raw.get("version", 1)) is not int or raw.get("version", 1) != 1:
        raise SystemExit("vfx.version must be 1")
    preset = raw.get("preset", "burst")
    if preset not in PRESETS:
        raise SystemExit(f"unknown effect preset: {preset!r}")
    result = {
        "version": 1,
        "preset": preset,
        "layout": raw.get("layout", "content-aware"),
        "matte": raw.get("matte", "chroma"),
        "processing": raw.get("processing", "crisp"),
        "origin": raw.get("origin", list(PRESETS[preset]["origin"])),
        "edge_policy": raw.get("edge_policy", "error"),
        "edge_alpha": raw.get("edge_alpha", 8),
    }
    if result["layout"] not in LAYOUTS:
        raise SystemExit(f"vfx.layout must be one of {LAYOUTS}")
    if result["matte"] not in MATTES:
        raise SystemExit(f"vfx.matte must be one of {MATTES}")
    if result["processing"] not in PROCESSING:
        raise SystemExit(f"vfx.processing must be one of {PROCESSING}")
    if result["matte"] == "black-additive" and result["processing"] == "pixel":
        raise SystemExit("black-additive requires continuous alpha; use crisp or soft processing")
    origin = result["origin"]
    if (not isinstance(origin, (list, tuple)) or len(origin) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or not 0 <= v <= 1 for v in origin)):
        raise SystemExit("vfx.origin must contain two finite normalized coordinates in 0..1")
    result["origin"] = list(origin)
    if result["edge_policy"] not in ("error", "warn"):
        raise SystemExit("vfx.edge_policy must be error or warn")
    if type(result["edge_alpha"]) is not int or not 0 <= result["edge_alpha"] <= 254:
        raise SystemExit("vfx.edge_alpha must be an integer in 0..254")
    if not states:
        raise SystemExit("VFX requires at least one animation state")
    for name, entry in states.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise SystemExit(f"VFX state must be a safe filename: {name!r}")
        if not isinstance(entry, dict):
            raise SystemExit(f"VFX state {name} must be an object")
        for key in ("frames", "fps"):
            if type(entry.get(key)) is not int or entry[key] <= 0:
                raise SystemExit(f"VFX state {name}.{key} must be a positive integer")
        if type(entry.get("loop", False)) is not bool:
            raise SystemExit(f"VFX state {name}.loop must be boolean")
        if entry.get("takes"):
            raise SystemExit("VFX takes are not supported; use separate states or runs")
    for field in ("allow_blank_frames", "allow_sparse_frames"):
        mapping = raw.get(field, {})
        if not isinstance(mapping, dict) or set(mapping) - set(states):
            raise SystemExit(f"vfx.{field} must map declared states to 0-based frame indices")
        result[field] = {}
        for state, indices in mapping.items():
            if (not isinstance(indices, list) or any(type(i) is not int
                    or not 0 <= i < states[state]["frames"] for i in indices)):
                raise SystemExit(f"vfx.{field}.{state} contains invalid 0-based frame indices")
            result[field][state] = sorted(set(indices))
    return result


def config(request: dict[str, Any]) -> dict[str, Any] | None:
    if "vfx" not in request:
        return None
    if request.get("subject") != "effect":
        raise SystemExit("a vfx block requires subject: effect")
    if request.get("directions") or request.get("rig") or request.get("layers"):
        raise SystemExit("VFX uses a fixed effect origin, not character directions or rig layers")
    fit = request.get("fit") or {}
    if fit.get("pixel_unfake") or set(fit) - {"pixel_unfake", "resample"}:
        raise SystemExit("VFX does not use character fit/alignment/pixel-unfake; use vfx.processing and vfx.origin")
    if fit.get("resample", "lanczos") not in ("nearest", "lanczos"):
        raise SystemExit("VFX fit.resample supports nearest or lanczos")
    return normalize_vfx(request["vfx"], request.get("states") or {})


def frame_floor(cfg: dict[str, Any], state: str, index: int, default: int) -> int:
    if index in cfg["allow_blank_frames"].get(state, []):
        return 0
    if index in cfg["allow_sparse_frames"].get(state, []):
        return 1  # sparse does NOT authorize an empty frame
    return max(1, default)


def fingerprint(request: dict[str, Any], state: str) -> str:
    payload = {"vfx": config(request), "cell": request["cell"], "fit": request.get("fit"),
               "state": request["states"][state], "chroma": request.get("chroma"),
               "chroma_key": request.get("chroma_key")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
