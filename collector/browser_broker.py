from __future__ import annotations

import atexit
import itertools
import os
import subprocess
import time
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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

from collector.run_logging import collection_logger


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
_page_targets: dict[int, str] = {}
_page_ids = itertools.count(1)


def persistent_browser_ready(timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(
            f"{JMM_BROWSER_CDP_URL}/json/version", timeout=timeout
        ) as response:
            return response.status == 200
    except OSError:
        return False

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
  const pageTitle = document.title.toLowerCase();
  const pageUrl = location.href;
  const actualChallenge = [
    'help us keep seek secure',
    'confirm you are human',
    'verify you are human',
    'just a moment',
    'performing security verification',
    'enable javascript and cookies to continue',
    'access denied'
  ].some(x => low.includes(x) || pageTitle.includes(x));

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
  const noLongerAdvertised = low.includes('this job is no longer advertised');
  const notFound = low.includes('we couldn’t find that page') || low.includes("we couldn't find that page") || pageTitle.includes('404 page not found');
  const technicalError = pageTitle.includes('technical error');
  if (technicalError && !noLongerAdvertised && !notFound) {
    return {
      page_url: pageUrl,
      source_job_id: sourceJobId,
      transient_error: 'technical_error',
      human_check: false
    };
  }
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
    source_status: noLongerAdvertised ? 'no_longer_advertised' : (notFound ? 'not_found' : (job?.status || '')),
    terminal_unavailable: noLongerAdvertised || notFound,
    easy_apply: easyApply,
    apply_method: applyMethod,
    full_description: fullDescription,
    page_url: pageUrl,
    human_check: false
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


def _detach_playwright(*, clear_targets: bool) -> None:
    global _pw_manager, _pw, _browser, _context
    _pages.clear()
    if clear_targets:
        _page_targets.clear()
    _context = None
    _browser = None
    _pw = None
    if _pw_manager is not None:
        with suppress(Exception):
            _pw_manager.stop()
    _pw_manager = None


def close_browser() -> None:
    """Detach this client only; the JMM Chrome service intentionally stays open."""
    _detach_playwright(clear_targets=True)


atexit.register(close_browser)


def _page(page_id: int) -> Page:
    page = _pages.get(int(page_id))
    if page is None or page.is_closed():
        raise BrowserBrokerError(f"stale JMM browser page id: {page_id}")
    return page


def _register_page(page: Page, *, target_url: str | None = None) -> int:
    page_id = next(_page_ids)
    _pages[page_id] = page
    _page_targets[page_id] = str(target_url or page.url or "about:blank")
    return page_id


def _browser_lost(exc: Exception) -> bool:
    text = str(exc).casefold()
    return any(
        marker in text
        for marker in (
            "target page, context or browser has been closed",
            "connection closed while reading from the driver",
            "browser has been closed",
            "connect econnrefused",
            "stale jmm browser page id",
        )
    )


def _recover_browser_pages() -> None:
    global _browser, _context
    targets = dict(_page_targets)
    _pages.clear()
    _browser = None
    _context = None

    # Keep the existing Playwright driver alive. Starting another Sync API runtime
    # while handling an in-flight Playwright exception is invalid.
    if _pw is None:
        raise BrowserBrokerError(
            "persistent JMM browser recovery has no Playwright driver"
        )

    deadline = time.monotonic() + 20.0
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _ensure_browser_service()
            _browser = _pw.chromium.connect_over_cdp(JMM_BROWSER_CDP_URL, timeout=5000)
            if not _browser.contexts:
                raise BrowserBrokerError(
                    "recovered JMM browser exposed no default context"
                )
            _context = _browser.contexts[0]
            _context.add_init_script(_WEBDRIVER_INIT)
            break
        except Exception as exc:  # noqa: BLE001 - bounded recovery loop
            last = exc
            _browser = None
            _context = None
            time.sleep(0.5)
    else:
        raise BrowserBrokerError(f"persistent JMM browser recovery failed: {last}")

    assert _context is not None
    existing = list(_context.pages)
    used: set[int] = set()
    for page_id, target in targets.items():
        page = None
        for index, candidate in enumerate(existing):
            if index in used or candidate.is_closed():
                continue
            if candidate.url == target:
                page = candidate
                used.add(index)
                break
        if page is None:
            page = _context.new_page()
            if target and target != "about:blank":
                page.goto(target, wait_until="domcontentloaded", timeout=30000)
        _pages[page_id] = page


def _execute_browser_command(
    command: str,
    payload: dict[str, Any],
    *,
    page_id: int | None,
    timeout_seconds: int,
) -> Any:
    if command == "list_pages":
        return [
            {
                "pageId": pid,
                "title": page.title(),
                "url": page.url,
                "active": False,
            }
            for pid, page in list(_pages.items())
            if not page.is_closed()
        ]
    if command == "open_tab":
        assert _context is not None
        page = _context.new_page()
        url = str(payload.get("url") or "about:blank")
        pid = _register_page(page, target_url=url)
        if url != "about:blank":
            page.goto(
                url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000
            )
        if bool(payload.get("active")):
            page.bring_to_front()
        return {"ok": True, "pageId": pid, "url": page.url, "title": page.title()}
    if command == "navigate":
        pid = int(page_id or 0)
        page = _page(pid)
        url = str(payload.get("url") or "").strip()
        if not url:
            raise BrowserBrokerError("navigate url is required")
        _page_targets[pid] = url
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
        return {"ok": True, "pageId": pid, "url": page.url}
    if command == "snapshot":
        return _page(int(page_id or 0)).evaluate(
            _SNAPSHOT_JS, bool(payload.get("verbose", True))
        )
    if command == "select_page":
        pid = int(page_id or payload.get("pageId") or 0)
        page = _page(pid)
        if bool(payload.get("bringToFront", True)):
            page.bring_to_front()
        return {"ok": True, "pageId": pid, "url": page.url, "title": page.title()}
    if command == "close_tab":
        pid = int(page_id or payload.get("pageId") or 0)
        page = _page(pid)
        url = page.url
        page.close()
        _pages.pop(pid, None)
        _page_targets.pop(pid, None)
        return {"ok": True, "pageId": pid, "url": url}
    if command == "click":
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
        return {"ok": True, "uid": uid}
    if command == "seek_cards":
        page = _page(int(page_id or 0))
        page.wait_for_selector(
            'article[data-automation="normalJob"], article[data-automation="premiumJob"]',
            timeout=timeout_seconds * 1000,
        )
        return page.evaluate(_SEEK_CARDS_JS)
    if command == "seek_job_detail":
        return _page(int(page_id or 0)).evaluate(_SEEK_DETAIL_JS)
    raise BrowserBrokerError(f"unknown JMM browser command: {command}")


def browser_command(
    command: str,
    payload: dict[str, Any] | None = None,
    *,
    page_id: int | None = None,
    timeout_seconds: int = 30,
    profile: str = "jmm",
    _allow_recovery: bool = True,
) -> BrokerResponse:
    """Compatibility dispatcher backed by JMM's persistent browser service."""
    del profile
    payload = payload or {}
    started = time.perf_counter()
    start_browser()
    try:
        result = _execute_browser_command(
            command, payload, page_id=page_id, timeout_seconds=timeout_seconds
        )
    except PlaywrightTimeoutError as exc:
        raise BrowserBrokerTimeout(
            f"JMM browser {command!r} timed out after {timeout_seconds}s"
        ) from exc
    except BrowserBrokerError as exc:
        if _allow_recovery and _browser_lost(exc):
            collection_logger().warning(
                "browser lost during command=%s page_id=%s; recovering persistent browser",
                command,
                page_id,
            )
            _recover_browser_pages()
            collection_logger().info(
                "browser recovery completed command=%s page_id=%s", command, page_id
            )
            return browser_command(
                command,
                payload,
                page_id=page_id,
                timeout_seconds=timeout_seconds,
                _allow_recovery=False,
            )
        raise
    except PlaywrightError as exc:
        wrapped = BrowserBrokerError(f"JMM browser {command!r} failed: {exc}")
        if _allow_recovery and _browser_lost(wrapped):
            collection_logger().warning(
                "browser lost during command=%s page_id=%s; recovering persistent browser",
                command,
                page_id,
            )
            _recover_browser_pages()
            collection_logger().info(
                "browser recovery completed command=%s page_id=%s", command, page_id
            )
            return browser_command(
                command,
                payload,
                page_id=page_id,
                timeout_seconds=timeout_seconds,
                _allow_recovery=False,
            )
        raise wrapped from exc
    except Exception as exc:
        raise BrowserBrokerError(f"JMM browser {command!r} failed: {exc}") from exc
    return BrokerResponse(result=result, elapsed_seconds=time.perf_counter() - started)


def list_pages() -> BrokerResponse:
    return browser_command("list_pages")


def open_tab(url: str, *, active: bool = False) -> BrokerResponse:
    return browser_command("open_tab", {"url": url, "active": active})


def focus_or_open_tab(url: str, *, host_suffix: str) -> BrokerResponse:
    """Bring an existing persistent-browser tab forward, or open it once if absent."""
    started = time.perf_counter()
    try:
        start_browser()
        assert _context is not None
        suffix = host_suffix.strip().casefold().lstrip(".")
        for page in reversed(_context.pages):
            if page.is_closed():
                continue
            host = (urlsplit(page.url).hostname or "").casefold()
            if host == suffix or host.endswith(f".{suffix}"):
                page.bring_to_front()
                return BrokerResponse(
                    result={"ok": True, "url": page.url, "title": page.title(), "reused": True},
                    elapsed_seconds=time.perf_counter() - started,
                )
        page = _context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.bring_to_front()
        return BrokerResponse(
            result={"ok": True, "url": page.url, "title": page.title(), "reused": False},
            elapsed_seconds=time.perf_counter() - started,
        )
    except BrowserBrokerError:
        raise
    except Exception as exc:
        raise BrowserBrokerError(f"failed to focus/open persistent JMM browser tab: {exc}") from exc


def close_tab(page_id: int) -> BrokerResponse:
    """Close only a tab registered by this JMM broker client."""
    return browser_command(
        "close_tab",
        {"pageId": page_id},
        page_id=page_id,
        _allow_recovery=False,
    )


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
