"""Run from Ubuntu: python3 -B scripts/import-products.py.

Uses the existing WordPress container's PHP and WordPress API; no WP-CLI install,
container copy/recreate, credentials in arguments, or new container is needed.
Reruns skip existing marked sample codes, preserving subsequent user edits.
Stops on unmarked code collisions, including drafts and trash. No images imported.
"""
import base64
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
data = (root / "catalog/products.sample.json").read_bytes()
json.loads(data)
source = (root / "scripts/import-products.php").read_text(encoding="utf-8")
assert source.startswith("<?php")
prefix = "<?php\ndefine('BG_SAMPLE_JSON', base64_decode('" + base64.b64encode(data).decode("ascii") + "'));\n"
result = subprocess.run(
    ["docker", "exec", "-i", "beautyglow-website-wordpress-1", "php"],
    input=(prefix + source[len("<?php"):]).encode("utf-8"),
    capture_output=True,
)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
