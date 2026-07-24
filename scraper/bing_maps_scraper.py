"""
Bing Maps scraper — Selenium-based, mirrors the Google Maps scraper interface.
Extracts: business name, phone, address, website, and email (via site visit).
"""
import time
import random
import re
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urlparse, urljoin

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import requests
import os
import shutil
import json
from pathlib import Path

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0',
]

EMAIL_REGEX = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_REGEX = re.compile(r"(\+?[\d\s().\-]{7,20})")

# Result selectors to try in order (Bing Maps keeps updating their DOM)
RESULT_SELECTORS = [
    "div.b_split_card[role='listitem']",
    "[role='listitem'] [data-entity]",
    "[role='listitem'][data-type='Business']",
    "li.item",
    "li[data-task-id]",
    "li.taskItem",
    "ul#results_list li",
    "div.listings-container li",
    "li.b_top",
    "div.entity-listing-item",
    "li[class*='item']",
]

# Container selectors for the results panel
CONTAINER_SELECTORS = [
    "#results_list",
    "ul.taskPanelResultsList",
    "div.listings-container ul",
    "div.taskPanelContainer ul",
    "#tabPanel ul",
    "div[class*='results'] ul",
]

RESOURCE_BLOCKLIST = [
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp",
    "*.woff", "*.woff2", "*.ttf", "*.mp4", "*.webm",
]

EMAIL_CACHE = {}
EMAIL_CACHE_LOCK = threading.Lock()
_THREAD_LOCAL = threading.local()


class StopScrape(Exception):
    pass


def _resolve_driver_path():
    # Detect installed Firefox + geckodriver or fallback to Chrome driver.
    geckodriver = shutil.which("geckodriver")
    firefox = shutil.which("firefox")
    if geckodriver and firefox:
        return "firefox", geckodriver

    driver_path = ChromeDriverManager().install()
    if os.name == "nt" and not driver_path.lower().endswith(".exe"):
        candidate = Path(driver_path).with_name("chromedriver.exe")
        if candidate.exists():
            driver_path = str(candidate)
    return "chrome", driver_path


def _build_driver(driver_path, page_load_strategy="eager"):
    if isinstance(driver_path, tuple):
        browser, executable_path = driver_path
    else:
        browser, executable_path = "chrome", driver_path

    if browser == "firefox":
        options = FirefoxOptions()
        options.add_argument("--headless")
        options.add_argument("--width=1366")
        options.add_argument("--height=900")
        options.page_load_strategy = page_load_strategy
        options.set_preference("permissions.default.desktop-notification", 2)
        options.set_preference("permissions.default.geo", 2)
        options.set_preference("dom.webnotifications.enabled", False)
        options.set_preference("browser.display.use_document_fonts", 0)
        service = FirefoxService(executable_path=executable_path, log_output=os.devnull)
        return webdriver.Firefox(service=service, options=options)

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-sync")
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


def _safe_call(fn, *args, **kwargs):
    if not fn:
        return None
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def _should_stop(should_stop_fn):
    return bool(_safe_call(should_stop_fn))


def _wait_if_paused(should_pause_fn, log_fn):
    if not should_pause_fn:
        return
    if not _safe_call(should_pause_fn):
        return
    if log_fn:
        log_fn("[BingMaps] Job paused. Waiting...")
    while _safe_call(should_pause_fn):
        time.sleep(1)
    if log_fn:
        log_fn("[BingMaps] Job resumed.")


