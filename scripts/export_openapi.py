"""Export the FastAPI OpenAPI contract for downstream consumers.

Run after changing any API route or schema:
    uv run python scripts/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from glide.api.app import app


def main() -> None:
    spec = app.openapi()
    target = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
    target.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {target} ({len(target.read_text(encoding='utf-8'))} bytes)")


if __name__ == "__main__":
    main()
