#!/usr/bin/env python3
"""End-to-end smoke test: render the built-in example spec (or a JSON file) to a real Slides deck.

Usage: python scripts/render_example.py [spec.json] [--update PRESENTATION_ID]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gcp_architect.diagram.render import render_deck  # noqa: E402
from gcp_architect.diagram.spec import ArchitectureSpec, example_spec  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("spec", nargs="?")
ap.add_argument("--update", help="rebuild this presentation id in place")
args = ap.parse_args()

data = json.loads(Path(args.spec).read_text()) if args.spec else example_spec()
result = render_deck(ArchitectureSpec.model_validate(data), presentation_id=args.update)
print(json.dumps(result.to_dict(), indent=2))
