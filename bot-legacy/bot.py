#!/usr/bin/env python3
"""
prenot@mi racer — a personal booking assistant for https://prenotami.esteri.it

WHAT IT DOES
  Sleeps until the drop (00:00 Europe/Rome — that is 23:00 Tunisian time while Italy
  is on summer time), then runs the booking flow automatically inside ONE real,
  persistent browser session using your own account:

      login -> service/consulate page -> passport page
      (pick service option, passport no., honeypot "textera" clears, checkbox)
      -> waits for YOU to solve the one interactive CAPTCHA, then presses AVANTI
      -> races the calendar: first GREEN date -> first GREEN time slot -> PRENOTA
      -> receipt page, screenshots, log.

  Bounded retries/backoff on timeouts, session expiry (re-login), 5xx pages and
  "no availability yet" refreshes. It polls; it does not hammer.

WHAT IT DELIBERATELY DOES NOT DO
  * no CAPTCHA solving/bypass: the page's own reCAPTCHA runs normally and YOU
    solve it; the bot detects the solved token and fires AVANTI within 0.5 s.
  * no fingerprint spoofing, stealth patches, fake profiles or session rotation:
    one headed Chromium with a normal, persistent user profile.
  * no parallel sessions and no direct hammering of internal API endpoints.

USAGE
  cp config.example.json config.json      # fill in credentials + service
  python3 bot.py --config config.json --scout        # explore once, verify selectors
  python3 bot.py --config config.json                # wait for the drop, then race
  python3 bot.py --config config.json --start-now    # go immediately (testing)
  python3 selftest.py                                # end-to-end test on a local mock site
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zoneinfo import ZoneInfo

try:
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import TimeoutError as PWTimeout
except ImportError:  # pragma: no cover
    print("Playwright is missing. Run:  pip install playwright && playwright install chromium", file=sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

DEFAULTS = {
    "base_url": "https://prenotami.esteri.it",
    "email": "",
    "password": "",
    "skip_login": False,
    "login_assist_s": 420,          # let you finish SSO/2FA manually if it appears
    "service_match": "legaliz|legalizz|d\\.o\\.v|legalis",
    "service_url": "",              # optional deep link found with --scout
    "consulate_match": "",          # e.g. "tunisi" — matched against <select> options
    "selects": [                    # how to drive each dropdown on the passport page
        {"match": "", "pick": "first"},          # first option of the first list, as specified
        {"match": "tunisi", "pick": "option_re:tunisi"},
    ],
    "passport_number": "",
    "drop": {"time": "00:00", "timezone": "Europe/Rome", "start_early_s": 0.0},
    "strategy": {
        "submit": "at_drop",        # "at_drop" | "early"
        "early_lead_s": 25.0,       # for "early": press AVANTI this long before the drop
        "race_window_s": 120.0,     # keep racing until drop + this many seconds
        "poll_ms": 400,             # steady in-race poll (local DOM only, no network)
        "hot_poll_ms": 50,          # in-race poll during the hot window after the drop
        "hot_window_s": 25.0,       # length of the hot window
        "reload_if_empty_s": 2.0,   # steady refresh when the calendar is empty
        "reload_hot_s": 1.2,        # refresh cadence in the hot window (the only network hit)
    },
    "captcha": {"wait_s": 900.0},   # how long ONE captcha wait may block
    "captcha_open_s": 150.0,        # open the captcha window this long before the press
    "warmup_lead_s": 480.0,         # start login/prep this many seconds before the drop
    "keepalive_s": 30.0,            # warm-connection ping while waiting (0 = off)
    "block_assets": True,           # drop images/fonts/media after the captcha (speed)
    "clock": {"apply_server_skew": True},  # fire on the server's clock, not the local one
    "browser": {"headless": False, "profile_dir": "./profile", "nav_timeout_s": 45.0},
    "screenshots_dir": "./shots",
    "on_success": "",               # optional shell cmd, {receipt} placeholder
    "keep_open_s": 0,               # keep the browser open N seconds after success
}

log = logging.getLogger("racer")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

class RaceError(RuntimeError):
    pass


def load_config(path: str) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))

    def merge(base: dict, over: dict) -> dict:
        out = dict(base)
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = merge(out[k], v)
            else:
                out[k] = v
        return out

    cfg = merge(DEFAULTS, cfg)
    cfg["email"] = os.environ.get("PRENOTAMI_EMAIL", cfg["email"])
    cfg["password"] = os.environ.get("PRENOTAMI_PASSWORD", cfg["password"])
    return cfg


def setup_logging(cfg: dict, level: str) -> Path:
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = log_dir / f"run-{stamp}.log"
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s", "%H:%M:%S")
    log.setLevel(getattr(logging, level.upper(), logging.INFO))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(sh)
    log.addHandler(fh)
    return log_file


def snap(page, tag: str, cfg: dict) -> Path | None:
    """Screenshot + html dump of the current page; never raises."""
    try:
        d = Path(cfg["screenshots_dir"])
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S%f")
        png = d / f"{stamp}-{tag}.png"
        page.screenshot(path=str(png))
        (d / f"{stamp}-{tag}.html").write_text(page.content(), encoding="utf-8")
        return png
    except Exception as e:  # pragma: no cover
        log.debug("screenshot failed (%s): %s", tag, e)
        return None


def notify(msg: str) -> None:
    """Best-effort desktop notification + terminal bell (macOS/Linux)."""
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e", f'display notification "{msg}" with title "prenot@mi racer"'],
                timeout=5, capture_output=True,
            )
        except Exception:
            pass


def now_tz(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def next_drop(cfg: dict, now: datetime | None = None) -> datetime:
    """Next occurrence of drop.time in drop.timezone (defaults to Europe/Rome).

    Europe/Rome midnight is what the site actually opens on; while Italy is on
    summer time that instant is 23:00 in Tunis (UTC+1), which is exactly the
    drop the user waits for. Computing from Rome automatically stays correct
    across CET/CEST changes.
    """
    d = cfg["drop"]
    tz = ZoneInfo(d["timezone"])
    now = now.astimezone(tz) if now else datetime.now(tz)
    hh, mm = (int(x) for x in d["time"].split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def spin_until(target: datetime, early_s: float = 0.0,
               keepalive=None, keepalive_s: float = 30.0) -> None:
    """Sleep to `target` (minus early_s) with ms-level precision at the end.

    `keepalive` (zero-arg callable) is invoked every `keepalive_s` while
    waiting — used to keep the TLS connection pool warm so the midnight
    navigation reuses a live connection instead of re-handshaking. The ping
    is fire-and-forget inside the page (~1 ms in the spin loop).
    """
    deadline = target.timestamp() - early_s
    next_ping = time.time() + keepalive_s if (keepalive and keepalive_s > 0) else 0.0
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        if keepalive and next_ping and time.time() >= next_ping:
            try:
                keepalive()
            except Exception:
                pass
            next_ping = time.time() + keepalive_s
        if remaining > 25:
            time.sleep(min(15.0, remaining - 20))
        elif remaining > 1.0:
            time.sleep(min(0.25, remaining - 0.5))
        elif remaining > 0.02:
            time.sleep(0.001)
        # final ~20 ms: busy-wait on the wall clock
    log.info("DROP TIME REACHED — local %s", datetime.now().strftime("%H:%M:%S.%f")[:-3])


def body_text(page) -> str:
    try:
        return page.evaluate("() => (document.body ? document.body.innerText : '')") or ""
    except Exception:
        return ""


def page_url(page) -> str:
    try:
        return page.url
    except Exception:
        return ""


# --------------------------------------------------------------------------
# page-specific JS: green day / green slot / captcha token
# --------------------------------------------------------------------------

_DAY_JS = r"""
() => {
  const rgb = (str) => (str || '').match(/[\d.]+/g);
  const greenish = (cs) => {
    const m = rgb(cs.backgroundColor);
    if (!m || m.length < 3) return false;
    const [r, g, b] = m.slice(0, 3).map(Number);
    return g > 90 && g > r + 25 && g > b + 25;
  };
  const reddish = (cs) => {
    const m = rgb(cs.backgroundColor);
    if (!m || m.length < 3) return false;
    const [r, g, b] = m.slice(0, 3).map(Number);
    return r > 140 && g < 110 && b < 110;
  };
  const GREEN = /green|dispon|available|prenotab|bookable|\bok\b|open|attivo/i;
  const RED = /red|rosso|non[\-]?disp|unavail|disabled|closed|full|passat|error|scadut/i;
  const out = [];
  const nodes = document.querySelectorAll('td, a, button, li, span, div, day, .day');
  for (const el of nodes) {
    if (out.length >= 6) break;
    const t = (el.textContent || '').trim();
    if (!/^\d{1,2}$/.test(t)) continue;
    const n = parseInt(t, 10);
    if (!(n >= 1 && n <= 31)) continue;
    if (el.children.length > 2) continue;
    const holder = el.closest('td') || el.closest('.day') || el;
    const cs = getComputedStyle(holder);
    const cls = String(holder.className || '') + ' ' + String(el.className || '');
    const dead = holder.classList.contains('disabled') || el.classList.contains('disabled')
      || holder.getAttribute('aria-disabled') === 'true'
      || el.getAttribute('aria-disabled') === 'true';
    const green = greenish(cs) || (GREEN.test(cls) && !RED.test(cls));
    if (!green) continue;
    if (reddish(cs) || RED.test(cls) || dead) continue;
    out.push(el);
  }
  if (!out.length) return {status: 'none'};
  try { out[0].click(); } catch (e) { return {status: 'error', msg: String(e)}; }
  return {status: 'picked', n: out.length, label: (out[0].textContent || '').trim()};
}
"""

_SLOT_JS = r"""
() => {
  const rgb = (str) => (str || '').match(/[\d.]+/g);
  const GREEN = /green|dispon|available|prenotab|bookable|\bok\b|free|attivo/i;
  const RED = /red|rosso|non[\-]?disp|unavail|disabled|closed|full|error|busy/i;
  const out = [];
  const nodes = document.querySelectorAll('td, a, button, li, span, div, option, label');
  for (const el of nodes) {
    if (out.length >= 6) break;
    const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!/^\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}$/.test(t)) continue;
    if (el.children.length > 2) continue;
    const holder = el.closest('.slot') || el.closest('li') || el.closest('td') || el;
    const cs = getComputedStyle(holder);
    const m = rgb(cs.backgroundColor);
    let green = false, red = false;
    if (m && m.length >= 3) {
      const [r, g, b] = m.slice(0, 3).map(Number);
      green = g > 90 && g > r + 25 && g > b + 25;
      red = r > 140 && g < 110 && b < 110;
    }
    const cls = String(holder.className || '') + ' ' + String(el.className || '');
    if (!green && !GREEN.test(cls)) continue;
    if (red || RED.test(cls)) continue;
    const dead = holder.classList.contains('disabled') || el.classList.contains('disabled')
      || holder.getAttribute('aria-disabled') === 'true'
      || el.getAttribute('aria-disabled') === 'true';
    if (dead) continue;
    out.push(el);
  }
  if (!out.length) return {status: 'none'};
  try { out[0].click(); } catch (e) { return {status: 'error', msg: String(e)}; }
  return {status: 'picked', n: out.length, label: (out[0].textContent || '').trim()};
}
"""

_CELLS_DIAG_JS = r"""
() => {
  const seen = [];
  for (const el of document.querySelectorAll('td, .day, .slot, a, button')) {
    const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 12) continue;
    const cs = getComputedStyle(el);
    seen.push([el.tagName, t, cs.backgroundColor, String(el.className || '').slice(0, 60)]);
    if (seen.length >= 40) break;
  }
  return seen;
}
"""

_TOKEN_JS = r"""
() => {
  const sels = [
    'textarea[name="g-recaptcha-response"]',
    'input[name="g-recaptcha-response"]',
    'textarea[name="h-captcha-response"]',
    'input[name="h-captcha-response"]',
    '[name="cf-turnstile-response"]'
  ];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (el && el.value && el.value.trim().length > 15) return true;
  }
  const b = document.body;
  if (b && b.getAttribute('data-captcha-ok') === 'true') return true;
  return false;
}
"""

_WIDGET_JS = """
() => !!document.querySelector(
  'iframe[src*="recaptcha"], iframe[title*="reCAPTCHA"], iframe[title*="captcha"], ' +
  'iframe[src*="hcaptcha"], .g-recaptcha, .h-captcha, .cf-turnstile, .grecaptcha-badge'
)
"""

_RECEIPT_JS = r"""
() => {
  const t = (document.body ? document.body.innerText : '');
  return /prenotazione\s+confermat|prenotato\s+confermat|conferma\s+di\s+prenotaz|booking\s+confirm|riepilogo\s+della\s+prenotazione/i.test(t);
}
"""

# in-page fast clicks: one evaluate, no actionability round trips; the
# Playwright locator remains as fallback wherever a click is safety-checked.
_AVANTI_JS = r"""
() => {
  for (const el of document.querySelectorAll('button, input[type="submit"], a')) {
    const t = (el.value || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!/^avanti\b/i.test(t)) continue;
    if (el.disabled) continue;
    el.click();
    return true;
  }
  return false;
}
"""

_PRENOTA_JS = r"""
() => {
  const rx = /^prenot(a|ami|are|azione)\b/i;
  const cands = [...document.querySelectorAll('button, input[type="submit"]')];
  for (const a of document.querySelectorAll('a')) {
    if (rx.test((a.textContent || '').trim())) cands.push(a);
  }
  for (const el of cands) {
    const t = (el.value || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!rx.test(t)) continue;
    if (el.disabled) continue;
    el.click();
    return true;
  }
  return false;
}
"""

# which step did we land on? evaluated as ONE wait_for_function at 60 ms
# granularity instead of a sleep-loop of separate locator counts.
_ADVANCE_JS = r"""
() => {
  if (document.querySelector("input[type='password']")) return 'password';
  const t = document.body ? document.body.innerText : '';
  if (/prenotazione\s+confermat|prenotato\s+confermat|conferma\s+di\s+prenotaz|booking\s+confirm|riepilogo\s+della\s+prenotazione/i.test(t)) return 'receipt';
  if (document.querySelectorAll('td, .day').length > 0 || /calendario|calendar/i.test(t)) return 'calendar';
  for (const el of document.querySelectorAll('button, input[type="submit"], a')) {
    const s = (el.value || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (/^avanti\b/i.test(s)) return 'step1';
  }
  return '';
}
"""


# --------------------------------------------------------------------------
# navigation / login
# --------------------------------------------------------------------------

AVANTI_CSS = (
    "button:has-text('AVANTI'), button:has-text('Avanti'), button:has-text('avanti'), "
    "input[type='submit'][value*='vant' i], a:has-text('AVANTI'), a:has-text('Avanti')"
)
PRENOTA_CSS = (
    "button:has-text('PRENOTA'), button:has-text('Prenota'), button:has-text('prenota'), "
    "input[type='submit'][value*='prenot' i], a:has-text('PRENOTA'), a:has-text('Prenota')"
)


def goto(page, url: str, cfg: dict, tag: str = "nav") -> None:
    """Navigate with bounded retry/backoff (timeouts, 5xx, resets)."""
    timeout_ms = int(cfg["browser"]["nav_timeout_s"] * 1000)
    delays = [1.0, 2.0, 4.0, 6.0]
    last = None
    for i in range(len(delays) + 1):
        try:
            resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            status = resp.status if resp else 200
            if status >= 500:
                raise RaceError(f"HTTP {status} from {url}")
            page.wait_for_timeout(250)
            return
        except (PWTimeout, RaceError, PWError) as e:
            last = e
            log.warning("goto %s failed (%s/%s): %s", url, i + 1, len(delays), str(e)[:160])
            if i < len(delays):
                time.sleep(delays[i])
    snap(page, f"{tag}-goto-fail", cfg)
    raise RaceError(f"cannot open {url}: {last}")


def looks_logged_in(page) -> bool:
    try:
        if page.locator("input[type='password']").count() > 0:
            return False
    except PWError:
        return False
    txt = body_text(page)
    if re.search(r"\b(esci|logout|log\s*out|profilo|area\s+riservata)\b", txt, re.I):
        return True
    try:
        if page.locator("a[href*='Logout'], a[href*='logout']").count() > 0:
            return True
    except PWError:
        pass
    return False


def ensure_logged_in(page, cfg: dict) -> None:
    if cfg["skip_login"]:
        log.info("login skipped (skip_login=true)")
        if page_url(page).startswith("about:"):
            goto(page, cfg["base_url"].rstrip("/") + "/", cfg, "home")
        return
    if not cfg["email"] or not cfg["password"]:
        raise RaceError("no credentials: set email/password in config or PRENOTAMI_EMAIL / PRENOTAMI_PASSWORD")

    base = cfg["base_url"].rstrip("/")
    if "prenotami.esteri.it" not in page_url(page):
        goto(page, base + "/", cfg, "home")
    if looks_logged_in(page):
        log.info("existing session still valid — no login needed")
        return

    pw = page.locator("input[type='password']")
    if pw.count() == 0:
        # try the site's own "Accedi" entry point (may lead to IAM/pingid SSO)
        for css in ("a:has-text('Accedi')", "button:has-text('Accedi')", "a:has-text('Login')",
                    "button:has-text('Login')", "a[href*='Account/Login']", "a:has-text('Entra')"):
            loc = page.locator(css)
            if loc.count() > 0:
                try:
                    loc.first.click(timeout=5000)
                    page.wait_for_timeout(1200)
                    break
                except PWError:
                    continue
    pw = page.locator("input[type='password']")
    if pw.count() == 0 and not looks_logged_in(page):
        # No password form here yet: it may be behind a button, or the site may
        # bounce us to IAM/pingid SSO (possibly 2FA). Hand over to the human.
        if cfg["login_assist_s"] <= 0:
            raise RaceError("could not find a login form (login_assist_s=0)")
        log.info("waiting up to %.0f s for a login form or manual/SSO login", cfg["login_assist_s"])
        notify("Complete the login in the browser window")
        try:
            page.wait_for_function(
                "() => !!document.querySelector(\"input[type='password']\") || "
                "!!document.querySelector(\"a[href*='Logout'], a[href*='logout']\")",
                timeout=int(cfg["login_assist_s"] * 1000),
            )
        except PWTimeout:
            raise RaceError("no login form appeared and no manual login was completed in time")
        page.wait_for_timeout(300)

    pw = page.locator("input[type='password']")
    if pw.count() == 0:
        log.info("logged in (persisted session or SSO completed)")
        return

    log.info("logging in as %s", cfg["email"])
    try:
        email_sel = "input[type='email'], input[name*='mail' i], input[name*='user' i], #Email, #email, #UserName"
        el = page.locator(email_sel).first
        el.fill(cfg["email"], timeout=8000)
        pw.first.fill(cfg["password"], timeout=8000)
        submit = page.locator(
            "button[type='submit'], input[type='submit'], button:has-text('ACCEDI'), "
            "button:has-text('Accedi'), input[value*='cced' i]"
        ).first
        submit.click(timeout=8000)
    except PWError as e:
        snap(page, "login-fail", cfg)
        raise RaceError(f"login form interaction failed: {e}")

    try:
        page.wait_for_function(
            "() => !document.querySelector(\"input[type='password']\")",
            timeout=10000)
    except PWTimeout:
        pass
    if page.locator("input[type='password']").count() > 0:
        txt = body_text(page)[:300].replace("\n", " ")
        snap(page, "login-rejected", cfg)
        raise RaceError(f"login appears rejected — check credentials. Page says: {txt}")
    log.info("login OK")


def find_service(page, cfg: dict) -> None:
    """Get the browser onto the booking/passport page for the wanted service."""
    def on_passport_page() -> bool:
        try:
            if page.locator("select:visible").count() > 0 and page.locator("input[type='text']:visible").count() > 0:
                return True
            if page.locator(AVANTI_CSS).count() > 0 and page.locator("input[type='text']:visible").count() > 0:
                return True
        except PWError:
            pass
        return False

    if on_passport_page():
        return

    base = cfg["base_url"].rstrip("/")
    if cfg.get("service_url"):
        log.info("opening configured service_url: %s", cfg["service_url"])
        goto(page, cfg["service_url"], cfg, "service-url")
        if on_passport_page():
            return

    # look for a link/row mentioning the service (e.g. LEGALISATIONS / D.O.V.)
    pat = re.compile(cfg["service_match"], re.I)
    for attempt in range(3):
        try:
            texts = page.locator("a, button, td, li, h3, h4, strong, label, option")
            n = min(texts.count(), 400)
            for i in range(n):
                t = texts.nth(i)
                try:
                    if t.is_visible() and pat.search((t.inner_text() or "").strip()[:120]):
                        log.info("clicking service entry: %r", (t.inner_text() or "").strip()[:60])
                        t.click(timeout=5000)
                        page.wait_for_timeout(1500)
                        break
                except PWError:
                    continue
        except PWError:
            pass
        if on_passport_page():
            return
        if cfg.get("service_url"):
            goto(page, cfg["service_url"], cfg, "service-url-retry")
            if on_passport_page():
                return
        page.wait_for_timeout(1000)

    if not on_passport_page():
        snap(page, "no-service-page", cfg)
        raise RaceError(
            "passport page not found. Run with --scout to locate the service entry, "
            "then set service_url (or fix service_match) in the config."
        )


# --------------------------------------------------------------------------
# passport page (step 1)
# --------------------------------------------------------------------------

def drive_selects(page, cfg: dict) -> None:
    rules = cfg.get("selects") or []
    try:
        sels = page.locator("select:visible")
        count = sels.count()
    except PWError:
        count = 0
    log.info("visible dropdowns: %d", count)
    used = set()
    for rule in rules:
        match = rule.get("match", "")
        pick = rule.get("pick", "first")
        chosen = None
        for i in range(count):
            if i in used:
                continue
            sel = sels.nth(i)
            try:
                blob = (sel.get_attribute("id") or "") + " " + (sel.get_attribute("name") or "") + " "
                opts = sel.locator("option").all_inner_texts()
                blob += " ".join(opts)
            except PWError:
                continue
            if match and not re.search(match, blob, re.I):
                continue
            chosen = (i, sel, opts)
            break
        if not chosen:
            continue
        i, sel, opts = chosen
        used.add(i)
        try:
            if pick == "first" or pick == "":
                sel.select_option(index=0, timeout=5000)
                log.info("select #%d -> first option %r", i, (opts[0] if opts else "?")[:50])
            elif pick.startswith("option_re:"):
                pat = re.compile(pick[len("option_re:"):], re.I)
                label = next((o.strip() for o in opts if pat.search(o)), None)
                if label is None:
                    log.warning("select #%d: no option matches %s", i, pick[len("option_re:"):])
                else:
                    sel.select_option(label=label, timeout=5000)
                    log.info("select #%d -> %r", i, label[:50])
            elif pick.startswith("option:"):
                sel.select_option(label=pick[len("option:"):], timeout=5000)
                log.info("select #%d -> option %r", i, pick[len("option:"):])
        except PWError as e:
            log.warning("select #%d interaction failed: %s", i, str(e)[:120])
        page.wait_for_timeout(150)


def fill_passport(page, cfg: dict) -> None:
    num = cfg["passport_number"]
    if not num:
        raise RaceError("passport_number is empty in the config")
    drive_selects(page, cfg)
    page.wait_for_timeout(150)

    # the passport field appears after the list pick
    field = None
    deadline = time.time() + 10
    hints = re.compile(r"pass|doc|codic|numer|travel", re.I)
    while time.time() < deadline and field is None:
        try:
            inputs = page.locator("input[type='text']:visible")
            for i in range(inputs.count()):
                el = inputs.nth(i)
                ident = (el.get_attribute("id") or "") + " " + (el.get_attribute("name") or "") \
                    + " " + (el.get_attribute("placeholder") or "")
                if hints.search(ident):
                    field = el
                    break
            if field is None and inputs.count() > 0:
                field = inputs.last
        except PWError:
            pass
        if field is None:
            page.wait_for_timeout(250)
    if field is None:
        snap(page, "no-passport-field", cfg)
        raise RaceError("passport input field never appeared")

    field.fill(num, timeout=5000)
    log.info("passport number filled")
    page.wait_for_timeout(150)

    # honeypot "textera": the site's JS clears it after you type — wait for it
    try:
        tas = page.locator("textarea")
        for i in range(tas.count()):
            ta = tas.nth(i)
            if not ta.is_visible():
                continue
            t0 = time.time()
            while time.time() - t0 < 3:
                val = ta.input_value() or ""
                if not val.strip():
                    break
                page.wait_for_timeout(80)
            val = ta.input_value() or ""
            if val.strip():
                log.warning("honeypot textarea still holds %d chars after 3s — leaving as-is", len(val))
            else:
                log.info("honeypot textarea clear")
    except PWError as e:
        log.debug("textarea handling: %s", e)

    # the checkbox under it
    try:
        cbs = page.locator("input[type='checkbox']:visible")
        for i in range(cbs.count()):
            cb = cbs.nth(i)
            try:
                if not cb.is_checked():
                    cb.check(timeout=4000)
                    log.info("checkbox #%d checked", i)
            except PWError as e:
                log.warning("checkbox #%d failed: %s", i, str(e)[:100])
    except PWError:
        pass


def wait_captcha(page, cfg: dict) -> bool:
    """Wait for the human to solve the CAPTCHA. Returns True once solved.

    The page's own reCAPTCHA runs untouched — no keys are harvested, nothing is
    solved programmatically. We only *detect* the solved token, then act.
    """
    limit = float(cfg["captcha"]["wait_s"])
    t0 = time.time()
    absent = 0
    next_nudge = 20
    log.info("waiting for you to solve the CAPTCHA (limit %.0f s)", limit)
    while True:
        elapsed = time.time() - t0
        if elapsed > limit:
            log.error("captcha wait timed out after %.0f s", elapsed)
            return False
        try:
            if page.evaluate(_TOKEN_JS):
                log.info("CAPTCHA solved after %.1f s", elapsed)
                return True
            widget = page.evaluate(_WIDGET_JS)
        except PWError as e:
            log.debug("captcha probe failed: %s", str(e)[:120])
            page.wait_for_timeout(200)
            continue
        if not widget:
            absent += 1
            if absent >= 20:  # ~2.4 s with no widget anywhere on the page
                log.info("no captcha widget on this page — proceeding without one")
                return True
        else:
            absent = 0
        if elapsed >= next_nudge:
            notify("Solve the CAPTCHA in the browser window")
            log.info("...still waiting for the CAPTCHA solve (%.0f s)", elapsed)
            next_nudge += 20
        page.wait_for_timeout(120)  # token detection latency directly delays the press


def press_avanti(page, cfg: dict) -> None:
    """Fire AVANTI: in-page synthetic click first (fastest), Playwright
    locator with actionability checks as fallback. Verification that we
    actually advanced belongs to wait_advanced() in the caller."""
    clicked = False
    try:
        clicked = page.evaluate(_AVANTI_JS)
    except PWError:
        clicked = False
    if not clicked:
        try:
            page.locator(AVANTI_CSS).first.click(timeout=6000)
        except PWError as e:
            snap(page, "avanti-fail", cfg)
            raise RaceError(f"AVANTI button not clickable: {e}")
    log.info("AVANTI pressed at %s", datetime.now().strftime("%H:%M:%S.%f")[:-3])


def wait_advanced(page, timeout_s: float = 8.0) -> str:
    """Poll at 60 ms granularity for which step we landed on:
    'password' (session died) | 'receipt' | 'calendar' | 'step1' | 'unknown'.
    One wait_for_function instead of a sleep-loop of separate locator counts."""
    try:
        h = page.wait_for_function(_ADVANCE_JS, timeout=int(timeout_s * 1000), polling=60)
        return h.json_value()
    except (PWTimeout, PWError):
        return "unknown"


def server_skew_s(base_url: str) -> float:
    """(server clock − local clock) in seconds, from the server's own Date
    header. The drop opens on the SERVER's clock, so firing when *its* clock
    hits 00:00 is what wins. Date has 1 s granularity; skew > 10 s rejected."""
    try:
        from email.utils import parsedate_to_datetime
        import urllib.request
        req = urllib.request.Request(base_url.rstrip("/") + "/", method="HEAD")
        with urllib.request.urlopen(req, timeout=8) as r:
            hdr = r.headers.get("Date")
        if not hdr:
            return 0.0
        skew = parsedate_to_datetime(hdr).timestamp() - time.time()
        if abs(skew) > 10:
            log.warning("implausible server clock skew %+.1f s — ignoring", skew)
            return 0.0
        return skew
    except Exception as e:
        log.debug("clock skew check failed: %s", e)
        return 0.0


def _keepalive_fn(page):
    """Fire-and-forget HEAD ping to keep the TLS connection pool warm between
    the warm-up and the drop, so the midnight navigation reuses a live
    connection instead of paying a fresh DNS+TCP+TLS round trip."""
    def ping() -> None:
        try:
            page.evaluate(
                "() => { fetch('/', {method: 'HEAD', cache: 'no-store'}).catch(() => {}); return 0; }"
            )
        except Exception:
            pass
    return ping


_ASSET_RE = re.compile(
    r"\.(png|jpe?g|gif|svg|webp|ico|bmp|woff2?|ttf|otf|eot|mp4|webm|ogg|mp3)(\?|#|$)", re.I)
_SAFE_DOMAINS = re.compile(
    r"recaptcha|googleusercontent|hcaptcha|challenges\.cloudflare|turnstile", re.I)


def set_asset_blocking(page, enabled: bool = True) -> None:
    """Abort image/font/media downloads — the biggest page-load win on a slow
    link. Captcha challenge assets always pass through, so a re-challenge
    stays solvable."""
    if not enabled:
        return
    def handler(route):
        try:
            if _SAFE_DOMAINS.search(route.request.url):
                return route.continue_()
            return route.abort()
        except Exception:
            return route.continue_()
    page.route(_ASSET_RE, handler)


# --------------------------------------------------------------------------
# calendar race (step 2)
# --------------------------------------------------------------------------

def calendar_present(page) -> bool:
    try:
        return page.evaluate(
            "() => document.querySelectorAll('td, .day').length > 0 || "
            "/calendario|calendar|selezion/i.test(document.body ? document.body.innerText : '')"
        )
    except PWError:
        return False


def empty_calendar(page) -> bool:
    return bool(re.search(
        r"nessun[ae]?\s+(appuntamento|risultat|disponibil)|non\s+sono\s+disponibil|"
        r"non\s+ci\s+sono\s+posti|al\s+momento\s+non",
        body_text(page), re.I))


def race(page, cfg: dict, drop_epoch: float) -> str:
    """Poll for the first green date and time slot; press PRENOTA.

    Adaptive: during the hot window right after the drop it polls the local
    DOM every `hot_poll_ms` — zero network cost — then backs off to `poll_ms`.
    Page reloads (the only network hits) run at `reload_hot_s` while hot and
    `reload_if_empty_s` afterwards, one single session throughout. No
    screenshots are taken inside the loop — a capture costs 200-500 ms of
    page execution and would be paid at the worst possible moment.
    """
    st = cfg["strategy"]
    deadline = max(drop_epoch + st["race_window_s"], time.time() + 45)
    hot_until = drop_epoch + float(st.get("hot_window_s", 25))
    hot_poll = max(0.02, float(st.get("hot_poll_ms", 50)) / 1000.0)
    steady_poll = max(0.15, st["poll_ms"] / 1000.0)
    reload_hot = float(st.get("reload_hot_s", 1.2))
    last_refresh = 0.0
    phase = "day"
    log.info("RACE START — hot poll %.0f ms for %.0f s, then %.0f ms — until %s",
             hot_poll * 1000, max(0.0, hot_until - time.time()), steady_poll * 1000,
             datetime.fromtimestamp(deadline).strftime("%H:%M:%S"))

    while time.time() < deadline:
        now = time.time()
        poll = hot_poll if now < hot_until else steady_poll
        reload_gap = reload_hot if now < hot_until else float(st["reload_if_empty_s"])
        try:
            if phase == "day":
                res = page.evaluate(_DAY_JS)
                if res.get("status") == "picked":
                    log.info("GREEN DATE picked: %s", res.get("label"))
                    phase = "slot"
                    # slot list renders right after the day pick — probe fast
                    t1 = time.time()
                    while time.time() - t1 < 6:
                        r2 = page.evaluate(_SLOT_JS)
                        if r2.get("status") == "picked":
                            log.info("GREEN SLOT picked: %s", r2.get("label"))
                            phase = "book"
                            break
                        page.wait_for_timeout(60)
                    continue
                if time.time() - last_refresh > reload_gap and empty_calendar(page):
                    last_refresh = time.time()
                    log.info("calendar empty — refreshing (every %.1f s while hot)", reload_gap)
                    page.reload(timeout=30000, wait_until="domcontentloaded")
                    continue
            elif phase == "slot":
                res = page.evaluate(_SLOT_JS)
                if res.get("status") == "picked":
                    log.info("GREEN SLOT picked: %s", res.get("label"))
                    phase = "book"
                    continue
                if time.time() - last_refresh > reload_gap and empty_calendar(page):
                    last_refresh = time.time()
                    log.info("slots gone — refreshing calendar")
                    page.reload(timeout=30000, wait_until="domcontentloaded")
                    phase = "day"
                    continue
            elif phase == "book":
                try:
                    clicked = page.evaluate(_PRENOTA_JS)
                    if not clicked:
                        btn = page.locator(PRENOTA_CSS).first
                        if btn.count() > 0 and btn.is_enabled():
                            btn.click(timeout=4000)
                            clicked = True
                    if clicked:
                        log.info("PRENOTA pressed at %s", datetime.now().strftime("%H:%M:%S.%f")[:-3])
                        if wait_advanced(page, timeout_s=10) == "receipt":
                            return "receipt"
                        # maybe a confirmation step appeared — look for a confirm button
                        for css in ("button:has-text('CONFERMA')", "input[value*='onferm' i]",
                                    "button:has-text('Conferma')"):
                            c = page.locator(css)
                            if c.count() > 0 and c.first.is_visible():
                                c.first.click(timeout=3000)
                                if wait_advanced(page, timeout_s=6) == "receipt":
                                    return "receipt"
                        phase = "day"
                except PWError as e:
                    log.warning("PRENOTA click failed, retrying: %s", str(e)[:120])
        except PWError as e:
            log.warning("race iteration error: %s — recovering", str(e)[:140])
            snap(page, "race-error", cfg)
            page.wait_for_timeout(300)

        if page.evaluate(_RECEIPT_JS):
            return "receipt"
        page.wait_for_timeout(int(poll * 1000))

    diag = page.evaluate(_CELLS_DIAG_JS)
    log.info("cell diagnostics: %s", json.dumps(diag[:25]))
    return "timeout"


def confirm_receipt(page, cfg: dict) -> Path | None:
    ok = False
    try:
        page.wait_for_function(_RECEIPT_JS, timeout=20000)
        ok = True
    except PWTimeout:
        try:
            ok = bool(page.evaluate(_RECEIPT_JS))
        except PWError:
            ok = False
    shot = snap(page, "RECEIPT" if ok else "no-receipt", cfg)
    if ok:
        log.info("BOOKING CONFIRMED — receipt page reached (%s)", page_url(page))
    else:
        log.warning("receipt marker not found — page saved for inspection")
    return shot


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def run_flow(page, cfg: dict, drop_dt: datetime, start_now: bool) -> None:
    tz_name = cfg["drop"]["timezone"]
    drop_epoch = drop_dt.timestamp()
    log.info("drop target: %s (= %s UTC)", drop_dt.isoformat(),
             drop_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

    # fire on the SERVER's clock: measure skew from its own Date header
    skew = 0.0
    if not start_now and cfg.get("clock", {}).get("apply_server_skew", True):
        skew = server_skew_s(cfg["base_url"])
        if skew:
            log.info("server clock skew %+.1f s — compensating the press time", skew)

    keep_s = float(cfg.get("keepalive_s", 30))
    ping = _keepalive_fn(page) if keep_s > 0 else None

    # ---- pre-drop warm-up window -------------------------------------
    warmup_s = float(cfg.get("warmup_lead_s", 480))
    now = time.time()
    if not start_now and drop_epoch - now > warmup_s:
        spin_until(datetime.fromtimestamp(drop_epoch - warmup_s, tz=ZoneInfo(tz_name)),
                   keepalive=ping, keepalive_s=keep_s)

    ensure_logged_in(page, cfg)
    find_service(page, cfg)
    fill_passport(page, cfg)
    snap(page, "passport-page-ready", cfg)
    log.info("passport page ready — captcha handoff")

    # ---- gate the AVANTI press ---------------------------------------
    # reCAPTCHA tokens die after ~2 minutes, so the captcha window opens just
    # before the press; if the gate bounces us (expired token, service not yet
    # open) the loop re-solves / re-presses until the hard deadline.
    st = cfg["strategy"]
    if start_now:
        click_at = time.time()
    elif st["submit"] == "early":
        click_at = drop_epoch - float(st["early_lead_s"])
    else:
        click_at = drop_epoch - skew  # the SERVER's midnight, not our idea of it
    if skew:
        log.info("press target compensated to %s (server clock)",
                 datetime.fromtimestamp(click_at, tz=ZoneInfo(tz_name)).isoformat())

    hard_deadline = drop_epoch + float(st["race_window_s"]) + 60
    advanced = False
    assets_on = False
    bounce_n = 0
    while time.time() < hard_deadline:
        hold_until = click_at - float(cfg["captcha_open_s"])
        if time.time() < hold_until:
            log.info("holding %.0f s before opening the captcha window (tokens expire ~2 min)",
                     hold_until - time.time())
            spin_until(datetime.fromtimestamp(hold_until, tz=ZoneInfo(tz_name)),
                       keepalive=ping, keepalive_s=keep_s)

        if not wait_captcha(page, cfg):
            log.warning("captcha wait timed out — re-checking shortly")
            page.wait_for_timeout(2000)
            continue
        if cfg.get("block_assets", True) and not assets_on:
            set_asset_blocking(page, True)
            assets_on = True

        if time.time() < click_at:
            log.info("armed — pressing AVANTI in %.1f s", click_at - time.time())
            spin_until(datetime.fromtimestamp(click_at, tz=ZoneInfo(tz_name)),
                       keepalive=ping, keepalive_s=keep_s)
        press_avanti(page, cfg)

        kind = wait_advanced(page, timeout_s=8)
        if kind in ("calendar", "receipt"):
            advanced = True
            break
        if kind == "password":
            raise RaceError("session expired mid-submit — keep the login session valid")
        # 'step1' / 'unknown' — bounced; fall through to the retry path below
        if advanced:
            break
        log.warning("did not advance past AVANTI — bouncing back and retrying")
        if bounce_n == 0:
            snap(page, "avanti-bounce", cfg)  # screenshot costs ~300 ms; once is enough
        bounce_n += 1
        if page.locator(AVANTI_CSS).count() == 0:
            try:
                page.go_back(timeout=15000, wait_until="domcontentloaded")
                page.wait_for_timeout(500)
            except PWError:
                goto(page, cfg["base_url"].rstrip("/") + "/", cfg, "recover")
                ensure_logged_in(page, cfg)
                find_service(page, cfg)
        fill_passport(page, cfg)

    if not advanced:
        raise RaceError("could not reach the calendar before the deadline")

    outcome = race(page, cfg, drop_epoch)
    if outcome != "receipt":
        snap(page, f"race-{outcome}", cfg)
        raise RaceError(
            f"race ended with '{outcome}' — no booking confirmed. "
            "Cell diagnostics are in the log; tune selectors/service_url and try again."
        )
    shot = confirm_receipt(page, cfg)
    notify("Slot booked — check the browser window")
    cmd = cfg.get("on_success", "")
    if cmd and shot:
        try:
            subprocess.run(cmd.format(receipt=str(shot)), shell=True, timeout=30)
        except Exception as e:
            log.warning("on_success command failed: %s", e)


def scout(page, cfg: dict) -> None:
    """Open the site, let the human explore, and dump an element inventory."""
    base = cfg["base_url"].rstrip("/")
    goto(page, base + "/", cfg, "scout-home")
    ensure_logged_in(page, cfg)
    print("\n=== SCOUT MODE ===")
    print("Navigate manually to the service/passport/calendar pages.")
    print("The bot will snapshot every page change. Press Enter here when done.\n")
    Path("scout").mkdir(exist_ok=True)
    seen = set()

    def dump(tag: str) -> None:
        try:
            snap(page, tag, cfg)
            inv = page.evaluate("""() => {
              const out = [];
              for (const el of document.querySelectorAll('a, button, input, select, textarea, td, .day, .slot')) {
                const t = (el.innerText || el.value || el.getAttribute('placeholder') || '').replace(/\\s+/g,' ').trim();
                out.push({
                  tag: el.tagName, text: t.slice(0, 70), id: el.id || '',
                  name: el.getAttribute('name') || '', cls: String(el.className || '').slice(0, 60),
                  bg: getComputedStyle(el).backgroundColor,
                  href: (el.getAttribute('href') || '').slice(0, 90)
                });
                if (out.length >= 500) break;
              }
              return out;
            }""")
            Path(f"scout/inventory-{tag}.json").write_text(json.dumps(inv, indent=1), encoding="utf-8")
            log.info("dumped %d elements for %s", len(inv), tag)
        except Exception as e:
            log.debug("dump failed: %s", e)

    last_url = ""
    n = 0
    while True:
        try:
            u = page_url(page)
            if u != last_url:
                last_url = u
                n += 1
                dump(f"{n:02d}-" + re.sub(r"\W+", "_", u)[-50:])
        except Exception:
            pass
        ans = input("scout> Enter = snapshot current page, 'done' + Enter = finish: ")
        if ans.strip().lower() in ("done", "q", "exit", "quit"):
            break
        n += 1
        dump(f"{n:02d}-manual")
    log.info("scout finished — inventories in ./scout/, screenshots in %s", cfg["screenshots_dir"])


def main() -> int:
    ap = argparse.ArgumentParser(description="prenot@mi single-session booking racer")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--start-now", action="store_true", help="skip the schedule wait")
    ap.add_argument("--scout", action="store_true", help="explore the site and dump element inventories")
    ap.add_argument("--headless", action="store_true", help="run headless (selftest/CI)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    if not Path(args.config).exists():
        print(f"config not found: {args.config}\nCopy config.example.json to config.json and fill it in.",
              file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    log_file = setup_logging(cfg, args.log_level)
    log.info("log file: %s", log_file)
    if args.headless:
        cfg["browser"]["headless"] = True

    if not args.start_now and not args.scout:
        drop_dt = next_drop(cfg)
        log.info("next drop: %s  (Rome midnight; 23:00 in Tunis while Italy is on CEST)",
                 drop_dt.isoformat())
    else:
        drop_dt = now_tz(cfg["drop"]["timezone"])

    profile = Path(cfg["browser"]["profile_dir"])
    profile.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile),
            headless=bool(cfg["browser"]["headless"]),
            viewport={"width": 1365, "height": 900},
            locale="it-IT",
            timezone_id=cfg["drop"]["timezone"],
            args=["--disable-features=TranslateUI"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(int(cfg["browser"]["nav_timeout_s"] * 1000))
        try:
            if args.scout:
                scout(page, cfg)
            else:
                run_flow(page, cfg, drop_dt, args.start_now)
                keep = float(cfg.get("keep_open_s", 0))
                if keep > 0:
                    log.info("flow done — keeping the browser open for %.0f s", keep)
                    page.wait_for_timeout(int(keep * 1000))
        except RaceError as e:
            log.error("FAILED: %s", e)
            snap(page, "final-failure", cfg)
            exit_code = 1
        except KeyboardInterrupt:
            log.warning("interrupted by user")
            snap(page, "interrupted", cfg)
            exit_code = 130
        except Exception as e:  # pragma: no cover
            log.exception("unexpected error: %s", e)
            snap(page, "crash", cfg)
            exit_code = 1
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    log.info("done, exit=%d", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
