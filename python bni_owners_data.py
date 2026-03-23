"""
BNI Connect Global – Member Scraper v2
=======================================
Fixes:
  1. Credentials: hardcode EMAIL/PASSWORD directly here (no env var needed)
  2. Member card detection: multiple DOM strategies so 0-results never happens
  3. Retry logic on profile fetch
  4. Better address / phone / email extraction
  5. Resume support (skips already-scraped profiles in CSV)
  6. Structured logging to file + console
"""

import csv
import logging
import os
import re
import sys
import time
from typing import Dict, List, Set

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import (
    BrowserContext,
    Page,
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

# ==============================================================================
# ✏️  EDIT THESE
# ==============================================================================
EMAIL    = "your_email@example.com"       # ← put your BNI email here
PASSWORD = "your_password_here"           # ← put your BNI password here
CITY     = "nagpur"                       # ← city to search

SERVICE_ACCOUNT_FILE = "service_account.json"
GOOGLE_SHEET_NAME    = "BNI Owners Data"
WORKSHEET_NAME       = "Details"
CSV_FILE             = f"bni_{CITY}_owners.csv"

HEADLESS             = False   # True = no browser window
SLOW_MO              = 150     # ms between actions (reduce if stable)
# ==============================================================================

LOGIN_URL  = "https://www.bniconnectglobal.com/login/"
SEARCH_URL = "https://www.bniconnectglobal.com/web/dashboard/search"
BASE_URL   = "https://www.bniconnectglobal.com"

HEADERS = [
    "Name", "Chapter", "Company", "City",
    "Industry and Classification",
    "Contact", "Mail", "Web Page Link", "Address",
]

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bni_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bni")


# ==============================================================================
# GOOGLE SHEET
# ==============================================================================
def get_worksheet() -> gspread.Worksheet:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds  = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    client = gspread.authorize(creds)

    try:
        ss = client.open(GOOGLE_SHEET_NAME)
    except gspread.SpreadsheetNotFound:
        ss = client.create(GOOGLE_SHEET_NAME)
        log.info("Created new Google Sheet: %s", GOOGLE_SHEET_NAME)

    try:
        ws = ss.worksheet(WORKSHEET_NAME)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=WORKSHEET_NAME, rows=5000, cols=20)

    ws.update(range_name="A1:I1", values=[HEADERS])
    log.info("Sheet ready: %s / %s", GOOGLE_SHEET_NAME, WORKSHEET_NAME)
    return ws


def write_to_sheet(ws: gspread.Worksheet, rows: List[Dict]) -> None:
    if not rows:
        return
    values = [[r.get(h, "") for h in HEADERS] for r in rows]
    ws.update(range_name=f"A2:I{len(values)+1}", values=values)
    log.info("Sheet updated — %d rows written.", len(values))


