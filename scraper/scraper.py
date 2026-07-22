import time
import random
import re
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import requests
import os
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs, urljoin

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36',
    # Add more from [24][29]
]

EMAIL_REGEX = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)

CARD_SELECTORS = [
    "div[role='article']",
    "div.Nv2PK",
]

ANCHOR_SELECTORS = [
    "a.hfpxzc",
    "a[href*='/maps/place/']",
]

END_MARKERS = [
    "you've reached the end of the list",
    "you have reached the end of the list",
    "end of the list",
    "no results found",
]

RESOURCE_BLOCKLIST = [
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
    "*.webp",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.otf",
    "*.mp4",
    "*.webm",
]

DEFAULT_WORKERS = int(os.getenv("SCRAPER_WORKERS", "5"))
DEFAULT_EMAIL_WORKERS = int(os.getenv("SCRAPER_EMAIL_WORKERS", "6"))
MAX_WORKERS = int(os.getenv("SCRAPER_MAX_WORKERS", "8"))
MAX_EMAIL_WORKERS = int(os.getenv("SCRAPER_MAX_EMAIL_WORKERS", "14"))

EMAIL_CACHE = {}
EMAIL_CACHE_LOCK = threading.Lock()
_THREAD_LOCAL = threading.local()


def _resolve_driver_path():
    driver_path = ChromeDriverManager().install()
    if os.name == "nt" and not driver_path.lower().endswith(".exe"):
        candidate = Path(driver_path).with_name("chromedriver.exe")
        if candidate.exists():
            driver_path = str(candidate)
    return driver_path


class DriverPool:
    def __init__(self, size, driver_path, page_load_strategy="eager"):
        self.size = max(1, size)
        self.driver_path = driver_path
        self.page_load_strategy = page_load_strategy
        self.queue = queue.Queue()
        self.closed = False
        for _ in range(self.size):
            self.queue.put(_build_driver(driver_path, page_load_strategy=page_load_strategy))

    def acquire(self, timeout=10):
        return self.queue.get(timeout=timeout)

    def release(self, driver):
        if self.closed:
            try:
                driver.quit()
            except Exception:
                pass
            return
        self.queue.put(driver)

    def close(self):
        self.closed = True
        while True:
            try:
                driver = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                driver.quit()
            except Exception:
                pass
            finally:
                self.queue.task_done()


def create_shared_drivers(speed="normal"):
    speed_cfg = _resolve_speed(speed)
    driver_path = _resolve_driver_path()
    workers = min(speed_cfg["workers"], MAX_WORKERS)
    detail_strategy = "none" if speed_cfg["speed"] == "fast" else "eager"
    listing_driver = _build_driver(driver_path, page_load_strategy="eager")
    detail_pool = DriverPool(workers, driver_path, page_load_strategy=detail_strategy)
    return {
        "driver_path": driver_path,
        "listing_driver": listing_driver,
        "detail_pool": detail_pool,
        "detail_strategy": detail_strategy,
        "workers": workers,
    }


class StopScrape(Exception):
    pass


class _LogQueue:
    def __init__(self, log_fn):
        self.log_fn = log_fn
        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = None
        if log_fn:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def write(self, message):
        if not self.log_fn:
            return
        self.queue.put(message)

    def _run(self):
        try:
            from django.db import close_old_connections
            close_old_connections()
        except Exception:
            pass
        while not self.stop_event.is_set() or not self.queue.empty():
            try:
                message = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.log_fn(message)
            except Exception:
                pass
            finally:
                self.queue.task_done()

    def close(self):
        if not self.log_fn or not self.thread:
            return
        self.stop_event.set()
        self.queue.join()
        self.thread.join(timeout=2)


def _safe_call(fn, *args, **kwargs):
    if not fn:
        return None
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def _should_stop(should_stop_fn):
    return bool(_safe_call(should_stop_fn))


def _extract_website(soup):
    selectors = [
        "a[data-item-id='authority']",
        "a[aria-label^='Website']",
        "a[aria-label*='Website']",
        "a[data-tooltip='Website']",
        "a[data-tooltip='Open website']",
    ]
    for selector in selectors:
        el = soup.select_one(selector)
        if el and el.get('href'):
            return el['href'].strip()
    return ""


