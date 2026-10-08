#!/usr/bin/env python3
"""
T0 — prenot@mi midnight racer
https://prenotami.esteri.it  ·  one real browser, one real session

The hot path (AVANTI → green day → green slot → PRENOTA) runs INSIDE the page:
MutationObserver + rAF, injected at document-start on every navigation. Python
only arms the clock and supervises. A 50 ms CDP poll cannot beat a same-turn
click on the node that just appeared.

Still out of scope (deliberate): captcha solving, fingerprint spoofing,
parallel sessions, hammering internal APIs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
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
    print("Playwright is missing. Run:  pip install playwright && playwright install chromium",
          file=sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

DEFAULTS = {
    "base_url": "https://prenotami.esteri.it",
    "email": "",
    "password": "",
    "skip_login": False,
    "login_assist_s": 420,
    "service_match": "legaliz|legalizz|d\\.o\\.v|legalis",
    "service_url": "",
    "consulate_match": "",
    "selects": [
        {"match": "", "pick": "first"},
        {"match": "tunisi", "pick": "option_re:tunisi"},
    ],
    "passport_number": "",
    "drop": {"time": "00:00", "timezone": "Europe/Rome", "start_early_s": 0.0},
    "strategy": {
        "submit": "at_drop",          # "at_drop" | "early"
        "early_lead_s": 25.0,
        "race_window_s": 120.0,
        "poll_ms": 200,               # fallback Python poll only
        "hot_poll_ms": 16,            # fallback Python poll in the hot window
        "hot_window_s": 40.0,
        "reload_if_empty_s": 1.5,
        "reload_hot_s": 0.4,          # in-page reload cadence while hot
        "burst_s": 6.0,               # re-press AVANTI window if the gate bounces
        "advance_poll_ms": 16,
    },
    "captcha": {"wait_s": 900.0},
    "captcha_open_s": 150.0,
    "warmup_lead_s": 480.0,
    "keepalive_s": 12.0,
    "block_assets": True,
    "clock": {"apply_server_skew": True, "samples": 7},
    "browser": {"headless": False, "profile_dir": "./profile", "nav_timeout_s": 45.0},
    "screenshots_dir": "./shots",
    "on_success": "",
    "keep_open_s": 0,
}

log = logging.getLogger("t0")

CHROME_FAST_ARGS = [
    "--disable-features=TranslateUI,BackForwardCache,MediaRouter,OptimizationHints,InterestFeedContentSuggestions",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-ipc-flooding-protection",
    "--disable-hang-monitor",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-first-run",
    "--password-store=basic",
]


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
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e", f'display notification "{msg}" with title "T0 racer"'],
                timeout=5, capture_output=True,
            )
        except Exception:
            pass


def now_tz(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def next_drop(cfg: dict, now: datetime | None = None) -> datetime:
    """Next occurrence of drop.time in drop.timezone (defaults to Europe/Rome)."""
    d = cfg["drop"]
    tz = ZoneInfo(d["timezone"])
    now = now.astimezone(tz) if now else datetime.now(tz)
    hh, mm = (int(x) for x in d["time"].split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def spin_until(target: datetime, early_s: float = 0.0,
               keepalive=None, keepalive_s: float = 12.0) -> None:
    """Sleep to `target` (minus early_s) with busy-wait on the last 8 ms."""
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
            time.sleep(min(10.0, remaining - 20))
        elif remaining > 1.0:
            time.sleep(min(0.12, remaining - 0.4))
        elif remaining > 0.008:
            time.sleep(0.0004)
    log.info("DROP TIME REACHED — local %s", datetime.now().strftime("%H:%M:%S.%f")[:-3])


def load_t0_js() -> str:
    p = Path(__file__).resolve().parent / "t0.js"
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise RaceError("t0.js is missing next to bot.py — re-copy the racer package")


# --------------------------------------------------------------------------
# page JS (fallback probes — the live race uses t0.js)
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
  if (window.__T0 && window.__T0.captchaOk) return window.__T0.captchaOk();
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
  if (window.__T0 && window.__T0.booked) return true;
  const t = (document.body ? document.body.innerText : '');
  return /prenotazione\s+confermat|prenotato\s+confermat|conferma\s+di\s+prenotaz|booking\s+confirm|riepilogo\s+della\s+prenotazione/i.test(t);
}
"""

