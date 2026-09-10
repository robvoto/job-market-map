from __future__ import annotations

import atexit
import itertools
import os
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Error as PlaywrightError,
)
from playwright.sync_api import (
    Page,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)


class BrowserBrokerError(RuntimeError):
    pass


class BrowserBrokerTimeout(BrowserBrokerError):
    """The JMM-owned Playwright browser did not answer within its deadline."""


@dataclass(frozen=True)
class BrokerResponse:
    result: Any
    elapsed_seconds: float


ROOT = Path(__file__).resolve().parents[1]
SEEK_PLAYWRIGHT_USER_DATA_DIR = ROOT / "data" / "playwright_jmm_seek_user_data"
_BROWSER_ARGS = ["--disable-blink-features=AutomationControlled"]
_WEBDRIVER_INIT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
)
JMM_BROWSER_CDP_PORT = int(os.environ.get("JMM_BROWSER_CDP_PORT", "9223"))
JMM_BROWSER_CDP_URL = f"http://127.0.0.1:{JMM_BROWSER_CDP_PORT}"
_BROWSER_START_SCRIPT = ROOT / "scripts" / "start_browser_service.sh"

_pw_manager = None
_pw = None
_browser = None
_context = None
_pages: dict[int, Page] = {}
_page_ids = itertools.count(1)

_SNAPSHOT_JS = r"""
(verbose) => {
  const visible = el => {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
  };
  const selectors = [
    'a', 'button', 'input', 'textarea', 'select',
    "[role='button']", "[role='link']", "[role='combobox']", "[role='option']",
    "[role='menuitem']", "[role='tab']", "[tabindex]:not([tabindex='-1'])",
    "[contenteditable='true']"
  ].join(',');
  let seq = 1;
  const elements = [];
  for (const el of document.querySelectorAll(selectors)) {
    if (!visible(el)) continue;
    if (!el.dataset.jmmBrowserId) el.dataset.jmmBrowserId = `jmm-${Date.now()}-${seq++}`;
    const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 500);
    elements.push({
      uid: el.dataset.jmmBrowserId,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      text,
      ariaLabel: el.getAttribute('aria-label') || '',
      name: el.getAttribute('name') || '',
      type: el.getAttribute('type') || '',
      placeholder: el.getAttribute('placeholder') || '',
      value: ('value' in el ? String(el.value || '') : '').slice(0, 500),
      href: el.href || ''
    });
    if (elements.length >= (verbose ? 1000 : 350)) break;
  }
  return {
    title: document.title,
    url: location.href,
    text: (document.body?.innerText || '').replace(/\n{3,}/g, '\n\n').slice(0, verbose ? 80000 : 30000),
    elements
  };
}
"""

