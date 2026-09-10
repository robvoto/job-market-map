from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

from collector.browser_broker import open_tab, snapshot

OUT = Path("exports/card_benchmark.json")


def linkedin_job_id(url: str) -> str | None:
    m = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d{7,12})", url or "", re.IGNORECASE)
    return m.group(1) if m else None


def seek_job_id(url: str) -> str | None:
    m = re.search(r"/job/(\d{7,10})", url or "", re.IGNORECASE)
    return m.group(1) if m else None


def inspect_source(name: str, url: str) -> dict:
    opened = open_tab(url, active=False)
    page_id = int(opened.result["pageId"])
    time.sleep(4)
    snap = snapshot(page_id, verbose=True)
    data = snap.result or {}
    elements = data.get("elements") or []
    if name == "linkedin":
        ids = [(linkedin_job_id(str(e.get("href") or "")), e) for e in elements]
    else:
        ids = [(seek_job_id(str(e.get("href") or "")), e) for e in elements]
    cards = {}
    for job_id, element in ids:
        if job_id and job_id not in cards:
            cards[job_id] = {
                "text": element.get("text"),
                "ariaLabel": element.get("ariaLabel"),
                "href": element.get("href"),
            }
    return {
        "source": name,
        "page_id": page_id,
        "url": data.get("url"),
        "title": data.get("title"),
        "open_seconds": round(opened.elapsed_seconds, 3),
        "snapshot_seconds": round(snap.elapsed_seconds, 3),
        "page_text_chars": len(data.get("text") or ""),
        "interactive_elements": len(elements),
        "unique_job_links": len(cards),
        "card_link_samples": list(cards.items())[:5],
        "page_text_head": (data.get("text") or "")[:3000],
    }


def main():
    linkedin = (
        "https://www.linkedin.com/jobs/search/?keywords="
        + quote_plus("technical implementation")
        + "&location="
        + quote_plus("Sydney NSW")
        + "&f_TPR=r604800&sortBy=DD"
    )
    seek = "https://www.seek.com.au/technical-implementation-jobs/in-Sydney-NSW?daterange=7&sortmode=ListedDate"
    results = [inspect_source("linkedin", linkedin), inspect_source("seek", seek)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    for item in results:
        print(
            f"{item['source']}: jobs={item['unique_job_links']} elements={item['interactive_elements']} "
            f"open={item['open_seconds']}s snapshot={item['snapshot_seconds']}s text_chars={item['page_text_chars']}"
        )
    print(OUT)


if __name__ == "__main__":
    main()