_AVANTI_JS = r"""
() => {
  for (const el of document.querySelectorAll('button, input[type="submit"], a')) {
    const t = (el.value || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!/^avanti\b/i.test(t)) continue;
    if (el.disabled) continue;
    try {
      el.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, cancelable:true, pointerType:'mouse'}));
      el.dispatchEvent(new PointerEvent('pointerup', {bubbles:true, cancelable:true, pointerType:'mouse'}));
    } catch (e) {}
    el.click();
    if (el.form && el.type === 'submit') { try { el.form.requestSubmit(el); } catch (e) {} }
    return true;
  }
  return false;
}
"""

_PRENOTA_JS = r"""
() => {
  const rx = /^prenot(a|ami|are|azione)\b/i;
  const cands = [...document.querySelectorAll('button, input[type="submit"], a')];
  for (const el of cands) {
    const t = (el.value || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!rx.test(t) || el.disabled) continue;
    el.click();
    return true;
  }
  return false;
}
"""

_ADVANCE_JS = r"""
() => {
  if (window.__T0 && window.__T0.booked) return 'receipt';
  if (document.querySelector("input[type='password']")) return 'password';
  const t = document.body ? document.body.innerText : '';
  if (/prenotazione\s+confermat|prenotato\s+confermat|conferma\s+di\s+prenotaz|booking\s+confirm|riepilogo\s+della\s+prenotazione/i.test(t)) return 'receipt';
  const n = document.querySelectorAll('table td, .day').length;
  if (n >= 7 || /calendario|calendar/i.test(t)) return 'calendar';
  for (const el of document.querySelectorAll('button, input[type="submit"], a')) {
    const s = (el.value || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (/^avanti\b/i.test(s)) return 'step1';
  }
  return '';
}
"""

_FIND_SERVICE_JS = r"""
(pat) => {
  const re = new RegExp(pat, 'i');
  const nodes = document.querySelectorAll('a, button, td, li, h3, h4, strong, label');
  for (const el of nodes) {
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t && re.test(t.slice(0, 160))) {
      el.click();
      return t.slice(0, 80);
    }
  }
  return null;
}
"""

_LOGGED_IN_JS = r"""
() => {
  if (document.querySelector("input[type='password']")) return false;
  const t = document.body ? document.body.innerText : '';
  if (/\b(esci|logout|log\s*out|profilo|area\s+riservata)\b/i.test(t)) return true;
  if (document.querySelector("a[href*='Logout'], a[href*='logout']")) return true;
  return false;
}
"""


AVANTI_CSS = (
    "button:has-text('AVANTI'), button:has-text('Avanti'), button:has-text('avanti'), "
    "input[type='submit'][value*='vant' i], a:has-text('AVANTI'), a:has-text('Avanti')"
)
PRENOTA_CSS = (
    "button:has-text('PRENOTA'), button:has-text('Prenota'), button:has-text('prenota'), "
    "input[type='submit'][value*='prenot' i], a:has-text('PRENOTA'), a:has-text('Prenota')"
)