def _clean_website(url):
    if not url:
        return ""
    if url.startswith("/url?"):
        query = parse_qs(urlparse(url).query)
        if "q" in query and query["q"]:
            return query["q"][0]
    return url


def _clean_phone(text):
    if not text:
        return ""
    return text.replace("\ue0b0", "").replace("Phone:", "").replace("Call", "").strip()


def _extract_phone_from_text(text):
    if not text:
        return ""
    match = re.search(r"(\+?\d[\d\s().-]{6,}\d)", text)
    if match:
        return match.group(1).strip()
    return ""


def _extract_phone(soup):
    el = soup.select_one("[data-item-id^='phone:tel'], [data-item-id^='phone:'], a[href^='tel:']")
    if el:
        href = el.get("href", "")
        if href.lower().startswith("tel:"):
            return href.split(":", 1)[1].strip()
        text = el.get_text(" ", strip=True)
        if text:
            return _clean_phone(text)
        aria = el.get("aria-label", "")
        if aria.lower().startswith("phone:"):
            return _clean_phone(aria.split(":", 1)[1].strip())
        parsed = _extract_phone_from_text(aria)
        if parsed:
            return _clean_phone(parsed)

    for tag in soup.select("[aria-label*='Phone'], [aria-label*='phone'], [aria-label*='Call'], [aria-label*='call']"):
        aria = tag.get("aria-label", "")
        parsed = _extract_phone_from_text(aria)
        if parsed:
            return _clean_phone(parsed)

    tel_link = soup.find("a", href=lambda x: x and x.lower().startswith("tel:"))
    if tel_link:
        return tel_link["href"].split(":", 1)[1].strip()
    return ""


def _sanitize_email(value):
    if not value:
        return ""
    cleaned = value.strip()
    if cleaned.lower().startswith("mailto:"):
        cleaned = cleaned.split("mailto:", 1)[1]
    for splitter in ("?", "#", "&"):
        if splitter in cleaned:
            cleaned = cleaned.split(splitter, 1)[0]
    match = EMAIL_REGEX.search(cleaned)
    if match:
        return match.group(0)
    return ""


def _extract_email(website, session=None):
    if not website:
        return ""
    if session is None:
        session = getattr(_THREAD_LOCAL, "session", None)
        if session is None:
            session = requests.Session()
            _THREAD_LOCAL.session = session
    try:
        headers = {'User-Agent': random.choice(USER_AGENTS)}
        resp = session.get(website, headers=headers, timeout=5)
        wsoup = BeautifulSoup(resp.text, 'html.parser')
        for tag in wsoup.find_all('a', href=True):
            href = tag.get("href", "")
            if "mailto:" in href.lower():
                email = _sanitize_email(href)
                if email:
                    return email
        text = wsoup.get_text(" ", strip=True)
        match = EMAIL_REGEX.search(text)
        if match:
            return match.group(0)
        return ""
    except Exception:
        return ""


def _normalize_place_url(base_url, href):
    if not href:
        return "", ""
    absolute = urljoin(base_url, href)
    normalized = absolute.split("?", 1)[0].rstrip("/")
    return absolute, normalized


def _detect_end_of_list(html):
    text = re.sub(r"\s+", " ", html.lower())
    return any(marker in text for marker in END_MARKERS)


def _pick_card_selector(driver):
    best_selector = CARD_SELECTORS[0]
    best_count = 0
    for selector in CARD_SELECTORS:
        try:
            count = len(driver.find_elements(By.CSS_SELECTOR, selector))
        except Exception:
            count = 0
        if count > best_count:
            best_count = count
            best_selector = selector
    return best_selector


def _count_cards(driver, selector):
    try:
        return len(driver.find_elements(By.CSS_SELECTOR, selector))
    except Exception:
        return 0


def _scroll_feed(driver, feed):
    driver.execute_script(
        "arguments[0].scrollTop = arguments[0].scrollHeight;",
        feed,
    )


def _wait_for_new_cards(driver, selector, prev_count, timeout=8):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, selector)) > prev_count
        )
        return True
    except TimeoutException:
        return False