_SEEK_DETAIL_JS = r"""
() => {
  const pageText = (document.body?.innerText || '').replace(/\r/g, '');
  const low = pageText.toLowerCase();
  const pageUrl = location.href;
  const actualChallenge = [
    'help us keep seek secure',
    'confirm you are human',
    'verify you are human',
    'just a moment',
    'performing security verification',
    'enable javascript and cookies to continue',
    'access denied'
  ].some(x => low.includes(x) || document.title.toLowerCase().includes(x));

  if (actualChallenge) return {page_url: pageUrl, human_check: true};

  const result = window.SEEK_REDUX_DATA?.jobdetails?.result;
  const job = result?.job || null;
  const lines = pageText.split('\n').map(x => x.trim()).filter(Boolean);
  const textOf = selectors => {
    for (const selector of selectors) {
      const el = document.querySelector(selector);
      const value = (el?.innerText || el?.textContent || '').trim();
      if (value) return value;
    }
    return '';
  };

  const sourceJobId = job?.id != null
    ? String(job.id)
    : (pageUrl.match(/\/job\/(\d+)/)?.[1] || '');
  const title = job?.title || textOf(['[data-automation="job-detail-title"]', 'h1']);
  const employer = job?.advertiser?.name || textOf([
    '[data-automation="advertiser-name"]',
    '[data-automation="job-detail-company"]'
  ]);
  let locationText = job?.location?.label || textOf(['[data-automation="job-detail-location"]']);
  const workType = job?.workTypes?.label || textOf(['[data-automation="job-detail-work-type"]']);
  const salaryText = job?.salary?.label || textOf(['[data-automation="job-detail-salary"]']) ||
    lines.slice(0, 35).find(x => x === 'Salary undisclosed' || /\$/.test(x) || /\b(per hour|per day|per annum|p\.a\.|p\.d\.)\b/i.test(x)) || '';

  const classificationRaw = textOf([
    '[data-automation="job-detail-classifications"]',
    '[data-automation="job-detail-classification"]'
  ]);
  const classification = job?.tracking?.classificationInfo || {};
  let classificationText = classification.classification || '';
  let subclassificationText = classification.subClassification || '';
  if ((!classificationText || !subclassificationText) && classificationRaw) {
    const match = classificationRaw.match(/^(.+?)\s*\((.+)\)$/);
    if (match) {
      subclassificationText = subclassificationText || match[1].trim();
      classificationText = classificationText || match[2].trim();
    } else if (!subclassificationText) {
      subclassificationText = classificationRaw.trim();
    }
  }

  const arrangements = Array.isArray(result?.workArrangements?.arrangements)
    ? result.workArrangements.arrangements
    : [];
  const arrangementLabels = [...new Set(arrangements.map(x => x?.label).filter(Boolean))];
  let workplaceType = arrangementLabels.join(', ') || result?.workArrangements?.label || '';
  if (!workplaceType && locationText) {
    const m = locationText.match(/\((Hybrid|Remote|On-site|On site)\)\s*$/i);
    if (m) {
      workplaceType = m[1].replace(/^On site$/i, 'On-site');
      locationText = locationText.replace(/\s*\([^)]*\)\s*$/, '').trim();
    }
  }

  const isLinkOut = typeof job?.isLinkOut === 'boolean' ? job.isLinkOut : null;
  const quickApplyVisible = lines.some(x => /^Quick apply$/i.test(x));
  const easyApply = isLinkOut === null ? (quickApplyVisible ? true : null) : !isLinkOut;
  const applyMethod = isLinkOut === null
    ? (quickApplyVisible ? 'quick_apply' : '')
    : (isLinkOut ? 'external_apply' : 'quick_apply');

  let fullDescription = '';
  if (typeof job?.content === 'string' && job.content.trim()) {
    const tmp = document.createElement('div');
    tmp.innerHTML = job.content;
    fullDescription = (tmp.innerText || tmp.textContent || '').trim();
  }
  if (!fullDescription) {
    const adEl = document.querySelector('[data-automation="jobAdDetails"]');
    fullDescription = (adEl?.innerText || adEl?.textContent || '').trim();
  }

  return {
    source_job_id: sourceJobId,
    title,
    employer,
    location: locationText,
    classification_text: classificationText,
    subclassification_text: subclassificationText,
    employment_type: workType,
    workplace_type: workplaceType,
    salary_text: salaryText,
    posted_at: job?.listedAt?.dateTimeUtc || '',
    expires_at: job?.expiresAt?.dateTimeUtc || '',
    source_status: job?.status || '',
    easy_apply: easyApply,
    apply_method: applyMethod,
    full_description: fullDescription,
    page_url: pageUrl,
    human_check: !sourceJobId || !fullDescription || fullDescription.length < 80
  };
}
"""