def goto(page, url: str, cfg: dict, tag: str = "nav", settle_ms: int = 80) -> None:
    timeout_ms = int(cfg["browser"]["nav_timeout_s"] * 1000)
    delays = [0.6, 1.2, 2.4, 4.0]
    last = None
    for i in range(len(delays) + 1):
        try:
            resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            status = resp.status if resp else 200
            if status >= 500:
                raise RaceError(f"HTTP {status} from {url}")
            if settle_ms > 0:
                page.wait_for_timeout(settle_ms)
            return
        except (PWTimeout, RaceError, PWError) as e:
            last = e
            log.warning("goto %s failed (%s/%s): %s", url, i + 1, len(delays), str(e)[:160])
            if i < len(delays):
                time.sleep(delays[i])
    snap(page, f"{tag}-goto-fail", cfg)
    raise RaceError(f"cannot open {url}: {last}")


def page_url(page) -> str:
    try:
        return page.url
    except Exception:
        return ""


def looks_logged_in(page) -> bool:
    try:
        return bool(page.evaluate(_LOGGED_IN_JS))
    except PWError:
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
        for css in ("a:has-text('Accedi')", "button:has-text('Accedi')", "a:has-text('Login')",
                    "button:has-text('Login')", "a[href*='Account/Login']", "a:has-text('Entra')"):
            loc = page.locator(css)
            if loc.count() > 0:
                try:
                    loc.first.click(timeout=4000)
                    page.wait_for_timeout(600)
                    break
                except PWError:
                    continue
    pw = page.locator("input[type='password']")
    if pw.count() == 0 and not looks_logged_in(page):
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
        page.wait_for_timeout(200)

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
        txt = ""
        try:
            txt = (page.evaluate("() => (document.body && document.body.innerText) || ''") or "")[:300]
        except PWError:
            pass
        snap(page, "login-rejected", cfg)
        raise RaceError(f"login appears rejected — check credentials. Page says: {txt.replace(chr(10), ' ')}")
    log.info("login OK")


def find_service(page, cfg: dict) -> None:
    def on_passport_page() -> bool:
        try:
            return bool(page.evaluate("""() => {
              const sel = [...document.querySelectorAll('select')].some(e => e.offsetParent);
              const inp = [...document.querySelectorAll("input[type='text']")].some(e => e.offsetParent);
              if (sel && inp) return true;
              const avanti = [...document.querySelectorAll('button, input[type=submit], a')]
                .some(el => /^avanti\\b/i.test((el.value || el.textContent || '').trim()));
              return avanti && inp;
            }"""))
        except PWError:
            return False

    if on_passport_page():
        return

    if cfg.get("service_url"):
        log.info("opening configured service_url: %s", cfg["service_url"])
        goto(page, cfg["service_url"], cfg, "service-url")
        if on_passport_page():
            return

    for attempt in range(3):
        try:
            hit = page.evaluate(_FIND_SERVICE_JS, cfg["service_match"])
            if hit:
                log.info("clicking service entry: %r", hit)
                page.wait_for_timeout(400)
        except PWError:
            pass
        if on_passport_page():
            return
        if cfg.get("service_url"):
            goto(page, cfg["service_url"], cfg, "service-url-retry")
            if on_passport_page():
                return
        page.wait_for_timeout(500)

    snap(page, "no-service-page", cfg)
    raise RaceError(
        "passport page not found. Run with --scout to locate the service entry, "
        "then set service_url (or fix service_match) in the config."
    )


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
                sel.select_option(index=0, timeout=4000)
                log.info("select #%d -> first option %r", i, (opts[0] if opts else "?")[:50])
            elif pick.startswith("option_re:"):
                pat = re.compile(pick[len("option_re:"):], re.I)
                label = next((o.strip() for o in opts if pat.search(o)), None)
                if label is None:
                    log.warning("select #%d: no option matches %s", i, pick[len("option_re:"):])
                else:
                    sel.select_option(label=label, timeout=4000)
                    log.info("select #%d -> %r", i, label[:50])
            elif pick.startswith("option:"):
                sel.select_option(label=pick[len("option:"):], timeout=4000)
                log.info("select #%d -> option %r", i, pick[len("option:"):])
        except PWError as e:
            log.warning("select #%d interaction failed: %s", i, str(e)[:120])
        page.wait_for_timeout(80)


