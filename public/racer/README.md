# T0 — prenot@mi midnight racer

A **single-session, human-in-the-loop** booking instrument for the LEGALISATIONS
(D.O.V.) slot drop on [prenotami.esteri.it](https://prenotami.esteri.it).

It does the timing-critical part: already logged in, passport filled, standing
on AVANTI. At the server’s midnight it fires, then an **in-page racer**
(MutationObserver + rAF, injected at document-start) books the first green
date and first green slot in the same turn the cell appears — not on a
Python poll 50 ms later.

## What actually changed (T0 rebuild)

| Bottleneck | Before | T0 |
|---|---|---|
| Calendar race | Python `evaluate` every 50 ms over CDP | In-page observer clicks in the mutation turn |
| AVANTI clock | Python sleep + evaluate (~2–8 ms jitter) | `performance.now()` busy-wait, sub-millisecond |
| Gate bounce | **8 s** `wait_for_function` before retry | 80–120 ms burst re-press for 6 s |
| Clock | One HTTP `Date` (±1 s) | 7-sample NTP-style median, RTT midpoint |
| Empty calendar | Playwright `reload` every 1.2 s | In-page `location.reload` every 400 ms while hot |
| Service pick | Up to 400 CDP `inner_text` calls | One evaluate |
| Captcha detect | 120 ms poll | 20 ms `wait_for_function` + token probe |
| Chrome | Default background throttling | IPC-flood + renderer-backgrounding off |

## Scope (unchanged, non-negotiable)

| Included | Excluded |
|---|---|
| Rome midnight scheduling (23:00 Tunis in summer, correct across CET/CEST) | CAPTCHA solving / bypass — *you* solve it |
| One persistent Chromium profile | Fingerprint spoofing, stealth, session rotation |
| Login → service → passport → AVANTI → calendar race → PRENOTA → receipt | Parallel browsers / API hammering |
| Bounded empty-calendar reloads, expired-token re-press | Queue skipping |

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
cp config.example.json config.json      # then edit it
```

Fill in at minimum: `email` / `password` (or `PRENOTAMI_EMAIL` / `PRENOTAMI_PASSWORD`),
`passport_number`, and confirm `selects` / `service_match`.

## First run: scout

```bash
python3 bot.py --config config.json --scout
```

## Race night

```bash
python3 bot.py --config config.json
```

```
T-8 min   browser opens, session reused, passport page filled
T-2.5 min notification: solve the CAPTCHA
T-0       T0 busy-waits the last 8 ms, presses AVANTI on the server clock
T+0       calendar document-start already has the observer running
          first green td → first green slot → PRENOTA, same turn as insert
receipt   screenshot + HTML in ./shots/
```

If the gate bounces an early press, T0 re-presses AVANTI every ~100 ms for
`strategy.burst_s` (default 6 s) instead of sitting idle for 8 seconds.

## Verify without the real site

```bash
python3 selftest.py
```

## Files

- `bot.py` — scheduler, session, burst press, supervisor
- `t0.js` — in-page racer (injected at document-start)
- `config.example.json`
- `selftest.py`
- `requirements.txt`

Run it on a wired connection, browser visible, a few minutes before Rome
midnight. The profile in `./profile` keeps you logged in across nights.
