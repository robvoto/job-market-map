from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from collector.jd_batch import missing_jd_primary_ids
from collector.run_logging import collection_logger, configure_collection_logging
from collector.run_lock import CollectionAlreadyRunning, collection_run_lock


def _fetch_via_api(api_url: str, job_id: int, *, source: str | None = None) -> str:
    url = f"{api_url.rstrip('/')}/jobs/{job_id}/jd"
    if source:
        from urllib.parse import urlencode
        url += "?" + urlencode({"source": source})
    request = Request(url, method="POST")
    try:
        with urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 410:
            return "unavailable"
        if exc.code in {404, 422}:
            raise RuntimeError(f"JD request cannot be completed HTTP {exc.code}: {body}") from exc
        raise RuntimeError(f"JD request failed HTTP {exc.code}: {body}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"JD request failed: {exc}") from exc
    return str(payload.get("status") or "enriched")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch missing JDs for a bounded recent market window.")
    parser.add_argument("--posted-since", required=True, help="ISO-8601 cutoff, e.g. 2026-09-16T00:00:00+10:00")
    parser.add_argument("--source", choices=["seek", "linkedin", "apsjobs"], default=None)
    parser.add_argument("--api-url", default="http://127.0.0.1:8770/v3")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N candidates for a safe sample run.")
    args = parser.parse_args(argv)
    configure_collection_logging()
    log = collection_logger()

    ids = missing_jd_primary_ids(source=args.source, posted_since=args.posted_since)
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be >= 1")
        ids = ids[: args.limit]
    try:
        with collection_run_lock("jd-repair", source=args.source or "all"):
            stored = cached = failed = unavailable = 0
            log.info("Recent JD repair started cutoff=%s source=%s candidates=%s limit=%s", args.posted_since, args.source or "all", len(ids), args.limit)
            print(f"Recent JD repair: {len(ids)} candidates", flush=True)

            for position, job_id in enumerate(ids, start=1):
                outcome = None
                last_error: Exception | None = None
                for attempt in (1, 2):
                    try:
                        outcome = _fetch_via_api(args.api_url, job_id, source=args.source)
                        last_error = None
                        break
                    except RuntimeError as exc:
                        last_error = exc
                        message = str(exc)
                        rate_limited = (
                    "429" in message
                    or "rate limit" in message.casefold()
                    or "rate-limit" in message.casefold()
                )
                        if rate_limited:
                            failed += 1
                            log.error("JD_REPAIR_FAILED job_id=%s source=%s error=%s", job_id, args.source or "any", exc)
                            log.error(
                                "JD_REPAIR_RATE_LIMIT_ABORT job_id=%s source=%s position=%s/%s error=%s",
                                job_id, args.source or "any", position, len(ids), exc,
                            )
                            print(f"JD repair stopped on source rate limit at {position}/{len(ids)}", flush=True)
                            return 2
                        if attempt == 1:
                            log.warning("JD repair failed; retrying once job_id=%s error=%s", job_id, exc)
                            time.sleep(0.25)
                            continue
                        failed += 1
                        log.error("JD_REPAIR_FAILED job_id=%s source=%s error=%s", job_id, args.source or "any", exc)
                if outcome == "unavailable":
                    unavailable += 1
                elif outcome == "cached":
                    cached += 1
                elif outcome:
                    stored += 1
                if position == 1 or position % 25 == 0 or position == len(ids):
                    message = (
                        f"JD repair progress {position}/{len(ids)} stored={stored} cached={cached} "
                        f"failed={failed} unavailable={unavailable}"
                    )
                    print(message, flush=True)
                    log.info(message)
                if last_error is not None:
                    continue

            print(
                f"JD repair complete candidates={len(ids)} stored={stored} cached={cached} "
                f"failed={failed} unavailable={unavailable}",
                flush=True,
            )
            return 1 if failed else 0
    except CollectionAlreadyRunning as exc:
        log.warning("Recent JD repair skipped because collection lock is active: %s", exc)
        print(f"JD repair blocked by active collection: {exc}", flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