def fill_passport(page, cfg: dict) -> None:
    num = cfg["passport_number"]
    if not num:
        raise RaceError("passport_number is empty in the config")
    drive_selects(page, cfg)

    field = None
    deadline = time.time() + 8
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
            page.wait_for_timeout(120)
    if field is None:
        snap(page, "no-passport-field", cfg)
        raise RaceError("passport input field never appeared")

    field.fill(num, timeout=4000)
    log.info("passport number filled")

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
                page.wait_for_timeout(40)
            val = ta.input_value() or ""
            if val.strip():
                log.warning("honeypot textarea still holds %d chars after 3s — leaving as-is", len(val))
            else:
                log.info("honeypot textarea clear")
    except PWError as e:
        log.debug("textarea handling: %s", e)

    try:
        cbs = page.locator("input[type='checkbox']:visible")
        for i in range(cbs.count()):
            cb = cbs.nth(i)
            try:
                if not cb.is_checked():
                    cb.check(timeout=3000)
                    log.info("checkbox #%d checked", i)
            except PWError as e:
                log.warning("checkbox #%d failed: %s", i, str(e)[:100])
    except PWError:
        pass


def wait_captcha(page, cfg: dict) -> bool:
    limit = float(cfg["captcha"]["wait_s"])
    t0 = time.time()
    next_nudge = 20
    log.info("waiting for you to solve the CAPTCHA (limit %.0f s)", limit)
    try:
        page.wait_for_function(
            """() => {
              if (window.__T0 && window.__T0.captchaOk && window.__T0.captchaOk()) return 'ok';
              const sels = [
                'textarea[name="g-recaptcha-response"]','input[name="g-recaptcha-response"]',
                'textarea[name="h-captcha-response"]','input[name="h-captcha-response"]',
                '[name="cf-turnstile-response"]'
              ];
              for (const s of sels) {
                const el = document.querySelector(s);
                if (el && el.value && el.value.trim().length > 15) return 'ok';
              }
              if (document.body && document.body.getAttribute('data-captcha-ok') === 'true') return 'ok';
              const widget = document.querySelector(
                'iframe[src*="recaptcha"], iframe[title*="reCAPTCHA"], iframe[title*="captcha"], ' +
                'iframe[src*="hcaptcha"], .g-recaptcha, .h-captcha, .cf-turnstile, .grecaptcha-badge');
              if (!widget) return 'none';
              return false;
            }""",
            timeout=min(2500, int(limit * 1000)),
            polling=20,
        )
    except (PWTimeout, PWError):
        pass

    absent = 0
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
            page.wait_for_timeout(80)
            continue
        if not widget:
            absent += 1
            if absent >= 12:
                log.info("no captcha widget on this page — proceeding without one")
                return True
        else:
            absent = 0
        if elapsed >= next_nudge:
            notify("Solve the CAPTCHA in the browser window")
            log.info("...still waiting for the CAPTCHA solve (%.0f s)", elapsed)
            next_nudge += 20
        page.wait_for_timeout(40)


def press_avanti(page, cfg: dict) -> None:
    clicked = False
    try:
        clicked = page.evaluate(_AVANTI_JS)
    except PWError:
        clicked = False
    if not clicked:
        try:
            page.locator(AVANTI_CSS).first.click(timeout=4000)
        except PWError as e:
            snap(page, "avanti-fail", cfg)
            raise RaceError(f"AVANTI button not clickable: {e}")
    log.info("AVANTI pressed at %s", datetime.now().strftime("%H:%M:%S.%f")[:-3])


def wait_advanced(page, timeout_s: float = 0.4, poll_ms: int = 16) -> str:
    try:
        h = page.wait_for_function(_ADVANCE_JS, timeout=int(timeout_s * 1000), polling=poll_ms)
        return h.json_value()
    except (PWTimeout, PWError):
        return "unknown"


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    return float(statistics.median(xs))


