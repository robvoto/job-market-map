from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class CardObservation:
    source: str
    canonical_url: str
    source_job_id: str | None = None
    title: str | None = None
    employer: str | None = None
    location: str | None = None
    geography_code: str | None = None
    salary_text: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    posted_text: str | None = None
    posted_at: str | None = None
    source_status: str | None = None
    apply_method: str | None = None
    reposted: bool = False
    applicant_count: int | None = None
    easy_apply: bool | None = None
    teaser_text: str | None = None
    raw_card_text: str | None = None
    classification_text: str | None = None
    subclassification_text: str | None = None
    card_tags: list[str] | None = None
    raw_json: dict[str, Any] | None = None
    query_text: str | None = None
    query_origin: str | None = None
    query_location: str | None = None
    rank: int | None = None
    page_number: int | None = None
    captured_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
