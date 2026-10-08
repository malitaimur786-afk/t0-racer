#!/usr/bin/env python3
"""Self-test for the prenot@mi racer.

1. Unit-tests the drop-scheduling math (Europe/Rome midnight == 23:00 Tunis
   while Italy is on summer time, and stays correct across CET/CEST).
2. Runs bot.py end-to-end in a real headless Chromium against a LOCAL mock of
   the two-step flow (passport page -> calendar -> receipt), covering the
   captcha handoff wait, honeypot clear, checkbox, service pick, green-day /
   green-slot race and receipt confirmation.

No network access to the real site is needed.  Run:  python3 selftest.py
"""

import functools
import http.server
import json
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import bot  # noqa: E402

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    if cond:
        print(f"  PASS  {name}")
    else:
        failures += 1
        print(f"  FAIL  {name}")
        if detail:
            tail = detail.strip()[-1500:]
            print("        " + tail.replace("\n", "\n        "))


# --------------------------------------------------------------------------
# fixtures: a miniature of the real two-step flow
# --------------------------------------------------------------------------

INDEX_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<title>Prenot@mi mock - selezione servizio</title>
<style>
  body { font-family: sans-serif; max-width: 760px; margin: 24px auto; }
  select, input, textarea, button { font-size: 16px; padding: 6px; margin: 4px 0; display: block; }
  iframe { border: 1px solid #bbb; }
</style></head><body>
<h1>Area di prenotazione</h1>
<h3 id="svcTitle" onclick="showPassport()" style="cursor:pointer">LEGALISATIONS (D.O.V.) — seleziona il servizio</h3>
<select id="svc">
  <option value="dov">LEGALISATIONS (D.O.V.)</option>
  <option value="visti">Visti</option>
</select>
<input type="text" id="passport" placeholder="Numero passaporto" style="display:none">
<textarea id="textera">SPAM-LEFTOVER</textarea>
<label><input type="checkbox" id="cb"> Dichiaro di aver letto le condizioni</label>
<iframe src="/fake-captcha.html" title="reCAPTCHA challenge" width="300" height="80"></iframe>
<button id="next">AVANTI</button>
<p id="err" style="color:red"></p>
<script>
  function showPassport() {
    document.getElementById('passport').style.display = 'block';
  }
  document.getElementById('svc').addEventListener('change', showPassport);
  var pp = document.getElementById('passport');
  pp.addEventListener('input', function () {
    setTimeout(function () { document.getElementById('textera').value = ''; }, 300);
  });
  // simulated human solving the CAPTCHA after 3 seconds
  setTimeout(function () { document.body.setAttribute('data-captcha-ok', 'true'); }, 3000);
  document.getElementById('next').addEventListener('click', function () {
    var err = document.getElementById('err');
    if (!pp.value.trim()) { err.textContent = 'Passaporto mancante'; return; }
    if (document.getElementById('textera').value.trim()) { err.textContent = 'Honeypot pieno'; return; }
    if (!document.getElementById('cb').checked) { err.textContent = 'Checkbox non spuntata'; return; }
    if (document.body.getAttribute('data-captcha-ok') !== 'true') { err.textContent = 'Captcha'; return; }
    err.textContent = '';
    location.href = '/calendar.html';
  });
</script>
</body></html>
"""

CALENDAR_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<title>Calendario prenotazioni</title>
<style>
  body { font-family: sans-serif; max-width: 760px; margin: 24px auto; }
  td { width: 40px; height: 40px; text-align: center; color: #fff; cursor: pointer; }
  .slot { padding: 6px 10px; margin: 4px; display: inline-block; cursor: pointer; }
</style></head><body>
<h1>Calendario — Luglio 2026</h1>
<table><tbody><tr id="row"></tr></tbody></table>
<div id="slots"><em>Scegli una data aperta</em></div>
<button id="book" style="display:none">PRENOTA</button>
<script>
  var row = document.getElementById('row');
  for (var day = 1; day <= 31; day++) {
    var open = (day === 7 || day === 8);
    var td = document.createElement('td');
    td.id = 'd' + day;
    td.style.background = open ? '#28a745' : '#dc3545';
    td.textContent = String(day);
    td.addEventListener('click', function () { pickDay(this); });
    row.appendChild(td);
  }
  function pickDay(el) {
    document.getElementById('slots').innerHTML =
      '<span class="slot" style="background:#dc3545">10:01-10:30</span>' +
      '<span class="slot" style="background:#28a745">11:01-11:30</span>' +
      '<span class="slot" style="background:#28a745">14:01-14:30</span>';
    var kids = document.getElementById('slots').children;
    for (var i = 0; i < kids.length; i++) {
      kids[i].addEventListener('click', function () { pickSlot(this); });
    }
    el.style.outline = '3px solid #000';
  }
  function pickSlot(el) {
    document.getElementById('book').style.display = 'inline-block';
    document.getElementById('book').dataset.slot = el.textContent;
  }
  document.getElementById('book').addEventListener('click', function () {
    if (this.style.display === 'none') return;
    location.href = '/receipt.html?slot=' + encodeURIComponent(this.dataset.slot || '');
  });
</script>
</body></html>
"""

RECEIPT_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"><title>Ricevuta</title></head><body>
<h1>PRENOTAZIONE CONFERMATA</h1>
<p>Servizio: LEGALISATIONS (D.O.V.) — luglio 2026, 11:01-11:30</p>
<p>Codice prenotazione: MOCK-0007</p>
</body></html>
"""

FAKE_CAPTCHA_HTML = "<!doctype html><title>reCAPTCHA mock</title><p>reCAPTCHA</p>"


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_clock() -> None:
    print("\n[1] drop scheduling math")
    rome = ZoneInfo("Europe/Rome")
    tunis = timezone(timedelta(hours=1))  # Tunisia: UTC+1 year-round (no DST)
    cfg = {"drop": {"time": "00:00", "timezone": "Europe/Rome"}}

    summer = datetime(2026, 7, 14, 12, 0, tzinfo=rome)
    t = bot.next_drop(cfg, now=summer)
    check("summer: next drop = 2026-07-15 00:00 Rome (+02:00)",
          t == datetime(2026, 7, 15, 0, 0, tzinfo=rome), str(t))
    check("summer: that is 23:00 Tunisian time (the stated drop)",
          t.astimezone(tunis).strftime("%H:%M") == "23:00",
          t.astimezone(tunis).isoformat())

    winter = datetime(2026, 1, 10, 12, 0, tzinfo=rome)
    t2 = bot.next_drop(cfg, now=winter)
    check("winter: next drop = 2026-01-11 00:00 Rome (+01:00)",
          t2 == datetime(2026, 1, 11, 0, 0, tzinfo=rome), str(t2))
    check("winter: CET offset +01:00", t2.utcoffset() == timedelta(hours=1))

    after = datetime(2026, 7, 15, 0, 30, tzinfo=rome)
    t3 = bot.next_drop(cfg, now=after)
    check("after midnight -> rolls to the following night",
          t3 == datetime(2026, 7, 16, 0, 0, tzinfo=rome), str(t3))
    check("always 00:00 wall clock in the drop timezone",
          all(x.strftime("%H:%M") == "00:00" for x in (t, t2, t3)))

    # keepalive hook must fire while the bot is waiting out a long spin
    pings = []
    spin_target = datetime.now(rome) + timedelta(seconds=1.6)
    bot.spin_until(spin_target, keepalive=lambda: pings.append(1), keepalive_s=0.5)
    check("spin_until fires keepalive pings while waiting", len(pings) >= 2, str(pings))


def test_e2e(tmp: Path) -> None:
    print("\n[2] end-to-end run against local mock site")
    site = tmp / "site"
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (site / "calendar.html").write_text(CALENDAR_HTML, encoding="utf-8")
    (site / "receipt.html").write_text(RECEIPT_HTML, encoding="utf-8")
    (site / "fake-captcha.html").write_text(FAKE_CAPTCHA_HTML, encoding="utf-8")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(site))
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    shots = tmp / "shots"
    cfg = {
        "base_url": f"http://127.0.0.1:{port}/",
        "skip_login": True,
        "service_match": "legalis|d\\.o\\.v",
        "service_url": "",
        "selects": [{"match": "", "pick": "first"}],
        "passport_number": "TEST123X",
        "drop": {"time": "00:00", "timezone": "Europe/Rome"},
        "strategy": {"submit": "at_drop", "race_window_s": 30,
                     "poll_ms": 150, "reload_if_empty_s": 1.0},
        "captcha": {"wait_s": 30},
        "captcha_open_s": 0,
        "warmup_lead_s": 0,
        "browser": {"headless": True, "profile_dir": str(tmp / "profile"),
                    "nav_timeout_s": 20},
        "screenshots_dir": str(shots),
        "keep_open_s": 0,
        "on_success": "",
    }
    cfg_path = tmp / "config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    try:
        proc = subprocess.run(
            [sys.executable, str(BASE / "bot.py"), "--config", str(cfg_path),
             "--start-now", "--headless"],
            cwd=str(tmp), capture_output=True, text=True, timeout=150,
        )
    finally:
        httpd.shutdown()

    out = (proc.stdout or "") + (proc.stderr or "")
    (tmp / "e2e-output.log").write_text(out, encoding="utf-8")

    check("bot exited 0", proc.returncode == 0, out)
    check("captcha handoff waited for the solve",
          "waiting for you to solve the CAPTCHA" in out, out)
    check("passport page prepared", "passport number filled" in out, out)
    check("AVANTI pressed", "AVANTI pressed" in out, out)
    check("green date picked in race", "GREEN DATE picked" in out, out)
    check("green slot picked in race", "GREEN SLOT picked" in out, out)
    check("receipt confirmed", "BOOKING CONFIRMED" in out, out)

    # measured local race speed: AVANTI -> PRENOTA (mock site, single session)
    def to_s(m):
        h, mi, s, frac = m.groups()
        return int(h) * 3600 + int(mi) * 60 + int(s) + int(frac.ljust(3, "0")[:3]) / 1000.0

    m_av = re.search(r"AVANTI pressed at (\d{2}):(\d{2}):(\d{2})\.(\d+)", out)
    m_pr = re.search(r"PRENOTA pressed at (\d{2}):(\d{2}):(\d{2})\.(\d+)", out)
    if m_av and m_pr:
        a, p = to_s(m_av), to_s(m_pr)
        if p < a:
            p += 86400
        print(f"        measured local AVANTI -> PRENOTA: {p - a:.2f} s (mock site)")
        check("AVANTI -> PRENOTA under 5 s locally", (p - a) < 5.0, f"{p - a:.2f} s")
    else:
        check("AVANTI/PRENOTA timestamps present", False, out)

    receipts = list(shots.glob("*RECEIPT*.png")) if shots.exists() else []
    check("receipt screenshot saved", bool(receipts),
          str(sorted(p.name for p in (shots.glob("*") if shots.exists() else []))[:12]))


def main() -> int:
    global failures
    print("prenot@mi racer self-test")
    test_clock()
    with tempfile.TemporaryDirectory(prefix="racer-selftest-") as td:
        test_e2e(Path(td))
    print(f"\n{'ALL TESTS PASSED' if failures == 0 else str(failures) + ' TEST(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