def server_skew_s(base_url: str, samples: int = 7) -> float:
    """Median (server − local) seconds via NTP-style HTTP Date sampling.

    Date is 1 s resolution; we take the midpoint of each request's RTT so a
    cluster of samples still beats a single ±1 s reading. Outliers > 10 s drop.
    """
    from email.utils import parsedate_to_datetime
    import urllib.request

    offsets: list[float] = []
    url = base_url.rstrip("/") + "/"
    for _ in range(max(1, samples)):
        try:
            req = urllib.request.Request(url, method="HEAD")
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=6) as r:
                hdr = r.headers.get("Date")
            t1 = time.time()
            if not hdr:
                continue
            mid = (t0 + t1) / 2.0
            skew = parsedate_to_datetime(hdr).timestamp() - mid
            if abs(skew) <= 10:
                offsets.append(skew)
        except Exception as e:
            log.debug("clock sample failed: %s", e)
        time.sleep(0.04)
    if not offsets:
        return 0.0
    med = _median(offsets)
    log.info("clock skew samples=%s median=%+.3f s  (rtt-midpoint)",
             ",".join(f"{x:+.3f}" for x in offsets), med)
    return med


def _keepalive_fn(page):
    def ping() -> None:
        try:
            page.evaluate(
                """() => {
                  try { fetch(location.href, {method: 'HEAD', cache: 'no-store'}).catch(() => {}); } catch (e) {}
                  try { fetch('/', {method: 'HEAD', cache: 'no-store'}).catch(() => {}); } catch (e) {}
                  return 0;
                }"""
            )
        except Exception:
            pass
    return ping


_ASSET_RE = re.compile(
    r"\.(png|jpe?g|gif|svg|webp|ico|bmp|woff2?|ttf|otf|eot|mp4|webm|ogg|mp3)(\?|#|$)", re.I)
_SAFE_DOMAINS = re.compile(
    r"recaptcha|googleusercontent|hcaptcha|challenges\.cloudflare|turnstile", re.I)
_TRACKER_RE = re.compile(
    r"google-analytics|googletagmanager|doubleclick|facebook\.net|hotjar|clarity\.ms|tiktok|scorecardresearch",
    re.I,
)


def install_network_filters(context, page, cfg: dict) -> None:
    def trackers(route):
        try:
            if _SAFE_DOMAINS.search(route.request.url):
                return route.continue_()
            return route.abort()
        except Exception:
            return route.continue_()

    try:
        context.route(_TRACKER_RE, trackers)
    except Exception:
        pass
    if cfg.get("block_assets", True):
        set_asset_blocking(page, True)


def set_asset_blocking(page, enabled: bool = True) -> None:
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


def bind_t0_console(page) -> None:
    def on_console(msg):
        try:
            t = msg.text
        except Exception:
            return
        if t.startswith("[T0]"):
            log.info("%s", t)
    page.on("console", on_console)


def t0_status(page) -> dict:
    try:
        st = page.evaluate("() => (window.__T0 && window.__T0.status) ? window.__T0.status() : null")
        return st or {}
    except PWError:
        return {}


def t0_present(page) -> bool:
    try:
        return bool(page.evaluate("() => !!(window.__T0 && window.__T0.status)"))
    except PWError:
        return False


def configure_t0(page, cfg: dict, drop_epoch: float) -> None:
    st = cfg["strategy"]
    spec = {
        "reloadHotMs": int(float(st.get("reload_hot_s", 0.4)) * 1000),
        "reloadSteadyMs": int(float(st.get("reload_if_empty_s", 1.5)) * 1000),
        "hotWindowMs": int(float(st.get("hot_window_s", 40)) * 1000),
        "dropEpochMs": int(drop_epoch * 1000),
        "maxReloads": 180,
    }
    try:
        page.evaluate(
            """(c) => { if (window.__T0 && window.__T0.configure) window.__T0.configure(c); }""",
            spec,
        )
    except PWError:
        pass


