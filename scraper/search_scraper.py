"""
Modular search-engine scraper.
Supports: Google, Bing, Yahoo, DuckDuckGo, Yandex.
Extracts business names, websites, emails, phones, addresses.
Uses requests + BeautifulSoup (no Selenium required).
"""
import re
import time
import random
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urlparse, urljoin, parse_qs

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_REGEX = re.compile(r"(\+?[\d\s().\-]{7,20})")

SKIP_DOMAINS = {
    "google.com", "google.co", "facebook.com", "twitter.com", "linkedin.com",
    "youtube.com", "instagram.com", "wikipedia.org", "yelp.com", "tripadvisor.com",
    "bing.com", "yahoo.com", "duckduckgo.com", "yandex.com", "amazon.com",
    "reddit.com", "pinterest.com", "tumblr.com", "tiktok.com", "snapchat.com",
    "whatsapp.com", "t.me", "play.google.com", "apps.apple.com",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

_SESSION_LOCAL = threading.local()


class StopScrape(Exception):
    pass


def _get_session():
    if not getattr(_SESSION_LOCAL, "session", None):
        s = requests.Session()
        s.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "DNT": "1",
        })
        _SESSION_LOCAL.session = s
    return _SESSION_LOCAL.session


def _rand_ua():
    return random.choice(USER_AGENTS)


def _fetch(url, timeout=12, extra_headers=None):
    session = _get_session()
    headers = {"User-Agent": _rand_ua()}
    if extra_headers:
        headers.update(extra_headers)
    try:
        resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.debug(f"Fetch failed for {url}: {exc}")
        return ""


def _clean_url(url):
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        return ""
    return url


def _domain_of(url):
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _is_skip_domain(url):
    domain = _domain_of(url)
    for skip in SKIP_DOMAINS:
        if domain == skip or domain.endswith("." + skip):
            return True
    return False


def _extract_email_from_html(html, base_url=""):
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    # Prefer mailto: links
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if "mailto:" in href.lower():
            m = EMAIL_REGEX.search(href)
            if m:
                return m.group(0).lower()
    # Fall back to text scan
    text = soup.get_text(" ", strip=True)
    m = EMAIL_REGEX.search(text)
    if m:
        return m.group(0).lower()
    return ""


def _extract_phone_from_html(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("a", href=True):
        if tag["href"].lower().startswith("tel:"):
            phone = tag["href"][4:].strip()
            if len(phone) >= 7:
                return phone
    text = soup.get_text(" ", strip=True)
    m = PHONE_REGEX.search(text)
    if m:
        p = re.sub(r"[^\d+]", "", m.group(0))
        if len(p) >= 7:
            return m.group(0).strip()
    return ""


def _visit_site_for_details(url, email_cache, email_cache_lock):
    """Visit a website and extract email + phone."""
    domain = _domain_of(url)
    if not domain:
        return "", ""
    with email_cache_lock:
        if domain in email_cache:
            return email_cache[domain].get("email", ""), email_cache[domain].get("phone", "")

    html = _fetch(url, timeout=8)
    email = _extract_email_from_html(html, url)
    phone = _extract_phone_from_html(html)

    # Also try /contact page if no email found
    if not email:
        contact_url = urljoin(url, "/contact")
        contact_html = _fetch(contact_url, timeout=6)
        if contact_html:
            email = _extract_email_from_html(contact_html, contact_url) or email
            phone = phone or _extract_phone_from_html(contact_html)

    with email_cache_lock:
        email_cache[domain] = {"email": email, "phone": phone}
    return email, phone


# ─── Engine-specific SERP parsers ────────────────────────────────────────────

def _parse_google_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    # Main organic results container
    for div in soup.select("div.g, div[data-sokoban-container], div.tF2Cxc"):
        if len(results) >= max_results:
            break
        a = div.select_one("a[href]")
        if not a:
            continue
        url = _clean_url(a.get("href", ""))
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title_el = div.select_one("h3")
        title = title_el.get_text(strip=True) if title_el else _domain_of(url)
        snippet_el = div.select_one("div[data-sncf], span.st, div.VwiC3b")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


def _parse_bing_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for li in soup.select("li.b_algo"):
        if len(results) >= max_results:
            break
        a = li.select_one("h2 a")
        if not a:
            continue
        url = _clean_url(a.get("href", ""))
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True)
        snippet_el = li.select_one("p")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


