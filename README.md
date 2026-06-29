# UK Gig Tracker

A tiny local tool that tells you which of your favourite artists are touring the
UK, when tickets go on sale, and — crucially — roughly when your **O2 Priority
window** opens, so you stop missing presales.

It fetches data from the free Ticketmaster Discovery API and writes a polished
`dashboard.html` you open in any browser. No server, no account, runs entirely
on your machine.

## Why this shape

O2 Priority has **no public API** — nothing can read your actual Priority offers.
But O2 Priority presales run **~48 hours before general on-sale**. Ticketmaster's
API *does* give us the general on-sale date, so we work backwards and flag when
your Priority window most likely opens. Two extra signals sharpen the guess:

- **Confirmed** — Ticketmaster explicitly lists an O2/Priority presale → exact time shown.
- **O2 venue · likely** — the show is at an O2-branded venue (The O2, O2 Academy,
  O2 Apollo, O2 City Hall, etc.), where a Priority presale almost always runs → high confidence.
- **Estimate** — non-O2 venue, just the 48h rule → still shown, lower confidence
  (Priority often runs at non-O2 venues too, so this isn't a "no").

Treat estimated/likely windows as a strong hint, and confirm in the O2 Priority app.

## Setup (one time, ~3 minutes)

1. Get a free Ticketmaster API key: https://developer.ticketmaster.com
   (Register → a default app is created → copy the **Consumer Key**.)
2. `cp config.example.json config.json` and paste your key in.
3. Make sure you have `requests`:  `pip install requests`
4. (Optional) Edit `artists.txt` — a starter list is pre-loaded.

## Run it

```bash
python tour_tracker.py
```

Then open `dashboard.html`. Re-run it whenever you want fresh data (e.g. weekly,
or after you hear a tour's been announced). It remembers what it saw last time and
tags anything **NEW**.

## Auto-suggest from your real taste (optional)

Instead of hand-curating `artists.txt`, pull your top + followed artists straight
from Spotify:

```bash
python spotify_import.py     # one-time browser approval, then merges into artists.txt
python tour_tracker.py
```

Setup steps are in the header of `spotify_import.py`.

## Run it on a schedule (optional)

To have it refresh itself, e.g. every morning, add a cron job (Mac/Linux):

```
0 8 * * *  cd /path/to/uk-gig-tracker && /usr/bin/python3 tour_tracker.py
```

Then just open `dashboard.html` whenever — it'll already be current.

## Files

| File | What it is |
|------|-----------|
| `tour_tracker.py` | Main script — fetches data, writes the dashboard |
| `artists.txt` | Your tracked artists (edit this) |
| `spotify_import.py` | Optional: auto-fill artists.txt from Spotify |
| `config.json` | Your API key(s) — created by you, gitignored |
| `dashboard.html` | Generated output — open in browser |
| `cache.json` | Remembers last run so it can flag NEW shows |

## Limitations (so nothing surprises you)

- Covers events ticketed via **Ticketmaster** (the bulk of UK arena/major gigs).
  A few shows on other platforms (Dice, Skiddle, See, AXS) won't appear.
- Priority timing is an **estimate** unless Ticketmaster explicitly lists an
  O2/Priority presale in the data — in which case the dashboard shows the
  confirmed time.
- Free key allows 5,000 calls/day — far more than this needs.