def _extract_place_summaries(html, base_url):
    soup = BeautifulSoup(html, 'html.parser')
    cards = []
    for selector in CARD_SELECTORS:
        cards = soup.select(selector)
        if cards:
            break

    summaries = []
    for card in cards:
        name = card.get("aria-label", "").strip()
        href = ""
        anchor = None
        for a_selector in ANCHOR_SELECTORS:
            anchor = card.select_one(a_selector)
            if anchor:
                break
        if anchor:
            href = anchor.get("href", "")
            if not name:
                name = anchor.get("aria-label", "").strip() or anchor.get_text(strip=True)

        if not name and not href:
            continue

        place_url, normalized_url = _normalize_place_url(base_url, href)
        summaries.append(
            {
                "name": name,
                "place_url": place_url,
                "normalized_url": normalized_url,
            }
        )
    return summaries


def _build_driver(driver_path, page_load_strategy="eager"):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-features=TranslateUI")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    options.add_argument("--log-level=3")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    options.page_load_strategy = page_load_strategy
    options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")

    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.fonts": 2,
        "profile.default_content_setting_values.notifications": 2,
        "profile.default_content_setting_values.geolocation": 2,
    }
    options.add_experimental_option("prefs", prefs)

    service = Service(driver_path, log_output=os.devnull)
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": RESOURCE_BLOCKLIST})
    except Exception:
        pass
    return driver


def _fetch_place_detail(driver, item, log, retries=3):
    name = item.get("name", "")
    place_url = item.get("place_url", "")
    maps_url = item.get("normalized_url") or place_url
    website = ""
    phone = ""
    email = ""

    if not place_url:
        return {
            'name': name,
            'phone': phone,
            'email': email,
            'website': website,
            'maps_url': maps_url,
        }

    if log:
        log.write(f"Opening business page: {name or place_url}")

    for attempt in range(1, retries + 1):
        try:
            driver.get(place_url)
        except TimeoutException:
            pass

        try:
            WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1"))
            )
        except TimeoutException:
            pass

        detail_soup = BeautifulSoup(driver.page_source, 'html.parser')
        if not name:
            h1 = detail_soup.find("h1")
            if h1:
                name = h1.get_text(strip=True)

        website = _clean_website(_extract_website(detail_soup))
        phone = _clean_phone(_extract_phone(detail_soup))
        if not phone:
            try:
                WebDriverWait(driver, 1.5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[data-item-id^='phone:'], a[href^='tel:']"))
                )
                detail_soup = BeautifulSoup(driver.page_source, 'html.parser')
                phone = _clean_phone(_extract_phone(detail_soup))
            except TimeoutException:
                pass

        if website or phone or name:
            break

        if log:
            log.write(f"Retrying detail load for {place_url} (attempt {attempt + 1}/{retries})")
        time.sleep(random.uniform(0.8, 1.4))

    return {
        'name': name,
        'phone': phone,
        'email': email,
        'website': website,
        'maps_url': maps_url,
    }


def _extract_emails_parallel(results, log, max_workers, on_email_update=None, email_cache=None, email_cache_lock=None):
    if email_cache is None:
        email_cache = EMAIL_CACHE
    if email_cache_lock is None:
        email_cache_lock = EMAIL_CACHE_LOCK
    domain_map = {}
    cached_hits = 0
    for idx, res in enumerate(results):
        website = res.get("website", "")
        if not website:
            continue
        domain = urlparse(website).netloc.lower()
        if not domain:
            continue
        with email_cache_lock:
            cached = email_cache.get(domain, None)
        if cached is not None:
            cached_hits += 1
            if cached:
                results[idx]["email"] = cached
                _safe_call(on_email_update, domain, cached)
            continue
        domain_map.setdefault(domain, {"url": website, "idxs": []})
        domain_map[domain]["idxs"].append(idx)

    if not domain_map:
        return

    workers = min(max_workers, len(domain_map))
    if workers <= 0:
        return

    if log:
        log.write(
            f"Email extraction: {len(domain_map)} unique domains with {workers} workers "
            f"({cached_hits} cached)."
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(_extract_email, item["url"]): domain
            for domain, item in domain_map.items()
        }
        for future in as_completed(future_map):
            domain = future_map[future]
            email = ""
            try:
                email = future.result()
            except Exception:
                email = ""
            if email:
                if log:
                    log.write(f"Email detected: {email}")
                for idx in domain_map[domain]["idxs"]:
                    results[idx]["email"] = email
                _safe_call(on_email_update, domain, email)
            with email_cache_lock:
                email_cache[domain] = email


