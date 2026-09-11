#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Backward-compatible wrapper for sprite_gen.compose.export_flipbook."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sprite_gen.compose.export_flipbook import main
if __name__ == "__main__":
    raise SystemExit(main())