def _extract_email_from_site(website, session=None):
    """Visit a website and extract the first email address found."""
    if not website:
        return ""
    if session is None:
        session = getattr(_THREAD_LOCAL, "session", None)
        if session is None:
            session = requests.Session()
            _THREAD_LOCAL.session = session
    try:
        headers = {'User-Agent': random.choice(USER_AGENTS)}
        resp = session.get(website, headers=headers, timeout=8, allow_redirects=True)
        soup = BeautifulSoup(resp.text, 'html.parser')
        # Prefer mailto links
        for tag in soup.find_all('a', href=True):
            href = tag.get("href", "")
            if "mailto:" in href.lower():
                m = EMAIL_REGEX.search(href)
                if m:
                    email = m.group(0).lower()
                    if not any(x in email for x in ["example", "domain", "yourname"]):
                        return email
        # Text scan
        text = soup.get_text(" ", strip=True)
        m = EMAIL_REGEX.search(text)
        if m:
            email = m.group(0).lower()
            if not any(x in email for x in ["example", "domain", "yourname"]):
                return email
        # Try /contact page
        for path in ["/contact", "/contact-us", "/about"]:
            try:
                contact_url = urljoin(website, path)
                resp2 = session.get(contact_url, headers=headers, timeout=6, allow_redirects=True)
                soup2 = BeautifulSoup(resp2.text, 'html.parser')
                for tag in soup2.find_all('a', href=True):
                    href = tag.get("href", "")
                    if "mailto:" in href.lower():
                        m = EMAIL_REGEX.search(href)
                        if m:
                            return m.group(0).lower()
                text2 = soup2.get_text(" ", strip=True)
                m = EMAIL_REGEX.search(text2)
                if m:
                    return m.group(0).lower()
            except Exception:
                pass
    except Exception:
        pass
    return ""


def _extract_phone_from_text(text):
    """Extract a phone number from text."""
    if not text:
        return ""
    m = PHONE_REGEX.search(text)
    if m:
        p = re.sub(r"[^\d+]", "", m.group(0))
        if len(p) >= 7:
            return m.group(0).strip()
    return ""


def _find_result_container(driver):
    """Find the results list container trying multiple selectors."""
    for sel in CONTAINER_SELECTORS:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el:
                return el, sel
        except Exception:
            pass
    return None, None


def _find_result_items(driver, container=None):
    """Find all result items using multiple selector strategies."""
    if container:
        for sel in ["li.item", "li", "div.entity-listing-item"]:
            try:
                items = container.find_elements(By.CSS_SELECTOR, sel)
                if items:
                    return items
            except Exception:
                pass
    # Fall back to global selectors
    for sel in RESULT_SELECTORS:
        try:
            items = driver.find_elements(By.CSS_SELECTOR, sel)
            if len(items) >= 1:
                return items
        except Exception:
            pass
    return []


def _has_business_cards(driver):
    """Return true only after Bing has rendered populated business cards."""
    return bool(_find_result_items(driver))


def _submit_search(driver, query, log_fn):
    """Submit a search through the current Bing Maps UI.

    Bing Maps now renders its application shell for a URL containing `q`,
    then waits for the search-box event before creating result cards.
    """
    selectors = [
        "#searchBoxInput",
        "input[name='searchbox']",
        "input[role='combobox']",
    ]
    search_input = None
    for selector in selectors:
        try:
            search_input = WebDriverWait(driver, 12).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            if search_input:
                break
        except Exception:
            continue
    if not search_input:
        raise RuntimeError("Bing Maps search box was not available.")

    search_input.click()
    search_input.clear()
    search_input.send_keys(query)
    search_input.send_keys(Keys.ENTER)
    if log_fn:
        log_fn(f"[BingMaps] Submitted search for '{query}'.")


