"""Fetch product imagery from Pexels once and commit the result.

The output (``data/catalog_images.json``) is checked in so neither the app nor
the demo needs an API key at runtime.

    set PEXELS_ACCESS_KEY=...      # or export on unix
    python scripts/fetch_images.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "catalog_images.json"

QUERIES = {
    "audio": "headphones product",
    "computing": "laptop desk product",
    "wearables": "smart watch product",
    "photography": "camera product",
    "home": "minimal home decor",
    "kitchen": "coffee maker kitchen",
    "fitness": "running shoes sport",
    "outdoor": "backpack hiking gear",
    "fashion": "leather bag fashion",
    "beauty": "skincare bottle product",
    "gaming": "gaming controller",
    "books": "stack of books",
}

PER_QUERY = 8


def fetch(query: str, key: str) -> list[dict]:
    url = (
        "https://api.pexels.com/v1/search?"
        + urllib.parse.urlencode({"query": query, "per_page": PER_QUERY, "orientation": "square"})
    )
    # Pexels rejects the default urllib user agent.
    request = urllib.request.Request(
        url, headers={"Authorization": key, "User-Agent": "streamrank-catalog/1.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    return [
        {
            "id": photo["id"],
            "photographer": photo["photographer"],
            "url": photo["src"]["medium"],
            "large": photo["src"]["large"],
            "alt": (photo.get("alt") or query).strip()[:120],
        }
        for photo in payload.get("photos", [])
    ]


def main() -> int:
    key = os.environ.get("PEXELS_ACCESS_KEY")
    if not key:
        print("PEXELS_ACCESS_KEY is not set", file=sys.stderr)
        return 1
    catalog = {category: fetch(query, key) for category, query in QUERIES.items()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    total = sum(len(v) for v in catalog.values())
    credits = sorted({photo["photographer"] for photos in catalog.values() for photo in photos})
    print(f"wrote {OUT} - {total} images across {len(catalog)} categories")
    (OUT.parent / "image_credits.txt").write_text(
        "Product photography from Pexels. Photographers:\n" + "\n".join(f"- {name}" for name in credits),
        encoding="utf-8",
    )
    print(f"credits written to {OUT.parent / 'image_credits.txt'} ({len(credits)} photographers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