_SEEK_CARDS_JS = r"""
() => {
  const selector = 'article[data-automation="normalJob"], article[data-automation="premiumJob"]';
  const cards = [...document.querySelectorAll(selector)];
  return cards.map(card => {
    const text = (card.innerText || card.textContent || '').trim();
    const textOf = selector => {
      const el = card.querySelector(selector);
      return (el?.innerText || el?.textContent || '').trim();
    };
    const textsOf = selector => [...card.querySelectorAll(selector)]
      .map(el => (el.innerText || el.textContent || '').trim())
      .filter(Boolean);
    const titleEl = card.querySelector('[data-automation="jobTitle"]');
    const href = titleEl?.href || '';
    const sourceJobId = href.match(/\/job\/(\d+)/)?.[1] || '';
    const postedValue = textOf('[data-automation="jobListingDate"]');
    const firstLine = text.split('\n').map(x => x.trim()).find(Boolean) || '';
    const postedText = firstLine.toLowerCase().startsWith('listed ')
      ? firstLine
      : (postedValue ? `Listed ${postedValue.replace(/^Listed\s+/i, '')}` : '');
    const employmentType = text.match(/This is a ([^\n]+?) job/i)?.[1]?.trim() || '';
    const locations = [...new Set(textsOf('[data-automation="jobLocation"]'))];
    let location = locations.join(', ');
    let workplaceType = '';
    const workplaceMatch = location.match(/\((Hybrid|Remote|On-site|On site)\)\s*$/i);
    if (workplaceMatch) {
      workplaceType = workplaceMatch[1].replace(/^On site$/i, 'On-site');
      location = location.replace(/\s*\([^)]*\)\s*$/, '').trim();
    }
    const sub = text.match(/(?:^|\n)subClassification:\s*([^\n]+)/i)?.[1]?.trim() || '';
    const cls = text.match(/(?:^|\n)classification:\s*([^\n]+)/i)?.[1]?.trim() || '';
    const tags = ['New to you', 'Strong applicant', 'Be an early applicant'].filter(tag => text.includes(tag));
    const recruiterMatch = text.match(/(?:^|\n)Recruited by\s*\n([^\n]+)/i);
    return {
      source_job_id: sourceJobId,
      canonical_url: href,
      title: textOf('[data-automation="jobTitle"]'),
      employer: textOf('[data-automation="jobCompany"]'),
      posted_text: postedText,
      employment_type: employmentType,
      location,
      workplace_type: workplaceType,
      salary_text: textOf('[data-automation="jobSalary"]'),
      teaser_text: textOf('[data-automation="jobShortDescription"]'),
      classification_text: cls,
      subclassification_text: sub,
      card_tags: tags,
      recruiter: recruiterMatch?.[1]?.trim() || '',
      raw_card_text: text
    };
  });
}
"""


def _ensure_browser_service() -> None:
    try:
        subprocess.run(
            [str(_BROWSER_START_SCRIPT)],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
            timeout=20,
            env={**os.environ, "JMM_BROWSER_CDP_PORT": str(JMM_BROWSER_CDP_PORT)},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = (
            getattr(exc, "stderr", None) or getattr(exc, "stdout", None) or str(exc)
        )
        raise BrowserBrokerError(
            f"failed to ensure persistent JMM browser service: {detail}"
        ) from exc


def start_browser() -> None:
    """Attach to the long-lived JMM Chrome; do not create a new browser per run."""
    global _pw_manager, _pw, _browser, _context
    if _context is not None:
        return
    SEEK_PLAYWRIGHT_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_browser_service()
    try:
        _pw_manager = sync_playwright()
        _pw = _pw_manager.start()
        _browser = _pw.chromium.connect_over_cdp(JMM_BROWSER_CDP_URL, timeout=10000)
        if not _browser.contexts:
            raise BrowserBrokerError(
                "persistent JMM browser exposed no default context"
            )
        _context = _browser.contexts[0]
        _context.add_init_script(_WEBDRIVER_INIT)
    except Exception as exc:
        close_browser()
        if isinstance(exc, BrowserBrokerError):
            raise
        raise BrowserBrokerError(
            f"failed to attach to persistent JMM browser: {exc}"
        ) from exc


def close_browser() -> None:
    """Detach this client only; the JMM Chrome service intentionally stays open."""
    global _pw_manager, _pw, _browser, _context
    _pages.clear()
    _context = None
    _browser = None
    _pw = None
    if _pw_manager is not None:
        with suppress(Exception):
            _pw_manager.stop()
    _pw_manager = None


atexit.register(close_browser)


def _page(page_id: int) -> Page:
    page = _pages.get(int(page_id))
    if page is None or page.is_closed():
        raise BrowserBrokerError(f"stale JMM browser page id: {page_id}")
    return page


def _register_page(page: Page) -> int:
    page_id = next(_page_ids)
    _pages[page_id] = page
    return page_id


def browser_command(
    command: str,
    payload: dict[str, Any] | None = None,
    *,
    page_id: int | None = None,
    timeout_seconds: int = 30,
    profile: str = "jmm",
) -> BrokerResponse:
    """Compatibility dispatcher backed by JMM's own persistent Playwright browser."""
    del profile
    payload = payload or {}
    started = time.perf_counter()
    start_browser()
    try:
        if command == "list_pages":
            result = [
                {
                    "pageId": pid,
                    "title": page.title(),
                    "url": page.url,
                    "active": False,
                }
                for pid, page in list(_pages.items())
                if not page.is_closed()
            ]
        elif command == "open_tab":
            assert _context is not None
            page = _context.new_page()
            pid = _register_page(page)
            url = str(payload.get("url") or "about:blank")
            if url != "about:blank":
                page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000
                )
            if bool(payload.get("active")):
                page.bring_to_front()
            result = {"ok": True, "pageId": pid, "url": page.url, "title": page.title()}
        elif command == "navigate":
            page = _page(int(page_id or 0))
            url = str(payload.get("url") or "").strip()
            if not url:
                raise BrowserBrokerError("navigate url is required")
            page.goto(
                url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000
            )
            result = {"ok": True, "pageId": int(page_id or 0), "url": page.url}
        elif command == "snapshot":
            page = _page(int(page_id or 0))
            result = page.evaluate(_SNAPSHOT_JS, bool(payload.get("verbose", True)))
        elif command == "select_page":
            page = _page(int(page_id or payload.get("pageId") or 0))
            if bool(payload.get("bringToFront", True)):
                page.bring_to_front()
            result = {
                "ok": True,
                "pageId": int(page_id or payload.get("pageId") or 0),
                "url": page.url,
                "title": page.title(),
            }
        elif command == "click":
            page = _page(int(page_id or 0))
            uid = str(payload.get("uid") or "").strip()
            if not uid:
                raise BrowserBrokerError("click uid is required")
            clicked = page.evaluate(
                """uid => {
                    const el = document.querySelector(`[data-jmm-browser-id=\"${CSS.escape(uid)}\"]`);
                    if (!el) return false;
                    el.scrollIntoView({block: 'center', inline: 'center'});
                    el.focus?.();
                    el.click();
                    return true;
                }""",
                uid,
            )
            if not clicked:
                raise BrowserBrokerError(f"element uid not found: {uid}")
            result = {"ok": True, "uid": uid}
        elif command == "seek_cards":
            page = _page(int(page_id or 0))
            page.wait_for_selector(
                'article[data-automation="normalJob"], article[data-automation="premiumJob"]',
                timeout=timeout_seconds * 1000,
            )
            result = page.evaluate(_SEEK_CARDS_JS)
        elif command == "seek_job_detail":
            page = _page(int(page_id or 0))
            result = page.evaluate(_SEEK_DETAIL_JS)
        else:
            raise BrowserBrokerError(f"unknown JMM browser command: {command}")
    except PlaywrightTimeoutError as exc:
        raise BrowserBrokerTimeout(
            f"JMM browser {command!r} timed out after {timeout_seconds}s"
        ) from exc
    except BrowserBrokerError:
        raise
    except PlaywrightError as exc:
        raise BrowserBrokerError(f"JMM browser {command!r} failed: {exc}") from exc
    except Exception as exc:
        raise BrowserBrokerError(f"JMM browser {command!r} failed: {exc}") from exc
    return BrokerResponse(result=result, elapsed_seconds=time.perf_counter() - started)


