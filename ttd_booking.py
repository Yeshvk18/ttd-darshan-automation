#!/usr/bin/env python3
"""
TTD Rs.300 Special Entry Darshan - assisted booking helper (Playwright).

What it does:   opens the official portal in a visible browser, pre-fills repetitive fields
                (date, slot, pilgrim details) from config.yaml, waits properly for the
                dynamic pages, and stops for YOUR review.
What it never does: solve/bypass CAPTCHA, read/enter the OTP, skip queues or quota rules,
                or press any payment button. Login OTP, CAPTCHA and payment are always manual.

Usage:
    python ttd_booking.py                 # normal assisted run
    python ttd_booking.py --inspect       # dump page fields/screenshots at each step (first-time calibration)
    python ttd_booking.py --config my.yaml
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import yaml
from playwright.sync_api import (
    Locator,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

DEBUG_DIR = Path("debug")
MAX_PILGRIMS = 6

# Words used to find fields by label / placeholder / name / id / formcontrolname.
FIELD_HINTS = {
    "name": ["full ?name", "pilgrim ?name", "^name$", "name"],
    "age": ["age"],
    "gender": ["gender", "sex"],
    "id_type": ["id ?type", "photo ?id ?type", "proof ?type", "id ?proof"],
    "id_number": ["id ?(number|no)", "aadhaar", "proof ?(number|no)"],
}
CAPTCHA_SEL = (
    "[class*=captcha i]:visible, [id*=captcha i]:visible, img[src*=captcha i]:visible, "
    "iframe[src*=recaptcha i]:visible, input[name*=captcha i]:visible, input[placeholder*=captcha i]:visible"
)
OTP_SEL = "input[name*=otp i]:visible, input[placeholder*=otp i]:visible, input[formcontrolname*=otp i]:visible"
# Buttons we refuse to click automatically: payment is always the user's action.
PAYMENT_RE = re.compile(r"pay|payment|upi|card|net ?banking", re.I)


# --------------------------------------------------------------------------- utils
def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def ask(msg: str) -> str:
    return input(f"\n>>> {msg}\n    ").strip()


def pause(msg: str) -> None:
    ask(f"{msg}  (press Enter when done)")


def shot(page: Page, name: str) -> None:
    DEBUG_DIR.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(DEBUG_DIR / f"{name}.png"), full_page=True)
    except Exception as exc:  # screenshots are best-effort
        log(f"screenshot failed: {exc}")


def settle(page: Page, extra_ms: int = 600) -> None:
    """Wait for the Angular app to finish loading/animating."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    spinner = page.locator("[class*=spinner i], [class*=loader i], [class*=loading i], .mat-progress-spinner")
    try:
        spinner.first.wait_for(state="hidden", timeout=10000)
    except PWTimeout:
        pass
    page.wait_for_timeout(extra_ms)


def retry(fn, attempts: int = 3, delay: float = 1.5, what: str = "action"):
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            log(f"{what} failed (attempt {i}/{attempts}): {str(exc).splitlines()[0]}")
            time.sleep(delay * i)
    raise RuntimeError(f"{what} failed after {attempts} attempts") from last


def as_list(v) -> list[str]:
    if not v:
        return []
    return [v] if isinstance(v, str) else list(v)


# --------------------------------------------------------------------------- config
def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        sys.exit(f"{path} not found. Copy config.example.yaml to config.yaml and edit it.")
    cfg = yaml.safe_load(p.read_text()) or {}

    mobile = str(cfg.get("mobile", "")).strip()
    if not re.fullmatch(r"[6-9]\d{9}", mobile):
        sys.exit("config: 'mobile' must be a 10-digit Indian mobile number.")
    cfg["mobile"] = mobile

    dates = []
    for d in cfg.get("dates") or []:
        dt = datetime.strptime(str(d), "%Y-%m-%d").date()
        if dt < date.today():
            sys.exit(f"config: date {dt} is in the past.")
        if dt.weekday() < 5:
            log(f"WARNING: {dt} is a {dt:%A}, not a weekend.")
        dates.append(dt)
    if not dates:
        sys.exit("config: 'dates' must list at least one date.")
    cfg["dates"] = dates

    pilgrims = cfg.get("pilgrims") or []
    if not 1 <= len(pilgrims) <= MAX_PILGRIMS:
        sys.exit(f"config: provide 1-{MAX_PILGRIMS} pilgrims.")
    for i, pl in enumerate(pilgrims, 1):
        missing = [k for k in ("name", "age", "gender", "id_type", "id_number") if not str(pl.get(k, "")).strip()]
        if missing:
            sys.exit(f"config: pilgrim #{i} missing {missing}")
    cfg["slots"] = [str(s) for s in cfg.get("slots") or []]
    cfg["poll_seconds"] = max(20, int(cfg.get("poll_seconds", 30)))  # be polite to the server
    cfg["max_polls"] = max(1, int(cfg.get("max_polls", 20)))
    cfg.setdefault("selectors", {})
    cfg.setdefault("browser", {})
    return cfg


