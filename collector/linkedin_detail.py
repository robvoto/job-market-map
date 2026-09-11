from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

from collector.settings import get_setting

HEADER_CLASS_TOKENS = {
    "job-details-jobs-unified-top-card__container",
    "job-details-jobs-unified-top-card__container--two-pane",
    "job-details-jobs-unified-top-card__primary-description-container",
    "job-details-jobs-unified-top-card__primary-description",
    "jobs-unified-top-card__primary-description",
    "top-card-layout__second-subline",
}
CLOSED_PHRASE = "no longer accepting applications"
REPOSTED_RE = re.compile(r"\breposted\b", re.IGNORECASE)
APPLICANT_RE = re.compile(r"\b([\d,]+)\s+applicants\b", re.IGNORECASE)
APPLY_URL_CODE_RE = re.compile(
    r'<code[^>]*id="applyUrl"[^>]*>(.*?)</code>', re.IGNORECASE | re.DOTALL
)
APPLY_URL_VALUE_RE = re.compile(r'(?<=\?url=)[^"&<]+')
MIN_JD_CHARS = 120


class LinkedInDetailError(RuntimeError):
    pass


class _LinkedInPageParser(HTMLParser):
    _VOID_TAGS = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )
    _DESCRIPTION_CLASS = "show-more-less-html__markup"

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._header_depths: list[int] = []
        self._description_depth: int | None = None
        self._header_chunks: list[str] = []
        self._description_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() in self._VOID_TAGS:
            return
        self._depth += 1
        classes = set(str(dict(attrs).get("class") or "").split())
        if classes.intersection(HEADER_CLASS_TOKENS):
            self._header_depths.append(self._depth)
        if self._description_depth is None and self._DESCRIPTION_CLASS in classes:
            self._description_depth = self._depth

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag.casefold() in self._VOID_TAGS:
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, _tag: str) -> None:
        if self._header_depths and self._header_depths[-1] == self._depth:
            self._header_depths.pop()
        if self._description_depth == self._depth:
            self._description_depth = None
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._header_depths:
            self._header_chunks.append(data)
        if self._description_depth is not None:
            self._description_chunks.append(data)

    @staticmethod
    def _clean(chunks: list[str]) -> str:
        return re.sub(r"\s+", " ", " ".join(chunks)).strip()

    @property
    def header_text(self) -> str:
        return self._clean(self._header_chunks)

    @property
    def description(self) -> str:
        return self._clean(self._description_chunks)


@dataclass(frozen=True)
class LinkedInDetailEvidence:
    full_description: str | None
    apply_url: str | None
    apply_method: str
    easy_apply: bool | None
    reposted: bool
    source_status: str | None
    applicant_count: int | None
    header_text: str

    @property
    def facts(self) -> dict[str, object]:
        facts: dict[str, object] = {
            "apply_method": self.apply_method,
            "easy_apply": self.easy_apply,
            "reposted": self.reposted,
            "applicant_count": self.applicant_count,
        }
        if self.source_status:
            facts["source_status"] = self.source_status
        return {key: value for key, value in facts.items() if value is not None}


def classify_linkedin_apply_method(apply_url: str | None, canonical_url: str) -> str:
    direct = str(apply_url or "").strip()
    canonical = str(canonical_url or "").strip()
    if direct and direct != canonical:
        return "external_apply"
    if not direct:
        return "easy_apply"
    return "unknown"


def _extract_apply_url(html: str) -> str | None:
    code_match = APPLY_URL_CODE_RE.search(html)
    if not code_match:
        return None
    value_match = APPLY_URL_VALUE_RE.search(unescape(code_match.group(1)))
    if not value_match:
        return None
    return unquote(value_match.group()).strip() or None


def _exact_applicant_count(header_text: str) -> int | None:
    text = " ".join(str(header_text or "").split())
    for match in APPLICANT_RE.finditer(text):
        prefix = text[max(0, match.start() - 45) : match.start()].casefold().rstrip()
        if prefix.endswith(("first", "over", "more than", "at least")):
            continue
        return int(match.group(1).replace(",", ""))
    return None


def parse_linkedin_detail_html(html: str, *, canonical_url: str) -> LinkedInDetailEvidence:
    parser = _LinkedInPageParser()
    try:
        parser.feed(html or "")
        parser.close()
    except (TypeError, ValueError) as exc:
        raise LinkedInDetailError(f"LinkedIn job page could not be parsed: {exc}") from exc

    header = parser.header_text
    description = parser.description
    if len(description) < MIN_JD_CHARS:
        description = ""
    apply_url = _extract_apply_url(html)
    apply_method = classify_linkedin_apply_method(apply_url, canonical_url)
    closed = CLOSED_PHRASE in header.casefold()
    return LinkedInDetailEvidence(
        full_description=description or None,
        apply_url=apply_url,
        apply_method=apply_method,
        easy_apply=True if apply_method == "easy_apply" else False if apply_method == "external_apply" else None,
        reposted=bool(REPOSTED_RE.search(header)),
        source_status="no_longer_accepting_applications" if closed else None,
        applicant_count=_exact_applicant_count(header),
        header_text=header,
    )


def fetch_linkedin_detail(canonical_url: str) -> LinkedInDetailEvidence:
    url = str(canonical_url or "").strip()
    if not url:
        raise LinkedInDetailError("LinkedIn canonical URL is required")
    timeout = float(get_setting("collection.linkedin_detail_timeout_seconds"))
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = str(response.geturl() or "")
            if "linkedin.com/signup" in final_url:
                raise LinkedInDetailError("LinkedIn redirected the vacancy to sign-up")
            html = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise LinkedInDetailError(f"LinkedIn vacancy HTTP {exc.code}") from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise LinkedInDetailError(f"LinkedIn vacancy request failed: {exc}") from exc
    if not html.strip():
        raise LinkedInDetailError("LinkedIn vacancy returned an empty page")
    return parse_linkedin_detail_html(html, canonical_url=url)