def list_pages() -> BrokerResponse:
    return browser_command("list_pages")


def open_tab(url: str, *, active: bool = False) -> BrokerResponse:
    return browser_command("open_tab", {"url": url, "active": active})


def navigate(page_id: int, url: str) -> BrokerResponse:
    return browser_command("navigate", {"url": url}, page_id=page_id)


def _navigation_race(exc: Exception) -> bool:
    text = str(exc).casefold()
    return any(
        marker in text
        for marker in (
            "execution context was destroyed",
            "cannot find context with specified id",
            "most likely because of a navigation",
        )
    )


def snapshot(page_id: int, *, verbose: bool = True) -> BrokerResponse:
    last: Exception | None = None
    for attempt in range(12):
        try:
            return browser_command("snapshot", {"verbose": verbose}, page_id=page_id)
        except BrowserBrokerTimeout as exc:
            last = exc
        except BrowserBrokerError as exc:
            if not _navigation_race(exc):
                raise
            last = exc
        time.sleep(0.25 if attempt < 4 else 0.5)
    assert last is not None
    raise last


def select_page(page_id: int, *, bring_to_front: bool = False) -> BrokerResponse:
    return browser_command(
        "select_page",
        {"pageId": page_id, "bringToFront": bring_to_front},
        page_id=page_id,
    )


def seek_cards(page_id: int) -> BrokerResponse:
    return browser_command("seek_cards", page_id=page_id)


def seek_job_detail(page_id: int) -> BrokerResponse:
    return browser_command("seek_job_detail", page_id=page_id)


def click(page_id: int, uid: str) -> BrokerResponse:
    return browser_command("click", {"uid": uid}, page_id=page_id)
