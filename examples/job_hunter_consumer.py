"""Minimal Job Hunter-style consumer of the stable Job Market Map v1 feed.

This example deliberately uses HTTP rather than importing the mapper's SQLite schema.
A real consumer persists next_cursor only after it has safely processed the returned page.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import urlopen

BASE_URL = "http://127.0.0.1:8770/v3"


def fetch_jobs(after_id: int = 0, limit: int = 100) -> dict:
    query = urlencode({"after_id": after_id, "limit": limit, "include_raw": "true"})
    with urlopen(f"{BASE_URL}/feed/jobs?{query}", timeout=10) as response:
        return json.load(response)


if __name__ == "__main__":
    page = fetch_jobs()
    print(
        f"received={len(page['items'])} next_cursor={page['next_cursor']} has_more={page['has_more']}"
    )