def wait_until(start_at: str) -> None:
    if not start_at:
        return
    fmt = "%Y-%m-%d %H:%M" if len(start_at) > 5 else "%H:%M"
    target = datetime.strptime(start_at, fmt)
    if fmt == "%H:%M":
        now = datetime.now()
        target = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    log(f"Waiting until {target:%Y-%m-%d %H:%M} ...")
    while (left := (target - datetime.now()).total_seconds()) > 0:
        time.sleep(min(left, 5))


# --------------------------------------------------------------------------- guards (manual hand-offs)
def guard(page: Page, where: str) -> None:
    """Hand over to the user whenever CAPTCHA / OTP appears. Never attempt to solve them."""
    for _ in range(5):  # a solved captcha may immediately be followed by an OTP prompt
        if page.locator(CAPTCHA_SEL).count():
            shot(page, f"captcha_{where}")
            pause(f"CAPTCHA detected ({where}). Solve it in the browser")
            settle(page)
        elif page.locator(OTP_SEL).count():
            pause(f"OTP requested ({where}). Enter it in the browser")
            settle(page)
        else:
            return


def inspect_page(page: Page, name: str, enabled: bool) -> None:
    """Dump interactive controls so selectors can be calibrated."""
    if not enabled:
        return
    DEBUG_DIR.mkdir(exist_ok=True)
    data = page.evaluate(
        """() => [...document.querySelectorAll('input,select,textarea,button,mat-select,[role=button],[role=option],td[class*=day i]')]
            .filter(e => e.offsetParent !== null)
            .map(e => ({tag: e.tagName, type: e.type || '', id: e.id, name: e.getAttribute('name') || '',
                        fc: e.getAttribute('formcontrolname') || '', ph: e.getAttribute('placeholder') || '',
                        aria: e.getAttribute('aria-label') || '', cls: (e.className || '').toString().slice(0, 80),
                        text: (e.innerText || '').trim().slice(0, 60)}))"""
    )
    (DEBUG_DIR / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
    shot(page, name)
    log(f"inspect: saved debug/{name}.json and .png ({len(data)} controls)")


# --------------------------------------------------------------------------- locating helpers
def visible_matches(page: Page, hints: list[str], override: list[str]) -> list[Locator]:
    """All visible inputs/selects that look like the requested field, in DOM order."""
    for sel in override:
        loc = page.locator(f"{sel}:visible")
        if loc.count():
            return [loc.nth(i) for i in range(loc.count())]
    for h in hints:
        rx = re.compile(h, re.I)
        css_rx = h.replace("^", "").replace("$", "").replace(" ?", "").replace("(number|no)", "")
        candidates = [
            page.get_by_label(rx),
            page.get_by_placeholder(rx),
            page.locator(
                f"input[formcontrolname*='{css_rx}' i], select[formcontrolname*='{css_rx}' i], mat-select[formcontrolname*='{css_rx}' i],"
                f"input[name*='{css_rx}' i], select[name*='{css_rx}' i], input[id*='{css_rx}' i], select[id*='{css_rx}' i]"
            ),
        ]
        for c in candidates:
            try:
                n = c.count()
            except Exception:
                continue
            vis = [c.nth(i) for i in range(n) if c.nth(i).is_visible()]
            if vis:
                return vis
    return []


def set_value(loc: Locator, value: str) -> None:
    """Fill a text input, native <select>, or Angular mat-select."""
    tag = loc.evaluate("e => e.tagName.toLowerCase()")
    if tag == "select":
        try:
            loc.select_option(label=value)
        except Exception:
            opts = loc.locator("option").all_inner_texts()
            match = next((o for o in opts if value.lower() in o.lower()), None)
            if match is None:
                raise ValueError(f"option '{value}' not in {opts}")
            loc.select_option(label=match)
    elif tag == "mat-select" or loc.get_attribute("role") in ("combobox", "listbox"):
        loc.click()
        loc.page.get_by_role("option", name=re.compile(re.escape(value), re.I)).first.click()
    else:
        loc.fill(value)
        loc.blur()


# --------------------------------------------------------------------------- flow steps
def check_notices(page: Page, cfg: dict) -> None:
    """Read the public dashboard notices about Rs.300 quota for the target months."""
    text = page.inner_text("body")
    months = {f"{d:%B}-{d.year}".lower() for d in cfg["dates"]}
    lines = [ln.strip() for ln in text.splitlines() if re.search(r"special entry darshan\s*\(rs\.?\s*300", ln, re.I)]
    for ln in lines:
        flag = ""
        if any(m in ln.lower() for m in months):
            flag = "  <-- TARGET MONTH"
            if "completed" in ln.lower():
                flag += " (ALREADY SOLD OUT per portal notice)"
        log(f"Portal notice: {ln}{flag}")
    if not lines:
        log("No Rs.300 notice found on dashboard (layout may have changed).")


def is_logged_in(page: Page) -> bool:
    return page.get_by_text(re.compile(r"^\s*(log ?out|sign ?out)\s*$", re.I)).count() > 0 or (
        page.get_by_text(re.compile(r"^\s*log ?in\s*$", re.I)).count() == 0
        and page.get_by_text(re.compile(r"^\s*namaskaram", re.I)).count() > 0
    )


def login(page: Page, cfg: dict) -> None:
    if is_logged_in(page):
        log("Existing session detected - skipping login.")
        return
    log("Opening login ...")
    page.get_by_text(re.compile(r"^\s*log ?in\s*$", re.I)).first.click()
    box = page.get_by_text("Registered mobile number").locator("xpath=following::input[1]")
    box.wait_for(state="visible")
    box.fill(cfg["mobile"])
    page.get_by_role("button", name=re.compile(r"get otp", re.I)).click()
    log("OTP requested by you via the site. The SMS goes to your registered number.")
    pause("Enter the OTP (and any CAPTCHA) in the browser and complete login")
    settle(page)
    guard(page, "login")


def open_special_entry(page: Page) -> None:
    log("Opening 'Special Entry Darshan' ...")
    page.goto(page.url.split("#")[0], wait_until="domcontentloaded")  # ensure dashboard
    settle(page)
    tile = page.get_by_text("Special Entry Darshan", exact=True).first
    retry(lambda: tile.click(), what="open Special Entry Darshan")
    settle(page)
    guard(page, "entry")


def pick_date(page: Page, d: date, cfg: dict) -> bool:
    log(f"Selecting date {d:%a %d-%b-%Y} ...")
    override = cfg["selectors"].get("date_cell")
    if override:
        loc = page.locator(override.format(d=d)).first
        try:
            loc.wait_for(state="visible", timeout=8000)
            loc.click()
            settle(page)
            return True
        except PWTimeout:
            return False

    # Best effort: open a date picker if the date is an input, navigate months, click the day.
    for opener in ("input[placeholder*=date i]", "[class*=datepicker-toggle]", "mat-datepicker-toggle button", "input[readonly]"):
        o = page.locator(f"{opener}:visible").first
        if o.count():
            try:
                o.click(timeout=3000)
                break
            except Exception:
                continue
    month_label = f"{d:%B} {d.year}".lower()
    for _ in range(12):
        header = page.locator("[class*=calendar i] [class*=period i], [class*=month i][class*=label i], .mat-calendar-period-button, .ui-datepicker-title, [class*=header i]")
        txt = " ".join(header.all_inner_texts()).lower() if header.count() else page.inner_text("body").lower()
        if month_label in txt or f"{d:%b} {d.year}".lower() in txt:
            break
        nxt = page.locator("[aria-label*=next i], [class*=next i], .mat-calendar-next-button").first
        if not nxt.count():
            break
        nxt.click()
        page.wait_for_timeout(400)
    cell = page.locator(
        f"[aria-label*='{d:%d %B %Y}' i], [aria-label*='{d:%B} {d.day}, {d.year}' i], "
        f"td:not([class*=disabled i]):not([class*=other i]) >> text=/^\\s*{d.day}\\s*$/"
    ).first
    try:
        cell.wait_for(state="visible", timeout=5000)
        if cell.is_disabled() or "disabled" in (cell.get_attribute("class") or "").lower():
            log("Date is disabled / not bookable.")
            return False
        cell.click()
        settle(page)
        return True
    except PWTimeout:
        log("Could not find that date in the calendar.")
        return False


def pick_slot(page: Page, cfg: dict) -> str | None:
    override = cfg["selectors"].get("slot_option")
    if override:
        options = page.locator(f"{override}:visible")
    else:
        options = page.locator(
            "[class*=slot i]:visible, label:visible, mat-radio-button:visible, tr:visible, li:visible, button:visible"
        ).filter(has_text=re.compile(r"\d{1,2}\s*[:.]\s*\d{2}"))
    n = options.count()
    available = []
    for i in range(n):
        o = options.nth(i)
        text = " ".join(o.inner_text().split())
        low = text.lower()
        cls = (o.get_attribute("class") or "").lower()
        if any(w in low for w in ("sold out", "not available", "full", "closed", "unavailable")) or "disabled" in cls:
            continue
        available.append((i, text))
    log(f"Available slots: {[t for _, t in available] or 'none'}")
    if not available:
        return None
    wanted = cfg["slots"] or [""]
    for pref in wanted:
        for i, text in available:
            if pref.lower() in text.lower():
                options.nth(i).click()
                settle(page)
                return text
    return None


def set_pilgrim_count(page: Page, n: int, cfg: dict) -> None:
    sel = cfg["selectors"]
    counters = visible_matches(page, ["no\\.? ?of ?(persons|pilgrims|tickets)", "persons", "pilgrims", "quantity"], [])
    if counters:
        try:
            set_value(counters[0], str(n))
            settle(page)
            return
        except Exception as exc:
            log(f"count dropdown not usable ({exc}); trying add-pilgrim button")
    add = page.locator(sel["add_pilgrim_button"]).first if sel.get("add_pilgrim_button") else page.get_by_role(
        "button", name=re.compile(r"add (pilgrim|person|member)|^\+$", re.I)
    ).first
    for _ in range(n - 1):
        if not add.count():
            break
        add.click()
        page.wait_for_timeout(500)


def fill_pilgrims(page: Page, cfg: dict) -> list[str]:
    """Fill each pilgrim row. Returns a list of fields the user must complete manually."""
    todo: list[str] = []
    pilgrims = cfg["pilgrims"]
    set_pilgrim_count(page, len(pilgrims), cfg)
    for field in FIELD_HINTS:
        locs = visible_matches(page, FIELD_HINTS[field], as_list(cfg["selectors"].get(field)))
        if len(locs) < len(pilgrims):
            log(f"Found {len(locs)} '{field}' inputs for {len(pilgrims)} pilgrims.")
        for i, pl in enumerate(pilgrims):
            if i >= len(locs):
                todo.append(f"pilgrim {i + 1}: {field}")
                continue
            try:
                retry(lambda: set_value(locs[i], str(pl[field])), attempts=2, what=f"fill {field}[{i + 1}]")
            except Exception as exc:
                log(f"could not fill {field} for pilgrim {i + 1}: {exc}")
                todo.append(f"pilgrim {i + 1}: {field}")
    return todo


def review(page: Page, cfg: dict, chosen_date: date, slot: str, todo: list[str]) -> bool:
    shot(page, "review")
    print("\n" + "=" * 64)
    print(" REVIEW - nothing has been paid or submitted yet")
    print("=" * 64)
    print(f" Date   : {chosen_date:%A, %d %B %Y}")
    print(f" Slot   : {slot}")
    print(f" Mobile : {cfg['mobile']}")
    for i, p in enumerate(cfg["pilgrims"], 1):
        idn = str(p["id_number"])
        print(f" #{i}: {p['name']}, {p['age']}, {p['gender']}, {p['id_type']} ****{idn[-4:]}")
    amounts = [ln.strip() for ln in page.inner_text("body").splitlines() if re.search(r"₹|rs\.?\s*\d|total|amount", ln, re.I)]
    if amounts:
        print(" Site shows:", *amounts[:6], sep="\n   ")
    if todo:
        print("\n Could NOT auto-fill (do these by hand in the browser):")
        for t in todo:
            print("   -", t)
    print(" Screenshot: debug/review.png")
    print("=" * 64)
    ans = ask("Fix anything in the browser now. Type PROCEED to click the site's continue button (stops before payment), anything else to stay manual")
    return ans.upper() == "PROCEED"


def proceed(page: Page, cfg: dict) -> None:
    sel = cfg["selectors"].get("proceed_button")
    btn = (
        page.locator(sel).first
        if sel
        else page.get_by_role("button", name=re.compile(r"^(continue|proceed|next|submit|confirm)\b", re.I)).first
    )
    label = (btn.inner_text() or "").strip() if btn.count() else ""
    if not btn.count():
        log("No continue button found - press it yourself in the browser.")
        return
    if PAYMENT_RE.search(label):
        log(f"Button '{label}' looks like a payment action - NOT clicking. Do it yourself.")
        return
    log(f"Clicking '{label}' ...")
    btn.click()
    settle(page)
    guard(page, "after_proceed")


# --------------------------------------------------------------------------- main
def run(cfg: dict, inspect: bool) -> None:
    b = cfg["browser"]
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            b.get("profile_dir", "./profile"),
            headless=False,  # always visible: you stay in control
            slow_mo=int(b.get("slow_mo_ms", 100)),
            viewport={"width": 1366, "height": 900},
        )
        ctx.set_default_timeout(int(b.get("default_timeout_ms", 30000)))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(cfg.get("site_url"), wait_until="domcontentloaded")
            settle(page, 2000)
            check_notices(page, cfg)
            wait_until(cfg.get("start_at", ""))
            login(page, cfg)
            inspect_page(page, "01_after_login", inspect)

            open_special_entry(page)
            inspect_page(page, "02_special_entry", inspect)

            chosen: tuple[date, str] | None = None
            for attempt in range(1, cfg["max_polls"] + 1):
                for d in cfg["dates"]:
                    if not pick_date(page, d, cfg):
                        continue
                    guard(page, "date")
                    inspect_page(page, f"03_slots_{d}", inspect)
                    slot = pick_slot(page, cfg)
                    if slot:
                        chosen = (d, slot)
                        break
                if chosen:
                    break
                log(f"No slot yet (poll {attempt}/{cfg['max_polls']}); retrying in {cfg['poll_seconds']}s ...")
                time.sleep(cfg["poll_seconds"])
                page.reload(wait_until="domcontentloaded")
                settle(page)
            if not chosen:
                shot(page, "no_slots")
                log("No matching slot found. You can pick one manually in the browser.")
                pause("Choose date/slot manually if available")
                chosen = (cfg["dates"][0], "(chosen manually)")

            guard(page, "slot")
            proceed_after_slot = page.get_by_role("button", name=re.compile(r"^(continue|proceed|next)\b", re.I)).first
            if proceed_after_slot.count() and not PAYMENT_RE.search(proceed_after_slot.inner_text() or ""):
                proceed_after_slot.click()
                settle(page)
                guard(page, "pilgrim_form")
            inspect_page(page, "04_pilgrim_form", inspect)

            todo = fill_pilgrims(page, cfg)
            inspect_page(page, "05_pilgrim_filled", inspect)

            if review(page, cfg, chosen[0], chosen[1], todo):
                proceed(page, cfg)
            inspect_page(page, "06_after_proceed", inspect)
            print("\nFinish captcha / confirmation / PAYMENT yourself in the browser.")
            pause("Press Enter here only when you are finished to close the browser")
        except KeyboardInterrupt:
            log("Interrupted.")
        except Exception as exc:
            log(f"ERROR: {exc}")
            shot(page, "error")
            print("The browser stays open - continue manually.")
            pause("Press Enter to close the browser")
        finally:
            ctx.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--inspect", action="store_true", help="save page controls + screenshots under ./debug at each step")
    args = ap.parse_args()
    run(load_config(args.config), args.inspect)


if __name__ == "__main__":
    main()
