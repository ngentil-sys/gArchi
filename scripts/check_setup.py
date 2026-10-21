#!/usr/bin/env python3
"""Print a setup diagnosis (same as the agent's check_setup tool)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gcp_architect.tools import check_setup  # noqa: E402

print(json.dumps(check_setup(), indent=2))
