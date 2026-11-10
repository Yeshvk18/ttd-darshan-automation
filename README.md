# ttd-darshan-automation

# ttd-darshan-automation

Assisted booking helper for the TTD Rs.300 Special Entry Darshan, built on Playwright (Python).

It opens the official portal in a **visible** browser, pre-fills the repetitive parts (date, slot, pilgrim details) from a config file, waits for the dynamic pages to load, and then stops for your review.

## What it does and does not do

| Does | Never does |
|---|---|
| Opens the portal and reads the public quota notices for your target months | Solve or bypass CAPTCHA |
| Enters your mobile number and requests the login OTP | Read or enter the OTP (you type it) |
| Picks the first available date and slot from your preference list, re-checking periodically | Skip queues or quota rules |
| Fills pilgrim name, age, gender, ID type and ID number | Click any payment button |
| Shows a review summary and a screenshot before continuing | Submit anything without you typing `PROCEED` |

Login OTP, CAPTCHA and payment are always done by you in the browser.

## Files

| File | Purpose |
|---|---|
| `ttd_booking.py` | The script |
| `config.example.yaml` | Template config. Copy to `config.yaml` |
| `requirements.txt` | Python dependencies (`playwright`, `pyyaml`) |
| `.gitignore` | Keeps `config.yaml`, `profile/`, `debug/`, `.venv/` out of git |

## Prerequisites

- Python 3.10 or newer (`python3 --version`)
- Internet access to `ttdevasthanams.ap.gov.in`
- A mobile number registered on the TTD portal, with the phone in hand for the OTP
- Photo ID details for every pilgrim (max 6 per booking)

## Setup

```bash
# 1. Go to the project folder
cd ttd-darshan-automation

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Download the Chromium browser that Playwright drives
playwright install chromium

# 5. Create your private config
cp config.example.yaml config.yaml
```

Then edit `config.yaml` (see below). `config.yaml` holds personal data and is git-ignored. Never commit it.

## Configuration (`config.yaml`)

| Key | Required | Meaning |
|---|---|---|
| `site_url` | yes | Portal dashboard URL (pre-set in the example) |
| `mobile` | yes | 10-digit Indian mobile number starting with 6-9 |
| `dates` | yes | List of `YYYY-MM-DD` in order of preference. Past dates are rejected. Weekdays print a warning |
| `slots` | no | Preferred slot text in priority order, matched as a substring (e.g. `"10:00"`). Empty means first available |
| `poll_seconds` | no | Seconds between re-checks when no slot is found. Minimum 20, default 30 |
| `max_polls` | no | Maximum number of re-checks. Default 20 |
| `start_at` | no | Wait until `HH:MM` (today) or `YYYY-MM-DD HH:MM` before starting. Empty starts immediately |
| `pilgrims` | yes | 1 to 6 entries, each with `name`, `age`, `gender`, `id_type`, `id_number` (all required) |
| `browser.profile_dir` | no | Folder that keeps your login session between runs (default `./profile`) |
| `browser.slow_mo_ms` | no | Delay between browser actions (default 100) |
| `browser.default_timeout_ms` | no | Per-action timeout (default 30000) |
| `selectors.*` | no | CSS overrides if auto-matching misses a field (see Troubleshooting) |

Tips:
- Spell names exactly as on the ID.
- `gender` and `id_type` must match the options shown on the site (for example `Male`, `Female`, `Aadhaar`).
- Use the dates for the month whose quota has been released. The script prints the portal's notice for your target months at startup and flags "ALREADY SOLD OUT" if the portal says so.

## Running

First-time calibration run. It saves every page's controls and a screenshot under `debug/`:

```bash
python ttd_booking.py --inspect
```

Normal run:

```bash
python ttd_booking.py
```

Custom config file:

```bash
python ttd_booking.py --config my.yaml
```

### What happens, step by step

1. A Chromium window opens and loads the portal. Quota notices are printed.
2. If `start_at` is set, the script waits until that time.
3. Login: it enters your mobile number and clicks **Get OTP**. You type the OTP (and any CAPTCHA) in the browser, then press Enter in the terminal. If a session is already saved in `profile/`, login is skipped.
4. It opens **Special Entry Darshan**, then tries each date in order and picks the first available slot matching `slots`. If none is available it waits `poll_seconds`, reloads and retries up to `max_polls` times. After that it hands over so you can choose manually.
5. It fills the pilgrim details and prints a **REVIEW** summary (ID numbers masked to the last 4 digits) and saves `debug/review.png`. Anything it could not fill is listed for you to complete by hand.
6. Fix anything in the browser. Type `PROCEED` to click the site's continue button, or anything else to stay manual. The script refuses to click anything that looks like a payment button.
7. You complete CAPTCHA, confirmation and payment yourself, then press Enter in the terminal to close the browser.

Whenever a CAPTCHA or OTP prompt appears, the script pauses and waits for you.

## Verify the setup before the real booking window

1. `python3 --version` prints 3.10 or newer.
2. `pip list | grep -i -E "playwright|pyyaml"` shows both packages.
3. `playwright install chromium` finishes without errors.
4. `python ttd_booking.py --config config.yaml` fails with a clear `config:` message if the config is invalid, for example a past date, a bad mobile number or a missing pilgrim field. Fix these first.
5. Do a full dry run with `--inspect` well before the booking opens. Stop at the review step and do not type `PROCEED`. Check that:
   - the quota notice lines are printed,
   - the OTP login works and the session is saved,
   - date and slot selection work,
   - every pilgrim field is filled correctly,
   - `debug/` contains screenshots and JSON files.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `config.yaml not found` | Run `cp config.example.yaml config.yaml` and edit it |
| `config: 'mobile' must be a 10-digit Indian mobile number` | Use 10 digits starting with 6, 7, 8 or 9, no `+91` |
| `config: date ... is in the past` | Update `dates` to future dates |
| `playwright: command not found` or browser executable missing | Activate the venv, then run `pip install -r requirements.txt` and `playwright install chromium` |
| Date not selected, or "Could not find that date in the calendar" | Run with `--inspect`, open `debug/02_special_entry.json`, and set `selectors.date_cell`, e.g. `"td[aria-label*='{d:%d %B %Y}']"` |
| No slots detected but the site shows some | Set `selectors.slot_option` to the slot element's CSS selector, e.g. `".slot-card"` |
| A pilgrim field is not filled (listed under "Could NOT auto-fill") | Find the field's `name`, `id` or `formcontrolname` in `debug/04_pilgrim_form.json` and set `selectors.name`, `age`, `gender`, `id_type` or `id_number` |
| Continue button is not clicked | Set `selectors.proceed_button`, or click it yourself |
| Login keeps asking for OTP | Delete the `profile/` folder to reset the saved session, then log in again |
| Browser closes on an error | Check `debug/error.png` and the terminal message. The browser stays open until you press Enter, so you can continue manually |
| Pages load slowly or time out | Increase `browser.default_timeout_ms` and `browser.slow_mo_ms` |

If the portal's layout changes, auto-matching may fail. The `--inspect` output and the `selectors` overrides are the way to recalibrate without editing the code.

## Safety and responsible use

- Keep `config.yaml`, `profile/` and `debug/` private. They contain personal data and a logged-in session. They are already in `.gitignore`.
- Do not lower `poll_seconds` below 20. The script enforces this minimum to avoid loading the server.
- Use it only for your own legitimate bookings and follow the portal's terms.
