from __future__ import annotations


def job_identity_key(source: str, source_job_id: str | None, canonical_url: str) -> str:
    """Stable market identity that survives rich-row archival/resurrection."""
    source_key = " ".join((source or "").split()).casefold()
    if not source_key:
        raise ValueError("source is required for identity")
    source_id = " ".join((source_job_id or "").split()).strip()
    if source_id:
        return f"{source_key}:id:{source_id}"
    url = (canonical_url or "").strip()
    if not url:
        raise ValueError("canonical_url is required for identity fallback")
    return f"{source_key}:url:{url}"
