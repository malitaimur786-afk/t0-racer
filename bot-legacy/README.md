# prenot@mi racer

A **single-session, human-in-the-loop booking assistant** for the LEGALISATIONS
(D.O.V.) slot drop on [prenotami.esteri.it](https://prenotami.esteri.it).

It does the boring, timing-critical part perfectly: it wakes up for the drop,
already logged in with your session warm, your passport page filled, standing
at the AVANTI button — then it fires at the exact second, races the calendar
for the first green date and first green time slot, presses PRENOTA and saves
the receipt. One real browser, one real session, bounded polling.

## Scope (deliberate, non-negotiable)

| Included | Excluded |
|---|---|
| Precise scheduling: next **00:00 Europe/Rome** (= 23:00 Tunisian while Italy is on summer time, correct across CET/CEST automatically) | **CAPTCHA solving/bypass** — the site's reCAPTCHA runs untouched; *you* solve it (one click/puzzle), the bot detects the solved token and presses AVANTI for you within ~0.5 s |
| Persistent logged-in Chromium profile (real cookies, real session, normal browser) | Fingerprint spoofing, stealth patches, fake sessions, session rotation |
| Full automation of the flow: login → service → passport page (dropdown, passport no., honeypot clear, checkbox) → AVANTI → calendar race → PRENOTA → receipt | Parallel sessions / multiple browsers / hammering internal API endpoints |
| Bounded retries: timeouts,5xx, session expiry (re-login), expired captcha token (re-solve + re-press loop), "no availability" refreshes with a floor interval | Fuzzing alternate URLs, queue skipping |
| Screenshots + HTML dump + timestamped log of every step | |

The site is a government booking service: these boundaries are what keeps this
a personal tool instead of the same arms race that's breaking it. The
assistance it gives you is speed and reliability after *you* are present and
verified — it will not pretend to be a human on its own.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
cp config.example.json config.json      # then edit it
```

Fill in at minimum:

- `email` / `password` (or `PRENOTAMI_EMAIL` / `PRENOTAMI_PASSWORD` env vars)
- `passport_number`
- `selects` — how to drive the dropdowns (default picks the **first option of
  the first list**, as you described; the second rule matches a consulate like
  `tunisi` if there is a separate consulate dropdown)
- `drop.time` / `drop.timezone` — defaults to `00:00` `Europe/Rome`

## First run: scout the site (important)

The calendar/service pages of prenot@mi are only reachable inside a live
session, so verify the selectors once before trusting the automation:

```bash
python3 bot.py --config config.json --scout
```

Log in if asked, click through to the service/passport page yourself, press
Enter in the terminal to snapshot each page. Element inventories land in
`./scout/` (id/name/class/background-color of every control) and screenshots in
`./shots/`. Use that to confirm `service_match` finds the LEGALISATIONS entry —
or grab the direct URL and set `service_url`.

## Race night

```bash
python3 bot.py --config config.json
```

Timeline (default config):

```
T-8 min   browser opens, logs in (session reused if still valid), warms up
          navigates to the service, fills dropdown + passport, checks the box
T-2.5 min desktop notification: "Solve the CAPTCHA in the browser window"
          you solve it; the bot holds the page ready
T-0       AVANTI pressed (within ms of 00:00 Rome / 23:00 Tunis)
T+0..120s calendar race: first GREEN day → first GREEN time → PRENOTA
          empty calendar → bounded refresh (default 1× / 2 s, single session)
receipt   screenshot + HTML saved to ./shots/, optional on_success command
```

Useful flags and settings:

- `--start-now` — skip the schedule wait (testing)
- `strategy.submit: "early"` + `early_lead_s` — try pressing AVANTI *before*
  the drop and polling the calendar (only if the site accepts it; the bot
  verifies and re-tries if the gate bounces it back)
- `strategy.race_window_s` / `poll_ms` — how long and how fast to poll
- `captcha.wait_s` — how long one captcha wait may block (default 15 min)
- `keep_open_s` — keep the browser open after success so you can eyeball the
  receipt
- `on_success` — shell command to run, `{receipt}` = screenshot path
  (e.g. an `osascript`/ntfy/Telegram notification)

reCAPTCHA tokens expire after ~2 minutes, so the bot opens the captcha window
`captcha_open_s` (default150 s) before the press, and if the gate rejects a
stale token it automatically re-waits, re-presses and re-enters the race until
the deadline.

## Speed design (what actually makes it fast)

- **Fire on the server's clock** — skew is measured from the site's own `Date`
  header (`clock.apply_server_skew`) and the AVANTI press is shifted so it
  lands at the *server's* 00:00, not your machine's idea of it.
- **Warm connection** — a fire-and-forget `HEAD` ping every `keepalive_s`
  (default 30 s) during the wait keeps TLS alive, so the midnight navigation
  reuses the connection instead of re-handshaking.
- **In-page fast clicks** — AVANTI and PRENOTA fire via synthetic `el.click()`
  inside the page (one evaluate, no actionability round-trips); the strict
  Playwright locator stays as fallback.
- **Adaptive race polling** — for `hot_window_s` (25 s) after the drop the race
  polls the local DOM every `hot_poll_ms` (50 ms, zero network cost), then
  backs off to `poll_ms`. Empty-calendar reloads run at `reload_hot_s` (1.2 s)
  while hot and `reload_if_empty_s` (2 s) after — still one single session.
- **No screenshots in the hot loop** — a capture costs 200–500 ms of page
  execution; race logging stays text-only.
- **Asset blocking after the captcha** (`block_assets`) — images/fonts/media
  are aborted for the submit+race phase (captcha challenge assets exempt),
  the classic 2–5× page-load win on a slow link.
- **One combined state probe** — `wait_advanced()` is a single
  `wait_for_function` at 60 ms granularity instead of a sleep-loop of
  separate locator counts (was ~400 ms detection latency after AVANTI).

## Verify without touching the real site

```bash
python3 selftest.py
```

Runs the drop-scheduling math tests (summer/winter Rome↔Tunis mapping) and a
full end-to-end run of `bot.py` in headless Chromium against a local mock of
the two-step flow: service pick → passport fill → honeypot clear → checkbox →
captcha wait → AVANTI → green-day/green-slot race → PRENOTA → receipt.

## Files

- [bot.py](bot.py) — the racer (scheduler, session, flow, race, retries)
- [config.example.json](config.example.json) — config template
- [selftest.py](selftest.py) — local end-to-end verification
- [requirements.txt](requirements.txt) — dependencies

## Run it where it counts

Run on your own machine, on a wired/stable connection, a few minutes before
the drop, with the browser window visible so you can solve the CAPTCHA
immediately when notified. The browser session persists in `./profile`, so
later nights skip the login entirely.
