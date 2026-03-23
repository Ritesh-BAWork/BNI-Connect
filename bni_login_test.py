import os
from playwright.sync_api import sync_playwright, TimeoutError

LOGIN_URL = "https://www.bniconnectglobal.com/login/"
CITY_NAME = "Nagpur"

EMAIL = os.getenv("BNI_EMAIL")
PASSWORD = os.getenv("BNI_PASSWORD")

if not EMAIL or not PASSWORD:
    raise ValueError("BNI_EMAIL or BNI_PASSWORD is missing.")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=500)
    context = browser.new_context()
    page = context.new_page()

    print("Opening login page...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    print("Logging in...")
    page.locator('input[name="username"]').fill(EMAIL)
    page.locator('input[name="password"]').fill(PASSWORD)
    page.locator('button[type="submit"]').click()

    page.wait_for_url("**/web/dashboard", timeout=30000)
    page.wait_for_timeout(5000)

    print("Logged in:", page.url)

    # STEP 1: click search icon
    print("Trying to click search icon...")

    search_clicked = False
    search_selectors = [
        'button:has(svg)',
        'button[aria-label*="search" i]',
        'button[title*="search" i]',
        'button:has-text("")',
        'svg',
    ]

    for sel in search_selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            if count > 0:
                for i in range(min(count, 10)):
                    try:
                        loc.nth(i).click(timeout=2000)
                        page.wait_for_timeout(2000)
                        print(f"Clicked search candidate: {sel} [{i}]")
                        search_clicked = True
                        break
                    except:
                        pass
            if search_clicked:
                break
        except:
            pass

    if not search_clicked:
        print("Could not click search icon automatically.")
        input("Please click the search icon manually, then press Enter here...")

    page.wait_for_timeout(3000)

    # STEP 2: enter city name
    print("Trying to find city search input...")

    city_input_selectors = [
        'input[type="search"]',
        'input[placeholder*="city" i]',
        'input[placeholder*="search" i]',
        'input[name*="city" i]',
        'input[type="text"]'
    ]

    city_filled = False
    for sel in city_input_selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(timeout=5000)
            loc.click()
            loc.fill(CITY_NAME)
            page.wait_for_timeout(1000)
            print(f"Filled city using: {sel}")
            city_filled = True
            break
        except:
            pass

    if not city_filled:
        print("Could not find city input automatically.")
        input(f"Please type {CITY_NAME} manually, then press Enter here...")

    # STEP 3: click search / press enter
    print("Trying to run search...")

    search_run_selectors = [
        'button[type="submit"]',
        'button:has-text("Search")',
        'button:has-text("Find")',
        'button:has-text("Go")',
    ]

    search_done = False
    for sel in search_run_selectors:
        try:
            page.locator(sel).first.click(timeout=3000)
            page.wait_for_timeout(4000)
            print(f"Search submitted using: {sel}")
            search_done = True
            break
        except:
            pass

    if not search_done:
        try:
            page.keyboard.press("Enter")
            page.wait_for_timeout(4000)
            print("Search submitted using Enter key")
            search_done = True
        except:
            pass

    print("Current URL after search attempt:", page.url)
    input("Inspect result page, then press Enter to close...")
    browser.close()