def _extract_detail_from_panel(driver, log_fn):
    """After clicking a result, extract details from the info panel."""
    name = ""
    phone = ""
    address = ""
    website = ""

    # Wait briefly for detail panel
    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.entity-carousel-item, div.infocard, div.taskPanelDetail, h2.detail-title, div.detail-title, div.cs-detail"))
        )
    except TimeoutException:
        pass

    soup = BeautifulSoup(driver.page_source, 'html.parser')

    # Extract name from multiple possible elements
    for sel in ["h2.detail-title", "div.detail-title", "h1.title", "h2.title",
                "span.title", ".cs-detail h2", ".entity-name", "div.infocard h2"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            name = el.get_text(strip=True)
            break

    # Extract phone
    phone_el = soup.select_one("a[href^='tel:']")
    if phone_el:
        phone = phone_el.get("href", "").replace("tel:", "").strip()
    if not phone:
        for sel in ["span.b_phoneNum", "div.phone", ".detail-phone", "a[data-type='phone']"]:
            el = soup.select_one(sel)
            if el:
                phone = _extract_phone_from_text(el.get_text(strip=True))
                if phone:
                    break

    # Extract address
    for sel in ["div.detail-list div.b_address", "span.address", "div.address",
                ".detail-address", "div[data-type='address']", "p.address"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            address = el.get_text(strip=True)
            break

    # Extract website
    for sel in ["a[data-type='website']", "a.website-link", "a[aria-label*='ebsite']",
                "a[href*='http']:not([href*='bing.com']):not([href*='microsoft.com'])"]:
        el = soup.select_one(sel)
        if el and el.get("href"):
            href = el.get("href", "")
            if href.startswith("http") and "bing.com" not in href and "microsoft.com" not in href:
                website = href.split("?")[0].rstrip("/")
                break

    return {"name": name, "phone": phone, "address": address, "website": website}


def _extract_from_list_item(item_el):
    """Extract basic info directly from a list item element (without clicking)."""
    try:
        html = item_el.get_attribute("outerHTML")
        soup = BeautifulSoup(html, 'html.parser')

        name = ""
        phone = ""
        address = ""
        website = ""

        # Current Bing Maps cards embed canonical business data in JSON.
        entity_el = soup.select_one("[data-entity]")
        if entity_el:
            try:
                payload = json.loads(entity_el.get("data-entity", "{}"))
                entity = payload.get("entity", payload)
                name = str(entity.get("title") or "").strip()
                phone = str(entity.get("phone") or "").strip()
                address = str(entity.get("address") or "").strip()
                website = str(entity.get("website") or "").strip()
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

        for sel in ["a.text-title", "div.title", "h5", "strong", "a.b_title",
                    "div.entity-title", "span.name", "a[class*='title']",
                    "h3.l_magTitle"]:
            if name:
                break
            el = soup.select_one(sel)
            if el and el.get_text(strip=True):
                name = el.get_text(strip=True)
                break
        if not name:
            for a in soup.find_all("a"):
                txt = a.get_text(strip=True)
                if txt and len(txt) > 2 and "bing" not in a.get("href", "").lower():
                    name = txt
                    break

        phone_el = soup.select_one("a[href^='tel:']")
        if phone_el and not phone:
            phone = phone_el.get("href", "").replace("tel:", "").strip()
        if not phone:
            text = soup.get_text(" ")
            phone = _extract_phone_from_text(text)

        for sel in ["div.address", "span.address", "div.b_address", "p.address", "div[class*='address']"]:
            if address:
                break
            el = soup.select_one(sel)
            if el and el.get_text(strip=True):
                address = el.get_text(strip=True)
                break

        if not website:
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if href.startswith("http") and "bing.com" not in href and "microsoft.com" not in href:
                    website = href.split("?")[0].rstrip("/")
                    break

        return {"name": name, "phone": phone, "address": address, "website": website}
    except Exception:
        return {"name": "", "phone": "", "address": "", "website": ""}


def scrape_bing_maps(
    search_phrase,
    location,
    max_results=200,
    log_fn=None,
    should_pause_fn=None,
    should_stop_fn=None,
    on_result=None,
    on_email_update=None,
    speed="normal",
    seed_seen=None,
    email_cache=None,
    email_cache_lock=None,
    driver_path=None,
):
    """
    Scrape Bing Maps for business listings.
    Returns list of dicts: {name, phone, email, website, address, maps_url, location}.
    """
    if email_cache is None:
        email_cache = EMAIL_CACHE
    if email_cache_lock is None:
        email_cache_lock = EMAIL_CACHE_LOCK

    query = f"{search_phrase} {location}".strip() if location else search_phrase
    # Load the application shell first; the current UI requires a real input
    # event before it creates result cards.
    url = "https://www.bing.com/maps?setlang=en"

    def log(msg):
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass

    seen_keys = set(seed_seen or [])
    results = []
    results_lock = threading.Lock()

    log(f"[BingMaps] Starting: '{query}' (target {max_results})")

    if not driver_path:
        driver_path = _resolve_driver_path()

    driver = _build_driver(driver_path, page_load_strategy="eager")

    try:
        driver.set_page_load_timeout(40)
        try:
            driver.get(url)
        except TimeoutException:
            pass

        # Accept any cookie consent dialogs
        for consent_sel in ["button#bnp_btn_accept", "button[aria-label*='Accept']", "button.b_dismiss"]:
            try:
                btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.CSS_SELECTOR, consent_sel)))
                btn.click()
                time.sleep(0.5)
                break
            except Exception:
                pass

        _submit_search(driver, query, log)

        # Wait for actual business cards, not just the application shell.
        try:
            WebDriverWait(driver, 20).until(
                _has_business_cards
            )
        except TimeoutException:
            # Some Firefox runs need one additional input event while Bing's
            # client-side map view finishes loading. Retry once, bounded, so
            # a slow render is not reported as a false zero-result job.
            log("[BingMaps] Search submitted, but business cards did not render; retrying once.")
            try:
                _submit_search(driver, query, log)
                WebDriverWait(driver, 12).until(_has_business_cards)
            except TimeoutException:
                log("[BingMaps] Search completed without business cards. Bing Maps may have returned no matches or changed its UI.")
                return []

        time.sleep(random.uniform(1.0, 2.0))

        # Speed settings — more workers = faster email enrichment
        speed_cfg = {
            "slow": {"workers": 4, "delay": (1.2, 2.5)},
            "normal": {"workers": 8, "delay": (0.6, 1.4)},
            "fast": {"workers": 14, "delay": (0.3, 0.8)},
        }.get(speed, {"workers": 8, "delay": (0.6, 1.4)})

        max_email_workers = speed_cfg["workers"] + 2
        log(f"[BingMaps] Results detected. Collecting up to {max_results} listings...")

        collected_items = []
        seen_names = set()
        scroll_attempts = 0
        no_new_rounds = 0
        max_scroll_attempts = max(120, min(800, max_results * 6))

        while len(collected_items) < max_results and scroll_attempts < max_scroll_attempts:
            if _should_stop(should_stop_fn):
                log("[BingMaps] Stop requested.")
                raise StopScrape("Stop requested")
            _wait_if_paused(should_pause_fn, log)

            items = _find_result_items(driver)
            new_found = 0

            for item_el in items:
                if len(collected_items) >= max_results:
                    break
                try:
                    basic = _extract_from_list_item(item_el)
                    name = basic.get("name", "").strip()
                    if not name or len(name) < 2:
                        continue
                    key = name.lower()
                    if key in seen_names:
                        continue
                    seen_names.add(key)

                    item_data = {
                        **basic,
                        "element": item_el,
                        "maps_url": f"https://www.bing.com/maps/search?q={quote_plus(name)}",
                    }
                    collected_items.append(item_data)
                    new_found += 1
                except Exception:
                    continue

            if new_found > 0:
                no_new_rounds = 0
            else:
                no_new_rounds += 1

            if len(collected_items) >= max_results:
                break

            # Scroll the results panel to load more
            try:
                container, _ = _find_result_container(driver)
                if container:
                    driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
                else:
                    # Try scrolling the page
                    driver.execute_script("window.scrollBy(0, 400);")
            except Exception:
                try:
                    driver.execute_script("window.scrollBy(0, 400);")
                except Exception:
                    pass

            scroll_attempts += 1
            delay = random.uniform(*speed_cfg["delay"])
            time.sleep(delay)

            # Check if no new items loaded (end of list)
            new_items_after = _find_result_items(driver)
            if len(new_items_after) <= len(items) and scroll_attempts > 5:
                # Try multiple scroll strategies to load more results
                more_loaded = False

                # Strategy 1: Try "Show more" / "Load more" buttons
                for more_sel in [
                    "button.more", "a.more", "button[aria-label*='more']", "#see_more",
                    "button[aria-label*='More results']", "a.b_moretxt",
                    "div.b_pag a[aria-label*='Next']",
                ]:
                    try:
                        btn = driver.find_element(By.CSS_SELECTOR, more_sel)
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(2.0)
                        more_loaded = True
                        break
                    except Exception:
                        pass

                # Strategy 2: Scroll the window itself (some Bing Maps layouts use window scroll)
                if not more_loaded and scroll_attempts % 3 == 0:
                    try:
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        time.sleep(1.0)
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        time.sleep(0.5)
                        more_loaded = True
                    except Exception:
                        pass

                # Strategy 3: Scroll back up then down to trigger lazy load
                if not more_loaded and no_new_rounds > 0 and no_new_rounds % 5 == 0:
                    try:
                        container, _ = _find_result_container(driver)
                        if container:
                            driver.execute_script("arguments[0].scrollTop = 0;", container)
                            time.sleep(0.4)
                            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
                            time.sleep(1.0)
                    except Exception:
                        pass

                # Give up only after a generous number of attempts
                if not more_loaded and scroll_attempts > 40:
                    log(f"[BingMaps] End of results after {scroll_attempts} scroll attempts. Collected {len(collected_items)} listings.")
                    break

        log(f"[BingMaps] Collected {len(collected_items)} unique businesses. Enriching contact details...")

        # Now enrich: visit each website for email, and try to get detailed info
        def enrich_item(item_data):
            if _should_stop(should_stop_fn):
                return None
            _wait_if_paused(should_pause_fn, log)

            name = item_data.get("name", "")
            phone = item_data.get("phone", "")
            address = item_data.get("address", "")
            website = item_data.get("website", "")
            maps_url = item_data.get("maps_url", "")
            email = ""

            # Extract email from website
            if website:
                domain = urlparse(website).netloc.lower().lstrip("www.")
                with email_cache_lock:
                    cached = email_cache.get(domain)
                if cached is not None:
                    email = cached
                else:
                    try:
                        email = _extract_email_from_site(website)
                    except Exception:
                        email = ""
                    with email_cache_lock:
                        email_cache[domain] = email
                    if email:
                        log(f"[BingMaps] Email found: {email} ({name})")
                        if on_email_update:
                            try:
                                on_email_update(domain, email)
                            except Exception:
                                pass

            record = {
                "name": name,
                "phone": phone,
                "email": email,
                "website": website,
                "address": address,
                "maps_url": maps_url,
                "location": location,
                "source": "bing_maps",
            }

            if on_result:
                try:
                    on_result(record)
                except Exception:
                    pass

            return record

        workers = min(max_email_workers, len(collected_items), 16)
        if workers > 0 and collected_items:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(enrich_item, item): item for item in collected_items}
                done = 0
                for future in as_completed(futures):
                    if _should_stop(should_stop_fn):
                        break
                    done += 1
                    try:
                        record = future.result()
                        if record:
                            with results_lock:
                                results.append(record)
                            if done % 10 == 0:
                                log(f"[BingMaps] Enriched {done}/{len(collected_items)} listings...")
                    except Exception:
                        pass
        else:
            for item in collected_items:
                record = enrich_item(item)
                if record:
                    results.append(record)

        emails_found = sum(1 for r in results if r.get("email"))
        log(f"[BingMaps] Done: {len(results)} listings, {emails_found} emails found.")
        return results

    except StopScrape:
        raise
    except Exception as exc:
        log(f"[BingMaps] Scraper error: {exc}")
        return results
    finally:
        try:
            driver.quit()
        except Exception:
            pass