def arm_t0(page, click_at: float, cfg: dict, drop_epoch: float) -> bool:
    remaining_ms = max(0.0, (click_at - time.time()) * 1000.0)
    configure_t0(page, cfg, drop_epoch)
    try:
        ok = page.evaluate(
            """(ms) => {
              if (!window.__T0 || !window.__T0.arm) return false;
              window.__T0.arm(performance.now() + ms);
              return true;
            }""",
            remaining_ms,
        )
        return bool(ok)
    except PWError:
        return False


def hover_avanti(page) -> None:
    try:
        loc = page.locator(AVANTI_CSS).first
        if loc.count() > 0:
            loc.hover(timeout=800)
    except PWError:
        pass


def empty_calendar(page) -> bool:
    try:
        return bool(page.evaluate(
            r"""() => /nessun[ae]?\s+(appuntamento|risultat|disponibil)|non\s+sono\s+disponibil|non\s+ci\s+sono\s+posti|al\s+momento\s+non/i
              .test(document.body ? document.body.innerText : '')"""
        ))
    except PWError:
        return False


def race_t0(page, cfg: dict, drop_epoch: float) -> str:
    """Supervise the in-page racer. Clicks are not issued from Python."""
    st = cfg["strategy"]
    deadline = max(drop_epoch + float(st["race_window_s"]), time.time() + 45)
    configure_t0(page, cfg, drop_epoch)
    try:
        page.evaluate("() => { if (window.__T0 && window.__T0.raceNow) window.__T0.raceNow(); }")
    except PWError:
        pass
    log.info("RACE START — in-page T0 observer until %s",
             datetime.fromtimestamp(deadline).strftime("%H:%M:%S"))
    seen_day = seen_slot = seen_pren = False
    last_log = 0.0
    while time.time() < deadline:
        s = t0_status(page)
        if s.get("lastDay") and not seen_day:
            seen_day = True
            log.info("GREEN DATE picked: %s", s.get("lastDay"))
        if s.get("lastSlot") and not seen_slot:
            seen_slot = True
            log.info("GREEN SLOT picked: %s", s.get("lastSlot"))
        if s.get("lastPrenotaWall") and not seen_pren:
            seen_pren = True
            wall = datetime.fromtimestamp(s["lastPrenotaWall"] / 1000.0)
            log.info("PRENOTA pressed at %s", wall.strftime("%H:%M:%S.%f")[:-3])
        if s.get("booked"):
            return "receipt"
        try:
            if page.evaluate(_RECEIPT_JS):
                return "receipt"
        except PWError:
            pass
        now = time.time()
        if now - last_log > 5:
            log.info("racing — phase=%s day=%s slot=%s",
                     s.get("phase"), s.get("lastDay") or "-", s.get("lastSlot") or "-")
            last_log = now
        try:
            page.wait_for_timeout(16)
        except PWError:
            time.sleep(0.016)
    return "timeout"