def _parse_yahoo_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for div in soup.select("div.dd.algo"):
        if len(results) >= max_results:
            break
        a = div.select_one("h3 a, h3.title a")
        if not a:
            a = div.select_one("a[href]")
        if not a:
            continue
        raw_url = a.get("href", "")
        # Yahoo wraps redirect links
        if "yahoo.com" in raw_url:
            parsed = parse_qs(urlparse(raw_url).query)
            raw_url = parsed.get("RU", [raw_url])[0]
        url = _clean_url(raw_url)
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True)
        snippet_el = div.select_one("p.fz-ms, .compText p")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


def _parse_ddg_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for div in soup.select("div.result, div.result__body"):
        if len(results) >= max_results:
            break
        a = div.select_one("a.result__a, h2 a")
        if not a:
            continue
        url = _clean_url(a.get("href", ""))
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True)
        snippet_el = div.select_one("a.result__snippet, .result__snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


def _parse_yandex_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for div in soup.select("li.serp-item, div.organic"):
        if len(results) >= max_results:
            break
        a = div.select_one("a.link_theme_outer, a.OrganicTitle-Link")
        if not a:
            a = div.select_one("h2 a")
        if not a:
            continue
        url = _clean_url(a.get("href", ""))
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True)
        snippet_el = div.select_one("div.text-container, .OrganicTextContentSpan")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


# ─── Per-engine fetchers ──────────────────────────────────────────────────────

def _fetch_google_page(query, page, per_page=10):
    start = page * per_page
    url = f"https://www.google.com/search?q={quote_plus(query)}&num={per_page}&start={start}&hl=en"
    html = _fetch(url, extra_headers={"Referer": "https://www.google.com/"})
    return html


def _fetch_bing_page(query, page, per_page=10):
    first = page * per_page + 1
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={per_page}&first={first}"
    html = _fetch(url, extra_headers={"Referer": "https://www.bing.com/"})
    return html


def _fetch_yahoo_page(query, page, per_page=10):
    b = page * per_page + 1
    url = f"https://search.yahoo.com/search?p={quote_plus(query)}&n={per_page}&b={b}&ei=UTF-8"
    html = _fetch(url, extra_headers={"Referer": "https://search.yahoo.com/"})
    return html


def _fetch_ddg_page(query, page, per_page=10):
    # DuckDuckGo HTML: no pagination parameter, use 'dc' for offset in some APIs
    if page == 0:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    else:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&dc={page * per_page}"
    html = _fetch(url, extra_headers={"Referer": "https://duckduckgo.com/"})
    return html


def _fetch_yandex_page(query, page, per_page=10):
    p = page + 1  # yandex is 1-indexed
    url = f"https://yandex.com/search/?text={quote_plus(query)}&p={p}&lang=en"
    html = _fetch(url, extra_headers={"Referer": "https://yandex.com/"})
    return html


ENGINE_CONFIG = {
    "google": {
        "fetch": _fetch_google_page,
        "parse": _parse_google_results,
        "per_page": 10,
        "delay": (2.0, 4.5),
    },
    "bing": {
        "fetch": _fetch_bing_page,
        "parse": _parse_bing_results,
        "per_page": 10,
        "delay": (1.0, 2.5),
    },
    "yahoo": {
        "fetch": _fetch_yahoo_page,
        "parse": _parse_yahoo_results,
        "per_page": 10,
        "delay": (1.2, 2.8),
    },
    "duckduckgo": {
        "fetch": _fetch_ddg_page,
        "parse": _parse_ddg_results,
        "per_page": 10,
        "delay": (1.0, 2.0),
    },
    "yandex": {
        "fetch": _fetch_yandex_page,
        "parse": _parse_yandex_results,
        "per_page": 10,
        "delay": (1.5, 3.0),
    },
}


