"""
Modular search-engine scraper.
Supports: Google, Bing, Yahoo, DuckDuckGo, Yandex, Ecosia, Ask.
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
    "ecosia.org", "ask.com", "startpage.com",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# Country code → Google gl / Accept-Language mapping
COUNTRY_CONFIG = {
    "us": {"gl": "us", "hl": "en", "cr": "countryUS", "lang": "en-US,en;q=0.9"},
    "uk": {"gl": "uk", "hl": "en", "cr": "countryGB", "lang": "en-GB,en;q=0.9"},
    "gb": {"gl": "uk", "hl": "en", "cr": "countryGB", "lang": "en-GB,en;q=0.9"},
    "au": {"gl": "au", "hl": "en", "cr": "countryAU", "lang": "en-AU,en;q=0.9"},
    "ca": {"gl": "ca", "hl": "en", "cr": "countryCA", "lang": "en-CA,en;q=0.9"},
    "in": {"gl": "in", "hl": "en", "cr": "countryIN", "lang": "en-IN,en;q=0.9"},
    "de": {"gl": "de", "hl": "de", "cr": "countryDE", "lang": "de-DE,de;q=0.9,en;q=0.8"},
    "fr": {"gl": "fr", "hl": "fr", "cr": "countryFR", "lang": "fr-FR,fr;q=0.9,en;q=0.8"},
    "es": {"gl": "es", "hl": "es", "cr": "countryES", "lang": "es-ES,es;q=0.9,en;q=0.8"},
    "it": {"gl": "it", "hl": "it", "cr": "countryIT", "lang": "it-IT,it;q=0.9,en;q=0.8"},
    "br": {"gl": "br", "hl": "pt", "cr": "countryBR", "lang": "pt-BR,pt;q=0.9,en;q=0.8"},
    "nl": {"gl": "nl", "hl": "nl", "cr": "countryNL", "lang": "nl-NL,nl;q=0.9,en;q=0.8"},
    "ru": {"gl": "ru", "hl": "ru", "cr": "countryRU", "lang": "ru-RU,ru;q=0.9,en;q=0.8"},
    "jp": {"gl": "jp", "hl": "ja", "cr": "countryJP", "lang": "ja-JP,ja;q=0.9,en;q=0.8"},
    "sg": {"gl": "sg", "hl": "en", "cr": "countrySG", "lang": "en-SG,en;q=0.9"},
    "nz": {"gl": "nz", "hl": "en", "cr": "countryNZ", "lang": "en-NZ,en;q=0.9"},
    "za": {"gl": "za", "hl": "en", "cr": "countryZA", "lang": "en-ZA,en;q=0.9"},
    "ng": {"gl": "ng", "hl": "en", "cr": "countryNG", "lang": "en-NG,en;q=0.9"},
    "ae": {"gl": "ae", "hl": "en", "cr": "countryAE", "lang": "en-AE,en;q=0.9"},
    "pk": {"gl": "pk", "hl": "en", "cr": "countryPK", "lang": "en-PK,en;q=0.9"},
}

# Google tbm (search type) mapping
GOOGLE_SEARCH_TYPES = {
    "web": "",
    "images": "isch",
    "videos": "vid",
    "news": "nws",
}

_SESSION_LOCAL = threading.local()


class StopScrape(Exception):
    pass


def _get_session(country=""):
    if not getattr(_SESSION_LOCAL, "session", None):
        s = requests.Session()
        lang = COUNTRY_CONFIG.get(country, {}).get("lang", "en-US,en;q=0.9")
        s.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": lang,
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        })
        _SESSION_LOCAL.session = s
    return _SESSION_LOCAL.session


def _rand_ua():
    return random.choice(USER_AGENTS)


def _reset_session():
    """Force a fresh session with new headers."""
    _SESSION_LOCAL.session = None


def _fetch(url, timeout=14, extra_headers=None, retries=3):
    """Fetch a URL with retry + jitter on failure. Auto-recovers from blocks."""
    for attempt in range(retries + 1):
        try:
            session = _get_session()
            # Rotate UA on every request, especially on retries
            headers = {
                "User-Agent": _rand_ua(),
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
            if extra_headers:
                headers.update(extra_headers)
            # Progressive jitter on retries
            if attempt > 0:
                wait = random.uniform(8, 20) * attempt
                logger.debug(f"Retry {attempt}/{retries} for {url}, waiting {wait:.0f}s")
                time.sleep(wait)
                # Reset session to get fresh cookies/connection on retry
                _reset_session()
            resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (429, 503, 403):
                wait = random.uniform(30, 60) * (attempt + 1)
                logger.debug(f"Rate limited ({exc.response.status_code}) on {url}. Waiting {wait:.0f}s before retry {attempt+1}")
                time.sleep(wait)
                _reset_session()
            elif exc.response is not None and exc.response.status_code == 404:
                return ""
            else:
                logger.debug(f"HTTP error for {url}: {exc}")
                if attempt < retries:
                    time.sleep(random.uniform(3, 8))
                else:
                    return ""
        except requests.exceptions.ConnectionError as exc:
            logger.debug(f"Connection error for {url}: {exc}")
            if attempt < retries:
                time.sleep(random.uniform(5, 12))
            else:
                return ""
        except Exception as exc:
            logger.debug(f"Fetch failed for {url}: {exc}")
            if attempt < retries:
                time.sleep(random.uniform(2, 6))
            else:
                return ""
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
                email = m.group(0).lower()
                if not any(x in email for x in ["example", "domain", "yourname", "user@", "email@"]):
                    return email
    # Fall back to text scan
    text = soup.get_text(" ", strip=True)
    for m in EMAIL_REGEX.finditer(text):
        email = m.group(0).lower()
        if not any(x in email for x in ["example", "domain", "yourname", "user@", "sentry"]):
            return email
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


def _visit_site_for_details(url, email_cache, email_cache_lock, visit_pages=True):
    """Visit a website and extract email + phone."""
    domain = _domain_of(url)
    if not domain:
        return "", ""
    with email_cache_lock:
        if domain in email_cache:
            return email_cache[domain].get("email", ""), email_cache[domain].get("phone", "")

    email, phone = "", ""
    if visit_pages:
        html = _fetch(url, timeout=10)
        email = _extract_email_from_html(html, url)
        phone = _extract_phone_from_html(html)

        # Also try /contact and /about pages if no email found
        if not email:
            for path in ["/contact", "/contact-us", "/about", "/about-us"]:
                contact_url = urljoin(url, path)
                try:
                    contact_html = _fetch(contact_url, timeout=7, retries=0)
                    if contact_html:
                        email = _extract_email_from_html(contact_html, contact_url) or email
                        phone = phone or _extract_phone_from_html(contact_html)
                        if email:
                            break
                except Exception:
                    pass

    with email_cache_lock:
        email_cache[domain] = {"email": email, "phone": phone}
    return email, phone


def _is_captcha(html):
    """Detect if response is a CAPTCHA or bot block page."""
    if not html:
        return False
    lower = html.lower()
    markers = [
        "captcha", "unusual traffic", "access denied", "our systems have detected",
        "robot", "are you a human", "verify you are human", "i'm not a robot",
        "please verify", "suspicious activity", "automated queries",
        "blocked", "too many requests",
    ]
    return any(m in lower for m in markers)


# ─── Engine-specific SERP parsers ────────────────────────────────────────────

def _parse_google_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for div in soup.select("div.g, div[data-sokoban-container], div.tF2Cxc, div.Gx5Zad"):
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
        snippet_el = div.select_one("div[data-sncf], span.st, div.VwiC3b, div.lEBKkf")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


def _parse_google_images_results(html, max_results):
    """Extract website URLs from Google Images (sites behind the images)."""
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    # Image result links often have data-ou (original URL)
    for tag in soup.find_all(attrs={"data-ou": True}):
        url = _clean_url(tag.get("data-ou", ""))
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title = tag.get("alt", "") or _domain_of(url)
        results.append({"name": title, "website": url, "snippet": ""})
        if len(results) >= max_results:
            break
    # Fallback: look for links in JSON-like data
    if not results:
        for m in re.finditer(r'"ou":"(https?://[^"]+)"', html):
            url = _clean_url(m.group(1))
            if url and not _is_skip_domain(url) and url not in seen:
                seen.add(url)
                results.append({"name": _domain_of(url), "website": url, "snippet": ""})
            if len(results) >= max_results:
                break
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
        snippet_el = li.select_one("p, .b_caption p")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


def _parse_yahoo_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for div in soup.select("div.dd.algo, li[class*='first'], div[class*='algo']"):
        if len(results) >= max_results:
            break
        a = div.select_one("h3 a, h3.title a, a.ac-algo-ftr-b")
        if not a:
            a = div.select_one("a[href]")
        if not a:
            continue
        raw_url = a.get("href", "")
        if "yahoo.com" in raw_url:
            parsed = parse_qs(urlparse(raw_url).query)
            raw_url = parsed.get("RU", [raw_url])[0]
        url = _clean_url(raw_url)
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True)
        snippet_el = div.select_one("p.fz-ms, .compText p, p")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


def _parse_ddg_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for div in soup.select("div.result, div.result__body, article[data-testid]"):
        if len(results) >= max_results:
            break
        a = div.select_one("a.result__a, h2 a, a[data-testid='result-title-a']")
        if not a:
            continue
        url = _clean_url(a.get("href", ""))
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True)
        snippet_el = div.select_one("a.result__snippet, .result__snippet, span[data-testid='result-snippet']")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


def _parse_yandex_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for div in soup.select("li.serp-item, div.organic, div[class*='organic']"):
        if len(results) >= max_results:
            break
        a = div.select_one("a.link_theme_outer, a.OrganicTitle-Link, h2 a")
        if not a:
            continue
        url = _clean_url(a.get("href", ""))
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True)
        snippet_el = div.select_one("div.text-container, .OrganicTextContentSpan, .organic__text")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


def _parse_ecosia_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for article in soup.select("article.result, div.result-item, div[class*='result--web']"):
        if len(results) >= max_results:
            break
        a = article.select_one("a.result-url, a[class*='result-title'], h2 a, a[data-result-url]")
        if not a:
            a = article.select_one("a[href]")
        if not a:
            continue
        url = _clean_url(a.get("href", ""))
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title_el = article.select_one("h2, a[class*='title'], .result-title")
        title = title_el.get_text(strip=True) if title_el else _domain_of(url)
        snippet_el = article.select_one("p, .result-snippet, [class*='snippet']")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


def _parse_ask_results(html, max_results):
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for div in soup.select("div.PartialSearchResults-item, div[class*='result'], li[class*='result']"):
        if len(results) >= max_results:
            break
        a = div.select_one("a.PartialSearchResults-item-title-link, h2 a, a[href]")
        if not a:
            continue
        url = _clean_url(a.get("href", ""))
        if not url or _is_skip_domain(url) or url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True)
        snippet_el = div.select_one("p, .PartialSearchResults-item-abstract")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"name": title, "website": url, "snippet": snippet})
    return results


# ─── Per-engine fetchers ──────────────────────────────────────────────────────

def _fetch_google_page(query, page, per_page=10, country="", search_type="web"):
    start = page * per_page
    tbm = GOOGLE_SEARCH_TYPES.get(search_type, "")
    cfg = COUNTRY_CONFIG.get(country, {})
    gl = cfg.get("gl", "")
    hl = cfg.get("hl", "en")
    cr = cfg.get("cr", "")
    params = f"q={quote_plus(query)}&num={per_page}&start={start}&hl={hl}"
    if gl:
        params += f"&gl={gl}"
    if cr:
        params += f"&cr={cr}"
    if tbm:
        params += f"&tbm={tbm}"
    url = f"https://www.google.com/search?{params}"
    lang_header = cfg.get("lang", "en-US,en;q=0.9")
    return _fetch(url, extra_headers={
        "Referer": "https://www.google.com/",
        "Accept-Language": lang_header,
    })


def _fetch_bing_page(query, page, per_page=10, country="", search_type="web"):
    first = page * per_page + 1
    mkt = ""
    if country in ("us",): mkt = "en-US"
    elif country in ("uk", "gb"): mkt = "en-GB"
    elif country in ("au",): mkt = "en-AU"
    elif country in ("in",): mkt = "en-IN"
    elif country in ("ca",): mkt = "en-CA"
    mkt_param = f"&mkt={mkt}" if mkt else ""
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={per_page}&first={first}{mkt_param}"
    return _fetch(url, extra_headers={"Referer": "https://www.bing.com/"})


def _fetch_yahoo_page(query, page, per_page=10, country="", search_type="web"):
    b = page * per_page + 1
    url = f"https://search.yahoo.com/search?p={quote_plus(query)}&n={per_page}&b={b}&ei=UTF-8"
    return _fetch(url, extra_headers={"Referer": "https://search.yahoo.com/"})


def _fetch_ddg_page(query, page, per_page=10, country="", search_type="web"):
    if page == 0:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    else:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&dc={page * per_page}"
    return _fetch(url, extra_headers={"Referer": "https://duckduckgo.com/"})


def _fetch_yandex_page(query, page, per_page=10, country="", search_type="web"):
    p = page + 1
    url = f"https://yandex.com/search/?text={quote_plus(query)}&p={p}&lang=en"
    return _fetch(url, extra_headers={"Referer": "https://yandex.com/"})


def _fetch_ecosia_page(query, page, per_page=10, country="", search_type="web"):
    p = page * per_page
    url = f"https://www.ecosia.org/search?method=index&q={quote_plus(query)}&p={page}"
    return _fetch(url, extra_headers={"Referer": "https://www.ecosia.org/"})


def _fetch_ask_page(query, page, per_page=10, country="", search_type="web"):
    url = f"https://www.ask.com/web?q={quote_plus(query)}&page={page + 1}"
    return _fetch(url, extra_headers={"Referer": "https://www.ask.com/"})


ENGINE_CONFIG = {
    "google": {
        "fetch": _fetch_google_page,
        "parse_web": _parse_google_results,
        "parse_images": _parse_google_images_results,
        "per_page": 10,
        "delay": (3.0, 6.0),
        "captcha_wait": (45, 90),
    },
    "bing": {
        "fetch": _fetch_bing_page,
        "parse_web": _parse_bing_results,
        "per_page": 10,
        "delay": (1.5, 3.5),
        "captcha_wait": (20, 40),
    },
    "yahoo": {
        "fetch": _fetch_yahoo_page,
        "parse_web": _parse_yahoo_results,
        "per_page": 10,
        "delay": (1.5, 3.0),
        "captcha_wait": (20, 40),
    },
    "duckduckgo": {
        "fetch": _fetch_ddg_page,
        "parse_web": _parse_ddg_results,
        "per_page": 10,
        "delay": (1.0, 2.5),
        "captcha_wait": (15, 30),
    },
    "yandex": {
        "fetch": _fetch_yandex_page,
        "parse_web": _parse_yandex_results,
        "per_page": 10,
        "delay": (2.0, 4.0),
        "captcha_wait": (30, 60),
    },
    "ecosia": {
        "fetch": _fetch_ecosia_page,
        "parse_web": _parse_ecosia_results,
        "per_page": 10,
        "delay": (1.5, 3.0),
        "captcha_wait": (20, 40),
    },
    "ask": {
        "fetch": _fetch_ask_page,
        "parse_web": _parse_ask_results,
        "per_page": 10,
        "delay": (1.5, 3.0),
        "captcha_wait": (20, 40),
    },
}


def scrape_search_engine(
    search_phrase,
    location="",
    engine="google",
    max_results=100,
    country="",
    search_type="web",
    visit_pages=True,
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

    # Choose the right parser based on search_type
    if search_type == "images" and "parse_images" in cfg:
        parse_fn = cfg["parse_images"]
    else:
        parse_fn = cfg["parse_web"]

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
                if check_stop():
                    return
                time.sleep(1)
            log("Job resumed.")

    country_label = f" [{country.upper()}]" if country else ""
    type_label = f" ({search_type})" if search_type != "web" else ""
    log(f"[{engine.upper()}]{country_label}{type_label} Starting search: '{query}' (target {max_results})")

    serp_results = []
    seen_urls = set()
    max_pages = max(1, (max_results // per_page) + 4)
    captcha_retries = 0
    max_captcha_retries = 2

    for page in range(max_pages):
        if check_stop():
            raise StopScrape("Stop requested")
        wait_if_paused()

        if len(serp_results) >= max_results:
            break

        log(f"[{engine.upper()}] Fetching page {page + 1}…")
        try:
            html = cfg["fetch"](query, page, per_page=per_page, country=country, search_type=search_type)
        except Exception as exc:
            log(f"[{engine.upper()}] Fetch error page {page + 1}: {exc}")
            break

        if not html:
            log(f"[{engine.upper()}] Empty response on page {page + 1}, stopping.")
            break

        # CAPTCHA / bot block detection with auto-recovery
        if _is_captcha(html):
            if captcha_retries < max_captcha_retries:
                captcha_retries += 1
                # Exponential backoff: longer waits on each retry
                base_wait = cfg.get("captcha_wait", (30, 60))
                wait_sec = random.uniform(base_wait[0], base_wait[1]) * captcha_retries
                log(f"[{engine.upper()}] ⚠️ Bot detection on page {page + 1}. Auto-recovering — waiting {wait_sec:.0f}s (attempt {captcha_retries}/{max_captcha_retries})…")
                time.sleep(wait_sec)
                # Reset session to clear tracking cookies
                _SESSION_LOCAL.session = None
                # Retry with a fresh session
                try:
                    html = cfg["fetch"](query, page, per_page=per_page, country=country, search_type=search_type)
                except Exception:
                    html = ""
                if not html or _is_captcha(html):
                    if captcha_retries >= max_captcha_retries:
                        log(f"[{engine.upper()}] Still blocked after {max_captcha_retries} auto-recovery attempts. Stopping.")
                        break
                    # Continue loop — will try again next iteration
                    continue
                else:
                    log(f"[{engine.upper()}] ✅ Auto-recovered from bot detection. Continuing…")
                    captcha_retries = 0  # Reset counter on success
            else:
                log(f"[{engine.upper()}] Bot detection — max retries reached. Moving on.")
                break

        parsed = parse_fn(html, max_results)
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

        if new_count == 0 and page > 0:
            log(f"[{engine.upper()}] No new results. Stopping pagination.")
            break

        # Human-like delay between pages
        base_delay = cfg.get("delay", (2.0, 4.0))
        delay = random.uniform(*base_delay)
        # Occasionally add a longer pause to avoid patterns
        if random.random() < 0.15:
            delay += random.uniform(3, 8)
        log(f"[{engine.upper()}] Waiting {delay:.1f}s before next page…")
        time.sleep(delay)

    log(f"[{engine.upper()}] SERP done: {len(serp_results)} URLs. {'Visiting sites for contacts…' if visit_pages else 'Skipping site visits.'}")

    # ── Enrich: visit each site for email + phone ──
    results = []
    results_lock = threading.Lock()
    workers = min(max_email_workers, max(1, len(serp_results)), 8)

    def enrich(item):
        if check_stop():
            return None
        wait_if_paused()
        url = item.get("website", "")
        email, phone = "", ""
        if url:
            try:
                email, phone = _visit_site_for_details(url, email_cache, email_cache_lock, visit_pages=visit_pages)
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
            "snippet": item.get("snippet", ""),
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
                        if done % 5 == 0:
                            log(f"[{engine.upper()}] Enriched {done}/{len(serp_results)} sites…")
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
