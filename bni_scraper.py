# ============================================================
#  BNI CONNECT SCRAPER — Version 9.0 (FINAL)
#  Scrapes all cities, loads ALL members (not just 20)
#  Saves to Google Sheet (separate tab per city) + CSV
#
#  SETUP (run once):
#    pip install playwright gspread google-auth
#    playwright install chromium
#
#  HOW TO RUN:
#    python bni_scraper.py
# ============================================================

import csv, os, re, time
from typing import Dict, List, Set
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

# ============================================================
#  ✏️  CHANGE ONLY THESE TWO LINES
# ============================================================
BNI_EMAIL    = "abhijieetp@gmail.com"
BNI_PASSWORD = "Tropical@16"

# ============================================================
#  CITIES — add or remove as needed
# ============================================================
CITIES = [
    "Nagpur"
]

# ============================================================
#  SETTINGS — leave as-is
# ============================================================
LOGIN_URL            = "https://www.bniconnectglobal.com/login/"
SEARCH_URL           = "https://www.bniconnectglobal.com/web/dashboard/search"
SERVICE_ACCOUNT_FILE = "service_account.json"
GOOGLE_SHEET_NAME    = "BNI Owners Data"
HEADLESS             = False
SLOW_MO              = 200

HEADERS = [
    "Name",
    "Chapter",
    "Company",
    "City",
    "Industry and Classification",
    "Phone",
    "Email",
    "Website",
    "Address",
    "Professional Classification",
    "Business Description",
]


# ============================================================
#  GOOGLE SHEET
# ============================================================

def get_or_create_worksheet(client, city: str):
    try:
        spreadsheet = client.open(GOOGLE_SHEET_NAME)
    except Exception as e:
        print(f"  ❌ Cannot open sheet '{GOOGLE_SHEET_NAME}': {e}")
        return None
    try:
        ws = spreadsheet.worksheet(city)
        print(f"  ✅ Using existing tab: '{city}'")
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=city, rows=5000, cols=20)
        print(f"  ✅ Created new tab: '{city}'")
    # Always refresh headers in row 1
    ws.update("A1", [HEADERS])
    return ws


def append_to_sheet(ws, row: Dict):
    if not ws:
        return
    try:
        ws.append_row(
            [row.get(h, "") for h in HEADERS],
            value_input_option="USER_ENTERED"
        )
    except Exception as e:
        print(f"  ⚠️  Sheet write error: {e}")


# ============================================================
#  CSV
# ============================================================