def _wait_if_paused(should_pause_fn, log):
    if not should_pause_fn:
        return
    paused = _safe_call(should_pause_fn)
    if not paused:
        return
    if log:
        log.write("Job paused. Waiting...")
    while _safe_call(should_pause_fn):
        time.sleep(1)
    if log:
        log.write("Job resumed.")


def _resolve_speed(speed):
    if speed not in {"slow", "normal", "fast"}:
        speed = "normal"

    base_workers = max(1, DEFAULT_WORKERS)
    base_email = max(1, DEFAULT_EMAIL_WORKERS)

    if speed == "slow":
        workers = max(2, base_workers - 2)
        email_workers = max(2, base_email - 2)
        sleep_mult = 1.4
        queue_limit = 120
        log_every = 25
    elif speed == "fast":
        workers = min(MAX_WORKERS, base_workers + 4)
        email_workers = min(MAX_EMAIL_WORKERS, base_email + 6)
        sleep_mult = 0.55
        queue_limit = 320
        log_every = 50
    else:
        workers = min(MAX_WORKERS, base_workers)
        email_workers = min(MAX_EMAIL_WORKERS, base_email)
        sleep_mult = 1.0
        queue_limit = 180
        log_every = 30

    return {
        "speed": speed,
        "workers": workers,
        "email_workers": email_workers,
        "sleep_mult": sleep_mult,
        "queue_limit": queue_limit,
        "log_every": log_every,
    }


