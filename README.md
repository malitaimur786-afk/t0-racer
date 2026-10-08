# T0 — prenot@mi midnight racer

A single-session, human-in-the-loop booking instrument for the LEGALISATIONS
(D.O.V.) slot drop on [prenotami.esteri.it](https://prenotami.esteri.it).

It does the timing-critical part: already logged in, passport filled, standing
on AVANTI. At the server's midnight it fires, then an **in-page racer**
(MutationObserver + rAF, injected at document-start) books the first green
date and first green slot in the same turn the cell appears.

> **Scope note:** CAPTCHA solving / bypass is explicitly out of scope — you
> solve the CAPTCHA yourself. No fingerprint spoofing, no queue skipping.

## The bot

The racer lives in [`bot/`](bot/) — self-contained: `bot.py` (Playwright
driver), `t0.js` (in-page racer), `selftest.py`, `config.example.json`,
`requirements.txt`. See [`bot/README.md`](bot/README.md) for the full
write-up. An earlier iteration is preserved in [`bot-legacy/`](bot-legacy/).

```bash
cd bot
pip install -r requirements.txt
playwright install chromium
cp config.example.json config.json   # then edit it with your credentials
python bot.py
```

## The web app

This repo also contains the Mission Control web app (TanStack Start + React +
Tailwind) — countdown to midnight, race lab, engine panel, and config desk
for driving the racer. The config desk serves the bot files for download
from `public/racer/`.

```bash
npm install
npm run dev        # serves on http://localhost:8080
npm run build      # production build
npm run typecheck  # tsc --noEmit
npm test           # unit tests
```

Deploy: `vercel.json` is included — connect the repo to Vercel and deploy.

## Environment

The bot takes its credentials from `bot/config.json` (copied from
`config.example.json`) — never commit the filled-in file. The web app reads
configuration from environment variables (see `src/lib/env.server.ts`);
never commit a real `.env`.

## License

MIT — see [LICENSE](LICENSE).