def scrape_search_engine(
    search_phrase,
    location="",
    engine="google",
    max_results=100,
    log_fn=None,
    should_pause_fn=None,
    should_stop_fn=None,
    on_result=None,
    on_email_update=None,
    email_cache=None,
    email_cache_lock=None,
    max_email_workers=6,
):
    """
    Scrape a search engine for business leads.
    Returns list of dicts: {name, website, email, phone, address, location, search_query}.
    """
    if email_cache is None:
        email_cache = {}
    if email_cache_lock is None:
        email_cache_lock = threading.Lock()

    cfg = ENGINE_CONFIG.get(engine, ENGINE_CONFIG["google"])
    query = f"{search_phrase} {location}".strip() if location else search_phrase
    per_page = cfg["per_page"]

    def log(msg):
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass

    def check_stop():
        if should_stop_fn:
            try:
                return bool(should_stop_fn())
            except Exception:
                return False
        return False

    def check_pause():
        if should_pause_fn:
            try:
                return bool(should_pause_fn())
            except Exception:
                return False
        return False

    def wait_if_paused():
        if check_pause():
            log("Job paused. Waiting...")
            while check_pause():
                time.sleep(1)
            log("Job resumed.")

    log(f"[{engine.upper()}] Starting search: '{query}' (target {max_results})")

    serp_results = []
    seen_urls = set()
    max_pages = max(1, (max_results // per_page) + 3)

    for page in range(max_pages):
        if check_stop():
            raise StopScrape("Stop requested")
        wait_if_paused()

        if len(serp_results) >= max_results:
            break

        log(f"[{engine.upper()}] Fetching page {page + 1}")
        try:
            html = cfg["fetch"](query, page)
        except Exception as exc:
            log(f"[{engine.upper()}] Fetch error page {page + 1}: {exc}")
            break

        if not html:
            log(f"[{engine.upper()}] Empty response on page {page + 1}, stopping.")
            break

        # Detect CAPTCHA / bot block
        if any(marker in html.lower() for marker in [
            "captcha", "unusual traffic", "blocked", "access denied",
            "robot", "are you a human", "verify you are human"
        ]):
            log(f"[{engine.upper()}] Bot detection triggered on page {page + 1}. Stopping this engine.")
            break

        parsed = cfg["parse"](html, max_results)
        new_count = 0
        for item in parsed:
            url = item.get("website", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                serp_results.append(item)
                new_count += 1
                if len(serp_results) >= max_results:
                    break

        log(f"[{engine.upper()}] Page {page + 1}: +{new_count} results (total {len(serp_results)})")

        if new_count == 0:
            log(f"[{engine.upper()}] No new results found. Stopping pagination.")
            break

        delay_range = cfg["delay"]
        time.sleep(random.uniform(*delay_range))

    log(f"[{engine.upper()}] SERP collection done: {len(serp_results)} URLs. Now extracting contact details...")

    # ── Enrich: visit each site for email + phone ──
    results = []
    results_lock = threading.Lock()
    workers = min(max_email_workers, len(serp_results), 8)

    def enrich(item):
        if check_stop():
            return None
        wait_if_paused()
        url = item.get("website", "")
        email, phone = "", ""
        if url:
            try:
                email, phone = _visit_site_for_details(url, email_cache, email_cache_lock)
            except Exception as exc:
                logger.debug(f"Enrich error {url}: {exc}")
        record = {
            "name": item.get("name", _domain_of(url)),
            "website": url,
            "email": email,
            "phone": phone,
            "address": "",
            "maps_url": "",
            "search_query": query,
            "location": location,
            "source": engine,
        }
        if email and on_email_update:
            try:
                on_email_update(_domain_of(url), email)
            except Exception:
                pass
        if on_result:
            try:
                on_result(record)
            except Exception:
                pass
        return record

    if workers > 0 and serp_results:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(enrich, item): item for item in serp_results}
            done = 0
            for future in as_completed(futures):
                if check_stop():
                    break
                done += 1
                try:
                    record = future.result()
                    if record:
                        with results_lock:
                            results.append(record)
                        if done % 10 == 0:
                            log(f"[{engine.upper()}] Enriched {done}/{len(serp_results)} sites...")
                except Exception as exc:
                    logger.debug(f"Enrich future error: {exc}")
    else:
        for item in serp_results:
            record = enrich(item)
            if record:
                results.append(record)

    emails_found = sum(1 for r in results if r.get("email"))
    log(f"[{engine.upper()}] Done. {len(results)} results, {emails_found} emails found.")
    return results