def race_fallback(page, cfg: dict, drop_epoch: float) -> str:
    """Python CDP loop — only if t0.js did not inject."""
    st = cfg["strategy"]
    deadline = max(drop_epoch + st["race_window_s"], time.time() + 45)
    hot_until = drop_epoch + float(st.get("hot_window_s", 40))
    hot_poll = max(0.012, float(st.get("hot_poll_ms", 16)) / 1000.0)
    steady_poll = max(0.08, st["poll_ms"] / 1000.0)
    reload_hot = float(st.get("reload_hot_s", 0.4))
    last_refresh = 0.0
    phase = "day"
    log.info("RACE START — fallback CDP poll until %s",
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
                    t1 = time.time()
                    while time.time() - t1 < 4:
                        r2 = page.evaluate(_SLOT_JS)
                        if r2.get("status") == "picked":
                            log.info("GREEN SLOT picked: %s", r2.get("label"))
                            phase = "book"
                            break
                        page.wait_for_timeout(16)
                    continue
                if time.time() - last_refresh > reload_gap and empty_calendar(page):
                    last_refresh = time.time()
                    log.info("calendar empty — refreshing (every %.1f s while hot)", reload_gap)
                    page.reload(timeout=20000, wait_until="commit")
                    continue
            elif phase == "slot":
                res = page.evaluate(_SLOT_JS)
                if res.get("status") == "picked":
                    log.info("GREEN SLOT picked: %s", res.get("label"))
                    phase = "book"
                    continue
                if time.time() - last_refresh > reload_gap and empty_calendar(page):
                    last_refresh = time.time()
                    page.reload(timeout=20000, wait_until="commit")
                    phase = "day"
                    continue
            elif phase == "book":
                clicked = page.evaluate(_PRENOTA_JS)
                if not clicked:
                    btn = page.locator(PRENOTA_CSS).first
                    if btn.count() > 0:
                        btn.click(timeout=2500)
                        clicked = True
                if clicked:
                    log.info("PRENOTA pressed at %s", datetime.now().strftime("%H:%M:%S.%f")[:-3])
                    if wait_advanced(page, timeout_s=8) == "receipt":
                        return "receipt"
                    phase = "day"
        except PWError as e:
            log.warning("race iteration error: %s", str(e)[:140])
            page.wait_for_timeout(120)
        try:
            if page.evaluate(_RECEIPT_JS):
                return "receipt"
        except PWError:
            pass
        page.wait_for_timeout(int(poll * 1000))

    try:
        log.info("cell diagnostics: %s", json.dumps(page.evaluate(_CELLS_DIAG_JS)[:25]))
    except PWError:
        pass
    return "timeout"


def race(page, cfg: dict, drop_epoch: float) -> str:
    if t0_present(page):
        return race_t0(page, cfg, drop_epoch)
    return race_fallback(page, cfg, drop_epoch)


def confirm_receipt(page, cfg: dict) -> Path | None:
    ok = False
    try:
        page.wait_for_function(_RECEIPT_JS, timeout=12000, polling=20)
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


def burst_avanti(page, cfg: dict, click_at: float) -> str:
    """Press AVANTI at T-0 and re-press every ~80 ms if the gate bounces.

    The old 8 s wait_for_function after a too-early press was a drop-killer:
    a 200 ms-early click would sit idle through the actual midnight window.
    """
    st = cfg["strategy"]
    burst_s = float(st.get("burst_s", 6.0))
    poll = int(st.get("advance_poll_ms", 16))
    end = max(click_at + burst_s, time.time() + 1.0)
    presses = 0
    while time.time() < end:
        kind = wait_advanced(page, timeout_s=0.12, poll_ms=poll)
        if kind in ("calendar", "receipt"):
            return kind
        if kind == "password":
            raise RaceError("session expired mid-submit — keep the login session valid")
        try:
            press_avanti(page, cfg)
            presses += 1
        except RaceError:
            kind = wait_advanced(page, timeout_s=0.25, poll_ms=poll)
            if kind in ("calendar", "receipt"):
                return kind
            raise
    log.warning("burst ended after %d AVANTI presses without advancing", presses)
    return wait_advanced(page, timeout_s=1.5, poll_ms=poll)


def run_flow(page, cfg: dict, drop_dt: datetime, start_now: bool) -> None:
    tz_name = cfg["drop"]["timezone"]
    drop_epoch = drop_dt.timestamp()
    log.info("drop target: %s (= %s UTC)", drop_dt.isoformat(),
             drop_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

    skew = 0.0
    if not start_now and cfg.get("clock", {}).get("apply_server_skew", True):
        skew = server_skew_s(cfg["base_url"], int(cfg.get("clock", {}).get("samples", 7)))
        if skew:
            log.info("server clock skew %+.3f s — compensating the press time", skew)

    keep_s = float(cfg.get("keepalive_s", 12))
    ping = _keepalive_fn(page) if keep_s > 0 else None

    warmup_s = float(cfg.get("warmup_lead_s", 480))
    now = time.time()
    if not start_now and drop_epoch - now > warmup_s:
        spin_until(datetime.fromtimestamp(drop_epoch - warmup_s, tz=ZoneInfo(tz_name)),
                   keepalive=ping, keepalive_s=keep_s)

    ensure_logged_in(page, cfg)
    find_service(page, cfg)
    fill_passport(page, cfg)
    if start_now or (drop_epoch - time.time() > 8):
        snap(page, "passport-page-ready", cfg)
    log.info("passport page ready — captcha handoff")

    st = cfg["strategy"]
    early = float(cfg.get("drop", {}).get("start_early_s", 0) or 0)
    if start_now:
        click_at = time.time()
    elif st["submit"] == "early":
        click_at = drop_epoch - float(st["early_lead_s"]) - skew
    else:
        click_at = drop_epoch - skew - early
    if skew or early:
        log.info("press target %s (server-compensated, early=%.3fs)",
                 datetime.fromtimestamp(click_at, tz=ZoneInfo(tz_name)).isoformat(), early)

    hard_deadline = drop_epoch + float(st["race_window_s"]) + 60
    advanced = False
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
            page.wait_for_timeout(800)
            continue

        hover_avanti(page)
        armed = False
        if time.time() < click_at:
            log.info("armed — T0 fires AVANTI in %.3f s", click_at - time.time())
            armed = arm_t0(page, click_at, cfg, drop_epoch)
            spin_until(datetime.fromtimestamp(click_at, tz=ZoneInfo(tz_name)),
                       keepalive=ping, keepalive_s=keep_s)
        else:
            armed = arm_t0(page, time.time(), cfg, drop_epoch)

        kind = burst_avanti(page, cfg, click_at)
        if kind in ("calendar", "receipt"):
            advanced = True
            break
        log.warning("did not advance past AVANTI — bouncing back and retrying")
        if bounce_n == 0 and (drop_epoch - time.time() > 5):
            snap(page, "avanti-bounce", cfg)
        bounce_n += 1
        try:
            if page.evaluate("""() => ![...document.querySelectorAll('button,a,input')].some(el =>
              /^avanti\\b/i.test((el.value||el.textContent||'').trim()))"""):
                try:
                    page.go_back(timeout=10000, wait_until="domcontentloaded")
                    page.wait_for_timeout(200)
                except PWError:
                    goto(page, cfg["base_url"].rstrip("/") + "/", cfg, "recover", settle_ms=80)
                    ensure_logged_in(page, cfg)
                    find_service(page, cfg)
        except PWError:
            pass
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
    base = cfg["base_url"].rstrip("/")
    goto(page, base + "/", cfg, "scout-home")
    ensure_logged_in(page, cfg)
    print("\n=== SCOUT MODE ===")
    print("Navigate manually to the service/passport/calendar pages.")
    print("The bot will snapshot every page change. Press Enter here when done.\n")
    Path("scout").mkdir(exist_ok=True)

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
    ap = argparse.ArgumentParser(description="T0 — prenot@mi single-session midnight racer")
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

    t0_js = load_t0_js()
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
            args=CHROME_FAST_ARGS,
            bypass_csp=True,
        )
        if not args.scout:
            ctx.add_init_script("window.__T0_MODE='race';")
        ctx.add_init_script(t0_js)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(int(cfg["browser"]["nav_timeout_s"] * 1000))
        page.set_default_navigation_timeout(int(cfg["browser"]["nav_timeout_s"] * 1000))
        bind_t0_console(page)
        if not args.scout:
            install_network_filters(ctx, page, cfg)
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