def scrape_google_maps(
    search_phrase,
    location,
    domain='com',
    max_results=200,
    log_fn=None,
    should_pause_fn=None,
    should_stop_fn=None,
    on_listings_update=None,
    on_detail_progress=None,
    on_result=None,
    on_email_update=None,
    speed="normal",
    seed_seen=None,
    email_cache=None,
    listing_driver=None,
    detail_pool=None,
    driver_path=None,
    detail_strategy=None,
):
    query = f"{search_phrase} in {location}"
    url = f"https://www.google.{domain}/maps/search/{quote_plus(query)}"

    log = _LogQueue(log_fn)
    stop_event = threading.Event()
    speed_cfg = _resolve_speed(speed)
    log.write(
        f"Starting scrape: {query} (target {max_results}) "
        f"[speed={speed_cfg['speed']}, workers={speed_cfg['workers']}]"
    )

    if not driver_path:
        driver_path = _resolve_driver_path()

    owned_driver = listing_driver is None
    driver = listing_driver or _build_driver(driver_path, page_load_strategy="eager")

    try:
        driver.set_page_load_timeout(45)
        try:
            driver.get(url)
        except TimeoutException:
            pass

        try:
            WebDriverWait(driver, 25).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='feed']"))
            )
        except TimeoutException:
            log.write("Results panel did not load in time.")
            return []

        base_url = f"https://www.google.{domain}"
        place_summaries = []
        seen_keys = set(seed_seen or [])
        no_new_rounds = 0
        max_no_new_rounds = 8
        max_scrolls = max(120, min(1200, max_results * 3))
        selector = _pick_card_selector(driver)
        detail_queue = queue.Queue(maxsize=speed_cfg["queue_limit"])
        prefetch_queue = queue.Queue(maxsize=speed_cfg["queue_limit"])
        listing_done = threading.Event()
        stop_gate = threading.Event()
        results = []
        results_lock = threading.Lock()
        count_lock = threading.Lock()
        counts = {"enqueued": 0, "processed": 0}
        detail_start = time.time()
        prefetch_seen = set()
        prefetch_lock = threading.Lock()

        try:
            feed = driver.find_element(By.CSS_SELECTOR, "div[role='feed']")
        except Exception:
            log.write("Results feed not found.")
            return []

        workers = min(speed_cfg["workers"], MAX_WORKERS, max_results or 1)
        if detail_pool is not None:
            workers = min(workers, detail_pool.size)
        log.write(f"Processing {workers} listings concurrently (pipeline mode).")
        effective_detail_strategy = detail_strategy or ("none" if speed_cfg["speed"] == "fast" else "eager")

        prefetch_workers = min(MAX_EMAIL_WORKERS, max(2, speed_cfg["email_workers"]))
        if prefetch_workers > 0:
            log.write(f"Email prefetchers: {prefetch_workers}")

        def enqueue_email_prefetch(website):
            if not website:
                return
            domain = urlparse(website).netloc.lower()
            if not domain:
                return
            with prefetch_lock:
                if domain in prefetch_seen:
                    return
                prefetch_seen.add(domain)
            try:
                prefetch_queue.put_nowait((domain, website))
            except queue.Full:
                return

        def email_worker():
            while True:
                _wait_if_paused(should_pause_fn, log)
                if _should_stop(should_stop_fn):
                    stop_event.set()
                    stop_gate.set()
                    while True:
                        try:
                            prefetch_queue.get_nowait()
                            prefetch_queue.task_done()
                        except queue.Empty:
                            break
                    break
                try:
                    item = prefetch_queue.get(timeout=0.5)
                except queue.Empty:
                    if listing_done.is_set() or stop_gate.is_set():
                        break
                    continue
                if item is None:
                    prefetch_queue.task_done()
                    break
                domain, website = item
                try:
                    with EMAIL_CACHE_LOCK:
                        cached = email_cache.get(domain) if email_cache is not None else EMAIL_CACHE.get(domain)
                    if cached is not None:
                        continue
                    email = _extract_email(website)
                    with EMAIL_CACHE_LOCK:
                        if email_cache is not None:
                            email_cache[domain] = email
                        else:
                            EMAIL_CACHE[domain] = email
                    if email:
                        if log:
                            log.write(f"Email detected: {email}")
                        _safe_call(on_email_update, domain, email)
                finally:
                    prefetch_queue.task_done()

        prefetch_threads = []
        for _ in range(prefetch_workers):
            t = threading.Thread(target=email_worker, daemon=True)
            prefetch_threads.append(t)
            t.start()

        def worker():
            local_driver = None
            try:
                if detail_pool is not None:
                    try:
                        local_driver = detail_pool.acquire(timeout=8)
                    except queue.Empty:
                        local_driver = _build_driver(driver_path, page_load_strategy=effective_detail_strategy)
                else:
                    local_driver = _build_driver(driver_path, page_load_strategy=effective_detail_strategy)

                try:
                    local_driver.set_page_load_timeout(20 if speed_cfg["speed"] == "fast" else 30)
                except Exception:
                    pass

                while True:
                    _wait_if_paused(should_pause_fn, log)
                    if _should_stop(should_stop_fn):
                        stop_event.set()
                        stop_gate.set()
                        while True:
                            try:
                                detail_queue.get_nowait()
                                detail_queue.task_done()
                            except queue.Empty:
                                break
                        break
                    try:
                        item = detail_queue.get(timeout=0.5)
                    except queue.Empty:
                        if listing_done.is_set() or stop_gate.is_set():
                            break
                        continue
                    if item is None:
                        detail_queue.task_done()
                        break
                    try:
                        result = _fetch_place_detail(local_driver, item, log)
                        website = result.get("website", "")
                        if website:
                            enqueue_email_prefetch(website)
                        with results_lock:
                            results.append(result)
                        _safe_call(on_result, result)
                        with count_lock:
                            counts["processed"] += 1
                            processed = counts["processed"]
                            total_targets = min(max_results, counts["enqueued"])
                        _safe_call(on_detail_progress, processed, total_targets)
                        if processed % speed_cfg["log_every"] == 0 or processed == total_targets:
                            elapsed = max(time.time() - detail_start, 0.1)
                            avg = elapsed / max(processed, 1)
                            remaining = max(total_targets - processed, 0)
                            eta = remaining * avg
                            log.write(
                                f"Processed listings: {processed}/{total_targets} | "
                                f"avg {avg:.2f}s | ETA {eta/60:.1f}m | "
                                f"queue remaining: {detail_queue.qsize()}"
                            )
                    finally:
                        detail_queue.task_done()
            finally:
                if local_driver is not None:
                    if detail_pool is not None:
                        detail_pool.release(local_driver)
                    else:
                        local_driver.quit()

        threads = []
        for _ in range(workers):
            t = threading.Thread(target=worker, daemon=True)
            threads.append(t)
            t.start()

        def enqueue_item(item):
            while True:
                if _should_stop(should_stop_fn):
                    stop_event.set()
                    raise StopScrape("Stop requested")
                try:
                    detail_queue.put(item, timeout=0.5)
                    with count_lock:
                        counts["enqueued"] += 1
                        total_targets = min(max_results, counts["enqueued"])
                    return total_targets
                except queue.Full:
                    _wait_if_paused(should_pause_fn, log)
                    continue

        try:
            for scroll_index in range(max_scrolls):
                if _should_stop(should_stop_fn):
                    stop_event.set()
                    stop_gate.set()
                    log.write("Stop requested. Halting listing collection.")
                    raise StopScrape("Stop requested")
                _wait_if_paused(should_pause_fn, log)

                try:
                    feed_html = feed.get_attribute("innerHTML")
                except StaleElementReferenceException:
                    try:
                        feed = driver.find_element(By.CSS_SELECTOR, "div[role='feed']")
                        feed_html = feed.get_attribute("innerHTML")
                    except Exception:
                        feed_html = driver.page_source

                end_detected = _detect_end_of_list(feed_html)

                summaries = _extract_place_summaries(feed_html, base_url)
                new_count = 0
                for item in summaries:
                    if item["normalized_url"]:
                        key = item["normalized_url"]
                    else:
                        key = f"{item['name'].lower()}|{location.lower()}"

                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    place_summaries.append(item)
                    enqueue_item(item)
                    new_count += 1
                    if len(place_summaries) >= max_results:
                        break

                log.write(f"Loaded listings: {len(place_summaries)} | New listings detected: {new_count}")
                _safe_call(on_listings_update, len(place_summaries), new_count)

                if len(place_summaries) >= max_results:
                    break

                if new_count == 0:
                    no_new_rounds += 1
                else:
                    no_new_rounds = 0

                if end_detected and no_new_rounds >= 2:
                    log.write("End of list detected. Stopping scroll.")
                    break

                if no_new_rounds >= max_no_new_rounds:
                    log.write(f"No new listings after {no_new_rounds} scrolls. Stopping.")
                    break

                prev_count = _count_cards(driver, selector)
                log.write(f"Scrolling results panel... (pass {scroll_index + 1})")
                try:
                    _scroll_feed(driver, feed)
                except StaleElementReferenceException:
                    try:
                        feed = driver.find_element(By.CSS_SELECTOR, "div[role='feed']")
                        _scroll_feed(driver, feed)
                    except Exception:
                        break

                _wait_for_new_cards(driver, selector, prev_count, timeout=8 if speed == "fast" else 10)

                remaining = max_results - len(place_summaries)
                if remaining > 300:
                    sleep_range = (0.5, 1.0)
                elif remaining > 100:
                    sleep_range = (0.9, 1.6)
                else:
                    sleep_range = (1.2, 2.2)
                time.sleep(random.uniform(*sleep_range) * speed_cfg["sleep_mult"])
        finally:
            listing_done.set()
            if not stop_event.is_set():
                for _ in range(workers):
                    try:
                        detail_queue.put(None, timeout=0.5)
                    except queue.Full:
                        break
                for _ in range(prefetch_workers):
                    try:
                        prefetch_queue.put(None, timeout=0.5)
                    except queue.Full:
                        break
            watchdog_start = time.time()
            while True:
                detail_empty = detail_queue.unfinished_tasks == 0
                prefetch_empty = prefetch_queue.unfinished_tasks == 0
                if detail_empty and prefetch_empty:
                    break
                if time.time() - watchdog_start > 60:
                    stop_gate.set()
                    stop_event.set()
                    log.write("Watchdog triggered: forcing queue shutdown.")
                    break
                time.sleep(0.5)

            for t in threads:
                t.join(timeout=5)
            for t in prefetch_threads:
                t.join(timeout=5)

        if stop_event.is_set() or _should_stop(should_stop_fn):
            raise StopScrape("Stop requested")

        results = [r for r in results if r is not None]
        _wait_if_paused(should_pause_fn, log)
        _extract_emails_parallel(
            results,
            log,
            speed_cfg["email_workers"],
            on_email_update=on_email_update,
            email_cache=email_cache,
        )

        return results[:max_results]
    finally:
        if owned_driver:
            driver.quit()
        log.close()