def init_csv(city: str) -> str:
    fn = f"bni_{city.lower().replace(' ', '_')}_members.csv"
    with open(fn, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerow(HEADERS)
    return fn


def append_csv(fn: str, row: Dict):
    with open(fn, "a", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerow([row.get(h, "") for h in HEADERS])


# ============================================================
#  HELPERS
# ============================================================

def norm(t):
    return re.sub(r"\s+", " ", t or "").strip()


def is_phone(t: str) -> bool:
    """True only if text is a real phone number — not a date or long text."""
    if not t or len(t) > 20:
        return False
    if re.search(r"\d{2}/\d{2}/\d{4}", t):
        return False
    digits = re.sub(r"\D", "", t)
    return 8 <= len(digits) <= 15


def find_email(t: str) -> str:
    m = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", t or "")
    return m.group(0) if m else ""


def safe_body_lines(page) -> List[str]:
    """Get page text safely — handles pages with iframes (2 body elements)."""
    try:
        raw = page.locator("body").first.inner_text(timeout=10000)
        return [norm(x) for x in raw.splitlines() if norm(x)]
    except Exception:
        try:
            raw = page.evaluate("() => document.body ? document.body.innerText : ''")
            return [norm(x) for x in raw.splitlines() if norm(x)]
        except Exception:
            return []


# ============================================================
#  LOGIN
# ============================================================

def login(page):
    print("\n🔐 Logging in...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    for sel in ['input[name="username"]', 'input[name="email"]', 'input[type="email"]']:
        try:
            page.wait_for_selector(sel, timeout=3000)
            page.fill(sel, BNI_EMAIL)
            print(f"  ✅ Email: {sel}")
            break
        except: continue

    for sel in ['input[name="password"]', 'input[type="password"]']:
        try:
            page.wait_for_selector(sel, timeout=3000)
            page.fill(sel, BNI_PASSWORD)
            print(f"  ✅ Password: {sel}")
            break
        except: continue

    for sel in ['button[type="submit"]', 'button:has-text("Sign In")']:
        try:
            page.click(sel, timeout=3000)
            break
        except: continue

    page.wait_for_url("**/web/dashboard**", timeout=30000)
    page.wait_for_timeout(3000)
    print("✅ Login successful!\n")


# ============================================================
#  SEARCH
# ============================================================

def search_city(page, city: str):
    print(f"\n🔍 Searching: {city}")
    page.goto(SEARCH_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    for sel in ['input[type="search"]', 'input[type="text"]',
                'input[placeholder*="search" i]']:
        try:
            page.wait_for_selector(sel, timeout=3000)
            page.fill(sel, city)
            page.press(sel, "Enter")
            break
        except: continue

    page.wait_for_timeout(6000)
    print(f"✅ Search done for {city}")


# ============================================================
#  SCROLL TO LOAD ALL MEMBERS
#  BNI uses infinite/virtual scroll — we must scroll the inner
#  results container, not just the window.
#  Strategy: scroll all scrollable divs + window + End key
#  until no new member links appear for 3 rounds.
# ============================================================

def scroll_to_load_all(page) -> int:
    prev_count = 0
    no_change_rounds = 0

    while True:
        current_count = page.evaluate("""
            () => document.querySelectorAll('a[href*="networkHome?userId"]').length
        """)

        print(f"  📜 Scrolling... members loaded: {current_count}")

        if current_count == prev_count:
            no_change_rounds += 1
        else:
            no_change_rounds = 0
            prev_count = current_count

        if no_change_rounds >= 3:
            break

        # Scroll every scrollable div (results container)
        page.evaluate("""
            () => {
                document.querySelectorAll('div').forEach(div => {
                    const s = window.getComputedStyle(div);
                    if ((s.overflowY === 'auto' || s.overflowY === 'scroll')
                            && div.scrollHeight > div.clientHeight + 50
                            && div.clientHeight > 200) {
                        div.scrollTop += 800;
                    }
                });
            }
        """)

        # Also scroll the window and press End
        page.mouse.wheel(0, 1500)
        page.keyboard.press("End")
        page.wait_for_timeout(2500)

    print(f"  ✅ All members loaded: {prev_count}")
    return prev_count


# ============================================================
#  COLLECT ALL MEMBER LINKS FROM SEARCH RESULTS
# ============================================================

def get_all_members(page, city: str) -> List[Dict]:
    results = page.evaluate(f"""
        () => {{
            const CITY = {repr(city)};
            const members = [];

            const links = Array.from(
                document.querySelectorAll('a[href*="networkHome?userId"]')
            );

            for (const link of links) {{
                const name = (link.innerText || '').replace(/\\s+/g, ' ').trim();
                let href = link.getAttribute('href') || '';
                if (!name || !href) continue;
                if (href.startsWith('/'))
                    href = 'https://www.bniconnectglobal.com' + href;

                // Walk up DOM to find row container
                let rowEl = link.parentElement;
                let bestEl = null;

                for (let i = 0; i < 10; i++) {{
                    if (!rowEl) break;
                    const t = (rowEl.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (t.length > 20 && t.length < 600 && /BNI\\s/i.test(t)) {{
                        bestEl = rowEl;
                        break;
                    }}
                    rowEl = rowEl.parentElement;
                }}

                let chapter = '', company = '', cityVal = CITY, industry = '';

                if (bestEl) {{
                    const childTexts = Array.from(bestEl.children)
                        .map(c => (c.innerText || '').replace(/\\s+/g, ' ').trim())
                        .filter(t => t && t !== name && t !== '+' && t !== 'Connect');

                    chapter  = childTexts.find(x => /^BNI\\s/i.test(x)) || '';
                    cityVal  = childTexts.find(
                        x => x.toLowerCase() === CITY.toLowerCase()
                    ) || CITY;
                    industry = childTexts.find(x => x.includes('>')) || '';

                    const used = new Set([name, chapter, cityVal, industry,
                                          '', 'Connect', '+']);
                    company  = childTexts.find(x => !used.has(x)) || '';
                }}

                members.push({{ name, href, chapter, company,
                                city: cityVal, industry }});
            }}

            // Deduplicate by href
            const seen = new Set();
            return members.filter(m => {{
                if (seen.has(m.href)) return false;
                seen.add(m.href);
                return true;
            }});
        }}
    """)

    return [{
        "name":     norm(r.get("name", "")),
        "href":     norm(r.get("href", "")),
        "chapter":  norm(r.get("chapter", "")),
        "company":  norm(r.get("company", "")),
        "city":     norm(r.get("city", "")) or city,
        "industry": norm(r.get("industry", "")),
    } for r in results if norm(r.get("name","")) and norm(r.get("href",""))]


# ============================================================
#  EXTRACT PROFILE PAGE DETAILS
#  Personal Details:  Phone, Email, Website, Address
#  Professional Details: Classification, Business Description
# ============================================================

def extract_profile(page) -> Dict:
    det = {
        "Phone": "", "Email": "", "Website": "",
        "Address": "", "Professional Classification": "",
        "Business Description": "",
    }

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except: pass
    page.wait_for_timeout(2000)

    lines = safe_body_lines(page)
    full  = " ".join(lines)

    # ── EMAIL ──────────────────────────────────────────────
    try:
        ml = page.locator('a[href^="mailto:"]').first
        if ml.count() > 0:
            det["Email"] = (ml.get_attribute("href") or "").replace("mailto:", "").strip()
    except: pass
    if not det["Email"]:
        det["Email"] = find_email(full)

    # ── WEBSITE ────────────────────────────────────────────
    try:
        for i in range(min(page.locator('a[href]').count(), 40)):
            try:
                h = (page.locator('a[href]').nth(i).get_attribute("href") or "").strip()
                if (h and h.startswith("http")
                        and "bniconnect" not in h.lower()
                        and not h.startswith("mailto:")
                        and "#" not in h):
                    det["Website"] = h
                    break
            except: continue
    except: pass

    # ── PHONE ──────────────────────────────────────────────
    phones = []
    for line in lines:
        if len(line) > 20:
            continue
        if re.search(r"\d{2}/\d{2}/\d{4}", line):
            continue
        cleaned = re.sub(r"[\s\-\(\)\+\.]", "", line)
        if is_phone(cleaned) and cleaned not in phones:
            phones.append(cleaned)
        if len(phones) == 2:
            break
    if phones:
        det["Phone"] = " / ".join(phones)

    # ── ADDRESS ────────────────────────────────────────────
    for i, line in enumerate(lines):
        if line == "City" and i > 0:
            for j in range(i - 1, max(i - 6, 0), -1):
                c = lines[j]
                if (5 < len(c) < 200
                        and "@" not in c
                        and not c.startswith("http")
                        and not is_phone(re.sub(r"[\s\-\.]", "", c))
                        and c not in {"Personal Details", "Professional Details",
                                      "My Bio", "Profile", "MSP", "Training History"}
                        and not re.search(r"\d{2}/\d{2}/\d{4}", c)):
                    det["Address"] = c
                    break
            break

    if not det["Address"]:
        for line in lines:
            if (len(line) < 200 and
                    any(k in line.lower() for k in [
                        "road", "nagar", "tower", "complex", "floor", "lane",
                        "building", "colony", "plot", "apartment", "sector",
                        "street", "avenue", "block", "phase", "drive", "close"
                    ])):
                det["Address"] = line
                break

    # ── PROFESSIONAL CLASSIFICATION & BUSINESS DESCRIPTION ─
    in_prof = False
    prof_count = 0
    for line in lines:
        if line == "Professional Details":
            in_prof = True
            prof_count = 0
            continue
        if in_prof:
            if line in {"My Bio", "Training History", "‹", "›", "Profile"}:
                break
            if len(line) > 3 and not re.search(r"\d{2}/\d{2}/\d{4}", line):
                prof_count += 1
                if prof_count == 1:
                    det["Professional Classification"] = line
                elif prof_count == 2:
                    det["Business Description"] = line
                    break

    return det


# ============================================================
#  SCRAPE ONE CITY
# ============================================================

def scrape_city(page, city: str, ws, csv_file: str) -> int:
    print(f"\n{'='*55}")
    print(f"  🏙️  CITY: {city.upper()}")
    print(f"{'='*55}")

    # Search for this city
    search_city(page, city)

    # Scroll to load ALL members first
    print(f"\n  Loading all members for {city}...")
    scroll_to_load_all(page)

    # Collect all member links now that DOM is fully loaded
    all_members = get_all_members(page, city)
    print(f"  👥 Total unique members: {len(all_members)}\n")

    done_urls: Set[str] = set()
    city_rows: List[Dict] = []

    for m in all_members:
        url = m["href"]
        if url in done_urls:
            continue
        done_urls.add(url)

        print(f"  ➡️  [{len(city_rows)+1}/{len(all_members)}] {m['name']}")

        # Open profile page
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"     ⚠️  Cannot open: {e}")
            search_city(page, city)
            scroll_to_load_all(page)
            continue

        prof = extract_profile(page)

        final = {
            "Name":     m["name"],
            "Chapter":  m["chapter"],
            "Company":  m["company"],
            "City":     m["city"],
            "Industry and Classification": m["industry"],
            "Phone":    prof["Phone"],
            "Email":    prof["Email"],
            "Website":  prof["Website"],
            "Address":  prof["Address"],
            "Professional Classification": prof["Professional Classification"],
            "Business Description":        prof["Business Description"],
        }

        print(f"     Chapter : {final['Chapter']}")
        print(f"     Company : {final['Company']}")
        print(f"     Phone   : {final['Phone']}")
        print(f"     Email   : {final['Email']}")
        print(f"     Address : {final['Address']}")

        # Save immediately to CSV and Google Sheet
        append_csv(csv_file, final)
        append_to_sheet(ws, final)
        city_rows.append(final)

        # Go back to search results
        page.go_back(wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # If search page was lost, reload and re-scroll
        if "/web/dashboard/search" not in page.url:
            search_city(page, city)
            scroll_to_load_all(page)

    print(f"\n  ✅ {city} complete — {len(city_rows)} members saved")
    return len(city_rows)


# ============================================================
#  MAIN
# ============================================================

def main():
    print("=" * 55)
    print("  BNI Connect Scraper  —  Version 9.0 FINAL")
    print(f"  Cities : {', '.join(CITIES)}")
    print(f"  Sheet  : {GOOGLE_SHEET_NAME}")
    print("=" * 55)

    # Connect to Google Sheets once for all cities
    print("\n📋 Connecting to Google Sheet...")
    gsheet_client = None
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=scopes
        )
        gsheet_client = gspread.authorize(creds)
        print("✅ Google Sheet client ready")
    except Exception as e:
        print(f"⚠️  Sheet error: {e}")
        print("   Continuing — data will be saved to CSV only")

    summary = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO)
        page = browser.new_context().new_page()

        # Login once — stays logged in for all cities
        login(page)

        # Loop through all cities
        for i, city in enumerate(CITIES, 1):
            print(f"\n\n{'#'*55}")
            print(f"  CITY {i} of {len(CITIES)}: {city.upper()}")
            print(f"{'#'*55}")

            csv_file = init_csv(city)
            print(f"📄 CSV: {csv_file}")

            ws = None
            if gsheet_client:
                ws = get_or_create_worksheet(gsheet_client, city)

            count = scrape_city(page, city, ws, csv_file)
            summary[city] = count

            if i < len(CITIES):
                print(f"\n⏸️  Pausing 5 seconds before next city...")
                time.sleep(5)

        # Print final summary
        print(f"\n\n{'='*55}")
        print("  🎉  ALL CITIES COMPLETE!")
        print(f"{'='*55}")
        total = 0
        for city, count in summary.items():
            icon = "✅" if count > 0 else "⚠️ "
            print(f"  {icon} {city:<15} → {count:>4} members")
            total += count
        print(f"  {'─'*35}")
        print(f"     {'TOTAL':<15} → {total:>4} members")
        print(f"{'='*55}")
        print(f"\n📋 Google Sheet : {GOOGLE_SHEET_NAME}")
        print(f"📁 CSV files    : bni_[city]_members.csv")

        input("\nPress ENTER to close browser...")
        browser.close()


if __name__ == "__main__":
    main()