from __future__ import annotations

import re
import time
from urllib.parse import quote_plus

from collector.browser_broker import navigate, open_tab, snapshot

JOB = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{7,12})", re.IGNORECASE)
base = (
    "https://www.linkedin.com/jobs/search/?keywords="
    + quote_plus("technical implementation")
    + "&location="
    + quote_plus("Sydney NSW")
    + "&f_TPR=r604800&sortBy=DD"
)
opened = open_tab(base + "&start=0", active=False)
pid = int(opened.result["pageId"])
seen = set()
for start in (0, 7, 14, 21, 25, 32, 50):
    if start:
        navigate(pid, base + f"&start={start}")
    time.sleep(2.5)
    x = snapshot(pid, verbose=True).result or {}
    ids = []
    for e in x.get("elements") or []:
        m = JOB.search(str(e.get("href") or ""))
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    new = [i for i in ids if i not in seen]
    seen.update(ids)
    print(
        "start",
        start,
        "count",
        len(ids),
        "new",
        len(new),
        "ids",
        ids,
        "url",
        x.get("url"),
    )
print("total_unique", len(seen))