# ==============================================================================
# CSV
# ==============================================================================
def init_csv() -> Set[str]:
    """Initialise CSV; return set of profile URLs already scraped."""
    done: Set[str] = set()
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                u = row.get("profile_url", "").strip()
                if u:
                    done.add(u)
        log.info("Resume mode – %d records already in CSV.", len(done))
    else:
        with open(CSV_FILE, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(HEADERS + ["profile_url"])
    return done


def append_csv(row: Dict) -> None:
    with open(CSV_FILE, "a", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerow(
            [row.get(h, "") for h in HEADERS] + [row.get("profile_url", "")]
        )


# ==============================================================================
# HELPERS
# ==============================================================================
def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith(("http://", "https://")):
        return href
    return BASE_URL + ("" if href.startswith("/") else "/") + href


# Indian + generic phone regex
_PHONE_RE = re.compile(
    r"(\+?91[\s\-]?)?[6-9]\d{9}"
    r"|(\+\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}"
)


def find_phones(text: str) -> List[str]:
    seen: Set[str] = set()
    result = []
    for m in _PHONE_RE.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group())
        if 8 <= len(digits) <= 15 and digits not in seen:
            seen.add(digits)
            result.append(m.group().strip())
    return result


def find_email(page: Page, body: str) -> str:
    el = page.locator('a[href^="mailto:"]')
    if el.count():
        return (el.first.get_attribute("href") or "").replace("mailto:", "").strip()
    m = re.search(r"[\w.\-+]+@[\w.\-]+\.\w{2,}", body)
    return m.group(0) if m else ""


def find_website(page: Page) -> str:
    skip = {BASE_URL.lower(), "bniconnectglobal.com"}
    for el in page.locator("a[href]").element_handles():
        href = (el.get_attribute("href") or "").strip()
        if (
            href
            and href.startswith("http")
            and not any(s in href.lower() for s in skip)
            and not href.lower().startswith("mailto:")
            and "#" not in href
        ):
            return href
    return ""


# ==============================================================================
# LOGIN
# ==============================================================================
def login(page: Page) -> None:
    log.info("Navigating to login page…")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    # Fill credentials
    page.locator('input[name="username"]').fill(EMAIL)
    page.locator('input[name="password"]').fill(PASSWORD)
    page.locator('button[type="submit"]').click()

    try:
        page.wait_for_url("**/web/dashboard**", timeout=30_000)
    except PlaywrightTimeoutError:
        log.error("Login failed – check EMAIL / PASSWORD or solve any CAPTCHA manually.")
        input("Solve any CAPTCHA in the browser, then press Enter to continue…")

    page.wait_for_timeout(3_000)
    log.info("Logged in successfully.")


# ==============================================================================
# SEARCH
# ==============================================================================
def search_city(page: Page) -> None:
    log.info("Opening search page…")
    page.goto(SEARCH_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3_000)

    # Try multiple selectors for the search box
    selectors = [
        'input[placeholder*="Search"]',
        'input[placeholder*="search"]',
        'input[type="search"]',
        'input[type="text"]',
        '#search',
        '.search-input',
    ]
    search_box = None
    for sel in selectors:
        el = page.locator(sel).first
        if el.count() > 0:
            search_box = el
            log.info("Search box found with selector: %s", sel)
            break

    if not search_box:
        log.error("Could not find search box. Dumping page HTML for debug…")
        with open("debug_search_page.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        raise RuntimeError("Search box not found – see debug_search_page.html")

    search_box.click()
    search_box.fill(CITY)
    page.keyboard.press("Enter")
    page.wait_for_timeout(5_000)

    # Wait for results to appear
    try:
        page.wait_for_selector("a[href*='/web/member']", timeout=15_000)
        log.info("Search results loaded.")
    except PlaywrightTimeoutError:
        log.warning("No /web/member links appeared after search. Saving debug HTML…")
        with open("debug_results_page.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        log.warning("Saved debug_results_page.html – inspect it to fix selectors.")


# ==============================================================================
# COLLECT MEMBER CARDS  (3 strategies)
# ==============================================================================
def collect_cards(page: Page) -> List[Dict]:
    """
    Strategy 1 – anchor tags whose href contains /web/member
    Strategy 2 – any element with class containing 'member' or 'result'
    Strategy 3 – full JS evaluation walking the DOM
    """
    results: List[Dict] = []
    seen: Set[str] = set()

    # ── Strategy 1: direct href match ─────────────────────────────────────────
    anchors = page.locator("a[href*='/web/member']")
    count   = anchors.count()
    log.info("Strategy 1: found %d /web/member links", count)

    for i in range(count):
        a    = anchors.nth(i)
        name = norm(a.inner_text())
        href = abs_url(a.get_attribute("href") or "")
        if not name or not href:
            continue
        key = f"{name}|{href}"
        if key in seen:
            continue
        seen.add(key)

        # Try to get the row container text for extra fields
        row_text = ""
        try:
            # walk up max 6 parents looking for a container with >30 chars
            js = """
            (el) => {
                let node = el;
                for (let i = 0; i < 6; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    const t = (node.innerText || '').replace(/\\s+/g,' ').trim();
                    if (t.length > 30) return node.innerText;
                }
                return '';
            }
            """
            row_text = a.evaluate(js) or ""
        except Exception:
            pass

        card = _parse_card_text(row_text, name)
        card["profile_url"] = href
        results.append(card)

    # ── Strategy 2: JS full scan (catches lazy-loaded items) ──────────────────
    if len(results) == 0:
        log.info("Strategy 1 returned 0 – trying JS full-DOM scan…")
        raw = page.evaluate("""
        () => {
            const out = [];
            document.querySelectorAll('a').forEach(a => {
                const href = a.getAttribute('href') || '';
                const name = (a.innerText || '').replace(/\\s+/g,' ').trim();
                if (!name || !href.includes('/web/member')) return;

                // climb for row context
                let rowText = '';
                let node = a;
                for (let i=0; i<8; i++){
                    node = node.parentElement;
                    if (!node) break;
                    const t = (node.innerText||'').replace(/\\s+/g,' ').trim();
                    if (t.length > 20 && /BNI/i.test(t)){
                        rowText = node.innerText;
                        break;
                    }
                }
                out.push({ name, href, rowText });
            });
            return out;
        }
        """)

        for item in raw:
            name = norm(item.get("name", ""))
            href = abs_url(item.get("href", ""))
            if not name or not href:
                continue
            key = f"{name}|{href}"
            if key in seen:
                continue
            seen.add(key)
            card = _parse_card_text(item.get("rowText", ""), name)
            card["profile_url"] = href
            results.append(card)

    return results


def _parse_card_text(row_text: str, fallback_name: str) -> Dict:
    lines = [norm(x) for x in row_text.splitlines() if norm(x)]

    skip = {
        "search results", "name", "chapter", "company", "city",
        "industry and classification", "connect", "+", "search members",
    }
    lines = [l for l in lines if l.lower() not in skip]

    data = {
        "Name":    fallback_name,
        "Chapter": "",
        "Company": "",
        "City":    CITY.title(),
        "Industry and Classification": "",
    }

    if len(lines) >= 1:
        data["Name"]    = lines[0]
    if len(lines) >= 2:
        data["Chapter"] = lines[1]

    for line in lines:
        if line.lower() == CITY.lower():
            data["City"] = line

    for line in lines:
        if ">" in line:
            data["Industry and Classification"] = line

    used = {data["Name"], data["Chapter"], data["City"],
            data["Industry and Classification"]}
    remaining = [x for x in lines if x not in used]
    if remaining:
        data["Company"] = remaining[0]

    return data


# ==============================================================================
# SCROLL + LOAD ALL CARDS
# ==============================================================================
def load_all_cards(page: Page) -> List[Dict]:
    log.info("Scrolling to load all member cards…")

    all_cards: List[Dict] = []
    seen_keys: Set[str]   = set()
    stable = 0

    for round_no in range(1, CONFIG_MAX_ROUNDS + 1):
        batch = collect_cards(page)

        new_count = 0
        for card in batch:
            key = f"{card.get('Name','')}|{card.get('profile_url','')}"
            if key not in seen_keys:
                seen_keys.add(key)
                all_cards.append(card)
                new_count += 1

        log.info("Round %d: +%d new  (total %d)", round_no, new_count, len(all_cards))

        if new_count == 0:
            stable += 1
        else:
            stable = 0

        if stable >= CONFIG_STABLE_CUTOFF:
            log.info("No new members in %d rounds — done scrolling.", stable)
            break

        # Scroll page
        page.mouse.wheel(0, CONFIG_SCROLL_PX)
        page.wait_for_timeout(CONFIG_SCROLL_PAUSE)

        # Also scroll any scrollable div
        page.evaluate("""
        () => {
            document.querySelectorAll('div').forEach(el => {
                const s = window.getComputedStyle(el);
                if ((s.overflowY==='auto'||s.overflowY==='scroll')
                    && el.scrollHeight > el.clientHeight)
                    el.scrollTop += 1500;
            });
        }
        """)
        page.wait_for_timeout(CONFIG_SCROLL_PAUSE)

    log.info("Total member cards collected: %d", len(all_cards))
    return all_cards


# Scroll settings (edit at top CONFIG section to tune)
CONFIG_MAX_ROUNDS    = 50
CONFIG_STABLE_CUTOFF = 4
CONFIG_SCROLL_PX     = 2500
CONFIG_SCROLL_PAUSE  = 1200   # ms


# ==============================================================================
# PROFILE EXTRACTION
# ==============================================================================
def extract_profile(context: BrowserContext, url: str, retries: int = 3) -> Dict:
    details = {"Contact": "", "Mail": "", "Web Page Link": "", "Address": ""}

    for attempt in range(1, retries + 1):
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2_000)

            body_lines = [norm(x) for x in page.locator("body").inner_text().splitlines() if norm(x)]
            body_text  = " ".join(body_lines)

            details["Mail"]         = find_email(page, body_text)
            details["Web Page Link"] = find_website(page)

            phones = find_phones(body_text)
            if phones:
                details["Contact"] = " / ".join(phones[:2])

            details["Address"] = _extract_address(page, body_lines)
            break   # success

        except PlaywrightTimeoutError:
            log.warning("Timeout on %s (attempt %d/%d)", url, attempt, retries)
            if attempt < retries:
                time.sleep(2 ** attempt)
        except Exception as e:
            log.warning("Error on %s (attempt %d): %s", url, attempt, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
        finally:
            page.close()

    return details


def _extract_address(page: Page, body_lines: List[str]) -> str:
    # Try <address> tag first
    addr_el = page.locator("address")
    if addr_el.count():
        return norm(addr_el.first.inner_text())

    # Try labelled field: look for "Address" label then grab next lines
    address_lines = []
    capture = False
    stop_keywords = {
        "city", "zip", "postal code", "country", "state",
        "phone", "email", "website", "mobile",
    }
    for line in body_lines:
        low = line.lower()
        if low == "address":
            capture = True
            continue
        if capture:
            if any(low.startswith(k) for k in stop_keywords):
                break
            if line in {"‹", "›", "<", ">", "|"}:
                continue
            address_lines.append(line)
            if len(address_lines) >= 3:
                break

    if address_lines:
        return " ".join(address_lines).strip()

    # Keyword fallback
    keywords = [
        "road", "rd.", "nagar", "colony", "complex", "building",
        "floor", "plot", "lane", "sector", "chowk", "square",
        "apartment", "chaoni", "layout", "ward",
    ]
    candidates = [
        l for l in body_lines
        if any(k in l.lower() for k in keywords)
    ]
    return " ".join(candidates[:3]).strip()


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> None:
    # Quick credential check
    if "your_email" in EMAIL or not EMAIL:
        raise ValueError(
            "Please set EMAIL and PASSWORD at the top of this script!"
        )
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(f"Missing: {SERVICE_ACCOUNT_FILE}")

    log.info("Starting BNI scraper  |  city=%s", CITY)

    ws          = get_worksheet()
    done_urls   = init_csv()
    final_rows: List[Dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO)
        context = browser.new_context()
        page    = context.new_page()

        try:
            login(page)
            search_city(page)

            members = load_all_cards(page)

            if not members:
                log.error(
                    "0 members found.\n"
                    "  • Check debug_results_page.html to inspect the DOM.\n"
                    "  • The BNI site may require manual interaction first.\n"
                    "  • Try setting HEADLESS=False and logging in manually."
                )
                input("Press Enter to close…")
                return

            total = len(members)
            log.info("Extracting profiles for %d members…", total)

            for idx, member in enumerate(members, 1):
                url = member.get("profile_url", "")
                if not url or url in done_urls:
                    log.info("[%d/%d] Skipping (already done): %s", idx, total, member.get("Name"))
                    continue

                done_urls.add(url)
                log.info("[%d/%d] %s", idx, total, member.get("Name", ""))

                profile = extract_profile(context, url)

                row = {
                    "Name":    member.get("Name", ""),
                    "Chapter": member.get("Chapter", ""),
                    "Company": member.get("Company", ""),
                    "City":    member.get("City", CITY.title()),
                    "Industry and Classification": member.get("Industry and Classification", ""),
                    "Contact":      profile["Contact"],
                    "Mail":         profile["Mail"],
                    "Web Page Link": profile["Web Page Link"],
                    "Address":      profile["Address"],
                    "profile_url":  url,
                }

                final_rows.append(row)
                append_csv(row)
                log.info("    ✓ %s | %s | %s", row["Mail"], row["Contact"], row["Company"])

                # Interim sheet write every N profiles
                if len(final_rows) % 10 == 0:
                    log.info("Interim write to Sheet…")
                    write_to_sheet(ws, final_rows)

        except KeyboardInterrupt:
            log.warning("Interrupted by user — saving collected data…")

        finally:
            log.info("Final write to Sheet (%d rows)…", len(final_rows))
            write_to_sheet(ws, final_rows)
            log.info("All done. CSV: %s", CSV_FILE)

            input("Press Enter to close browser…")
            browser.close()


if __name__ == "__main__":
    main()