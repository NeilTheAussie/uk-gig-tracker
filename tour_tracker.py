#!/usr/bin/env python3
"""
UK Gig Tracker
--------------
Tracks UK tour dates + on-sale dates for a list of artists you care about,
using the (free) Ticketmaster Discovery API, and writes a self-contained
dashboard.html you can open in any browser.

The point: O2 Priority presales run ~48h before general on-sale. O2 Priority
has no public API, but if we know the on-sale date we can work backwards and
tell you roughly when your Priority window opens — so you stop missing them.

Usage:
    python tour_tracker.py

Setup:
    1. Get a free API key at https://developer.ticketmaster.com (takes 2 mins).
    2. Copy config.example.json -> config.json and paste your key in.
    3. Edit artists.txt (one artist per line). A starter list is pre-loaded.
    4. Run it. Open dashboard.html.
"""

import json
import os
import re
import sys
import time
import html
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

try:
    from zoneinfo import ZoneInfo
    UK_TZ = ZoneInfo("Europe/London")
except Exception:  # pragma: no cover
    UK_TZ = timezone.utc

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
ARTISTS_PATH = os.path.join(HERE, "artists.txt")
CACHE_PATH = os.path.join(HERE, "cache.json")
DASHBOARD_PATH = os.path.join(HERE, "dashboard.html")

API_BASE = "https://app.ticketmaster.com/discovery/v2"
PRIORITY_LEAD_HOURS = 48  # O2 Priority presale typically opens ~48h before general sale


# --------------------------------------------------------------------------- #
# Config / inputs
# --------------------------------------------------------------------------- #
def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(
            "No config.json found.\n"
            "  -> Copy config.example.json to config.json and add your Ticketmaster API key.\n"
            "  -> Get a free key at https://developer.ticketmaster.com"
        )
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    key = cfg.get("ticketmaster_api_key", "").strip()
    if not key or key.startswith("PASTE_"):
        sys.exit("Add your Ticketmaster API key to config.json first.")
    return cfg


def load_artists():
    if not os.path.exists(ARTISTS_PATH):
        sys.exit("No artists.txt found.")
    artists = []
    with open(ARTISTS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                artists.append(line)
    if not artists:
        sys.exit("artists.txt is empty. Add at least one artist (one per line).")
    return artists


def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


# --------------------------------------------------------------------------- #
# Ticketmaster API
# --------------------------------------------------------------------------- #
def tm_get(path, key, **params):
    params["apikey"] = key
    url = f"{API_BASE}/{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=20)
        except requests.RequestException as e:
            print(f"    network error: {e}; retrying...")
            time.sleep(2)
            continue
        if r.status_code == 429:  # rate limited
            print("    rate limited; pausing 3s...")
            time.sleep(3)
            continue
        if r.status_code != 200:
            print(f"    API returned {r.status_code} for {path}")
            return None
        return r.json()
    return None


def resolve_attraction_id(name, key):
    """Find the best-matching music attraction id for an artist name."""
    data = tm_get("attractions.json", key, keyword=name,
                  classificationName="music", size=5)
    if not data:
        return None, name
    items = (data.get("_embedded") or {}).get("attractions") or []
    if not items:
        return None, name
    # Prefer an exact (case-insensitive) name match, else first result.
    for it in items:
        if it.get("name", "").lower() == name.lower():
            return it.get("id"), it.get("name")
    return items[0].get("id"), items[0].get("name")


def fetch_uk_events(attraction_id, key):
    data = tm_get("events.json", key, attractionId=attraction_id,
                  countryCode="GB", classificationName="music",
                  sort="date,asc", size=50)
    if not data:
        return []
    return (data.get("_embedded") or {}).get("events") or []


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def to_uk(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(UK_TZ)


def fmt_dt(dt, with_time=True):
    if dt is None:
        return "TBC"
    if with_time:
        return dt.strftime("%a %d %b %Y, %H:%M")
    return dt.strftime("%a %d %b %Y")


def find_priority_presale(presales):
    """Return (name, start_dt, end_dt) for an O2/Priority presale if present."""
    for p in presales or []:
        nm = (p.get("name") or "").lower()
        if "o2" in nm or "priority" in nm:
            return (p.get("name"), parse_iso(p.get("startDateTime")),
                    parse_iso(p.get("endDateTime")))
    return None


# O2-branded venues (The O2, O2 Academy chain, O2 Apollo, O2 City Hall, etc.)
# almost always run an O2 Priority presale. "\bo2\b" matches "O2" as its own
# token, so it won't trip on words like "CO2".
O2_VENUE_RE = re.compile(r"\bo2\b", re.IGNORECASE)


def is_o2_venue(venue_name):
    return bool(venue_name and O2_VENUE_RE.search(venue_name))


def build_event_record(artist_display, ev):
    sales = ev.get("sales") or {}
    public = sales.get("public") or {}
    presales = sales.get("presales") or []

    public_start = parse_iso(public.get("startDateTime"))
    venues = (ev.get("_embedded") or {}).get("venues") or []
    venue = venues[0] if venues else {}
    status = ((ev.get("dates") or {}).get("status") or {}).get("code", "")

    priority = find_priority_presale(presales)
    o2_venue = is_o2_venue(venue.get("name"))
    if priority:
        prio_kind = "exact"
        prio_name, prio_start, prio_end = priority
    elif public_start:
        prio_start = public_start - timedelta(hours=PRIORITY_LEAD_HOURS)
        prio_end = public_start
        if o2_venue:
            prio_kind = "likely"
            prio_name = "O2 Priority (O2 venue — very likely)"
        else:
            prio_kind = "estimate"
            prio_name = "O2 Priority (estimated)"
    else:
        prio_kind = "unknown"
        prio_name = prio_start = prio_end = None

    return {
        "id": ev.get("id"),
        "artist": artist_display,
        "name": ev.get("name"),
        "url": ev.get("url"),
        "status": status,
        "event_local_date": ((ev.get("dates") or {}).get("start") or {}).get("localDate"),
        "event_dt": (lambda d: d.isoformat() if d else None)(
            parse_iso(((ev.get("dates") or {}).get("start") or {}).get("dateTime"))),
        "venue": venue.get("name"),
        "city": (venue.get("city") or {}).get("name"),
        "o2_venue": o2_venue,
        "public_start": public_start.isoformat() if public_start else None,
        "priority_kind": prio_kind,
        "priority_name": prio_name,
        "priority_start": prio_start.isoformat() if prio_start else None,
        "priority_end": prio_end.isoformat() if prio_end else None,
    }


# --------------------------------------------------------------------------- #
# Dashboard rendering (self-contained HTML, matches house style)
# --------------------------------------------------------------------------- #
def esc(x):
    return html.escape(str(x)) if x is not None else ""


def render_dashboard(events, generated_at, n_artists, n_new):
    now = datetime.now(UK_TZ)

    def pdt(iso):
        return to_uk(parse_iso(iso)) if iso else None

    # Buckets
    priority_upcoming = []   # priority window in the future -> the ones to catch
    on_sale_now = []
    other = []

    for e in events:
        prio_start = pdt(e["priority_start"])
        pub_start = pdt(e["public_start"])
        if prio_start and prio_start > now:
            priority_upcoming.append(e)
        elif pub_start and pub_start <= now and (e["status"] == "onsale" or e["status"] == ""):
            on_sale_now.append(e)
        else:
            other.append(e)

    priority_upcoming.sort(key=lambda e: pdt(e["priority_start"]) or now)
    on_sale_now.sort(key=lambda e: pdt(e["event_dt"]) or now)
    other.sort(key=lambda e: pdt(e["event_dt"]) or now)

    n_shows = len(events)
    n_priority = len(priority_upcoming)

    # ---- Priority hero cards ----
    hero_cards = ""
    if priority_upcoming:
        for e in priority_upcoming:
            prio_start = pdt(e["priority_start"])
            prio_end = pdt(e["priority_end"])
            pub_start = pdt(e["public_start"])
            kind = e["priority_kind"]
            BADGES = {
                "exact":    ("CONFIRMED O2 PRESALE",      "badge-green",  "card-green"),
                "likely":   ("O2 VENUE · PRIORITY LIKELY", "badge-indigo", "card-indigo"),
                "estimate": ("ESTIMATED PRIORITY WINDOW",  "badge-amber",  "card-amber"),
            }
            badge, badge_class, card_class = BADGES.get(kind, BADGES["estimate"])
            days_away = (prio_start - now).days if prio_start else None
            countdown = ""
            if days_away is not None:
                countdown = ("opens today" if days_away <= 0
                             else "opens tomorrow" if days_away == 1
                             else f"in {days_away} days")
            new_tag = '<span class="new-tag">NEW</span>' if e.get("_is_new") else ""
            o2_chip = '<span class="chip-o2">O2 venue</span>' if e.get("o2_venue") else ""
            if kind == "exact":
                window_line = f'O2 Priority presale: <strong>{esc(fmt_dt(prio_start))}</strong>'
            elif kind == "likely":
                window_line = (f'Priority opens ~<strong>{esc(fmt_dt(prio_start))}</strong> '
                               f'<span class="muted">(O2 venue — presale very likely; 48h before general sale)</span>')
            else:
                window_line = (f'Priority opens ~<strong>{esc(fmt_dt(prio_start))}</strong> '
                               f'<span class="muted">(estimate, 48h before general sale)</span>')
            general_line = (
                f'General sale: {esc(fmt_dt(pub_start))}' if pub_start else ""
            )
            hero_cards += f"""
            <div class="hero-card {card_class}">
                <div class="hero-top">
                    <span class="{badge_class}">{badge}</span>
                    <span class="countdown">{esc(countdown)}</span>
                    {new_tag}
                </div>
                <div class="hero-artist">{esc(e['artist'])}</div>
                <div class="hero-event">{esc(e['name'])}</div>
                <div class="hero-meta">
                    {esc(e['venue'] or 'Venue TBC')}{', ' + esc(e['city']) if e['city'] else ''} {o2_chip}
                    &nbsp;·&nbsp; {esc(fmt_dt(pdt(e['event_dt']), with_time=False) if e['event_dt'] else e['event_local_date'])}
                </div>
                <div class="hero-window">{window_line}</div>
                <div class="hero-general">{general_line}</div>
                <a class="hero-link" href="{esc(e['url'])}" target="_blank">View on Ticketmaster &rarr;</a>
            </div>"""
    else:
        hero_cards = ('<div class="callout">No upcoming Priority windows detected right now. '
                      'Either nothing tracked has gone on sale soon, or the dates are already on sale '
                      '(see below). Re-run after new tour announcements.</div>')

    # ---- On sale now table ----
    def rows(bucket, show_priority=True):
        out = ""
        for e in bucket:
            new_tag = '<span class="new-tag">NEW</span>' if e.get("_is_new") else ""
            ev_date = fmt_dt(pdt(e["event_dt"]), with_time=False) if e["event_dt"] else (e["event_local_date"] or "TBC")
            prio = ""
            if show_priority:
                ps = pdt(e["priority_start"])
                if ps:
                    k = e["priority_kind"]
                    tag = "" if k == "exact" else " (likely)" if k == "likely" else " (est)"
                    chip = ' <span class="chip-o2">O2</span>' if e.get("o2_venue") else ""
                    prio = f"{esc(fmt_dt(ps))}{tag}{chip}"
                else:
                    prio = "—"
            link = f'<a href="{esc(e["url"])}" target="_blank">tickets</a>' if e["url"] else ""
            out += f"""
            <tr>
                <td><strong>{esc(e['artist'])}</strong> {new_tag}</td>
                <td>{esc(e['venue'] or 'TBC')}<br><span class="muted">{esc(e['city'] or '')}</span></td>
                <td>{esc(ev_date)}</td>
                {f'<td>{prio}</td>' if show_priority else ''}
                <td>{link}</td>
            </tr>"""
        return out

    on_sale_table = ""
    if on_sale_now:
        on_sale_table = f"""
        <table>
            <thead><tr><th>Artist</th><th>Venue</th><th>Show date</th><th>Buy</th></tr></thead>
            <tbody>{rows(on_sale_now, show_priority=False)}</tbody>
        </table>"""
    else:
        on_sale_table = '<p class="muted">Nothing currently on general sale.</p>'

    all_table = ""
    if events:
        all_sorted = sorted(events, key=lambda e: pdt(e["event_dt"]) or now)
        all_table = f"""
        <table>
            <thead><tr><th>Artist</th><th>Venue</th><th>Show date</th><th>Priority (est/confirmed)</th><th>Buy</th></tr></thead>
            <tbody>{rows(all_sorted, show_priority=True)}</tbody>
        </table>"""
    else:
        all_table = '<p class="muted">No UK shows found for your tracked artists yet.</p>'

    new_kpi_class = "kpi-up" if n_new else "kpi-neutral"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>UK Gig Tracker</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    color:#1a1a1a; background:#fafafa; line-height:1.6; padding:40px;
    max-width:1100px; margin:0 auto; }}
.report-header {{ border-bottom:3px solid #1a1a1a; padding-bottom:20px; margin-bottom:30px; }}
.report-header h1 {{ font-size:28px; font-weight:700; margin-bottom:5px; }}
.report-meta {{ font-size:14px; color:#666; }}
.section {{ margin-bottom:35px; }}
.section h2 {{ font-size:20px; font-weight:600; margin-bottom:15px; padding-bottom:8px; border-bottom:1px solid #e0e0e0; }}
.kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:15px; margin:20px 0; }}
.kpi-card {{ background:#fff; border:1px solid #e0e0e0; border-radius:8px; padding:20px; text-align:center; }}
.kpi-value {{ font-size:32px; font-weight:700; }}
.kpi-label {{ font-size:13px; color:#666; margin-top:5px; }}
.kpi-up {{ color:#16a34a; }} .kpi-neutral {{ color:#666; }}
.callout {{ background:#fffbeb; border-left:4px solid #f59e0b; padding:15px 20px; margin:15px 0; border-radius:0 6px 6px 0; font-size:14px; }}
.hero-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; }}
.hero-card {{ background:#fff; border:1px solid #e0e0e0; border-left:4px solid #f59e0b; border-radius:8px; padding:18px 20px; }}
.hero-top {{ display:flex; align-items:center; gap:8px; margin-bottom:10px; flex-wrap:wrap; }}
.badge-amber {{ background:#fef3c7; color:#92400e; font-size:11px; font-weight:700; letter-spacing:.04em; padding:3px 8px; border-radius:4px; }}
.badge-green {{ background:#dcfce7; color:#166534; font-size:11px; font-weight:700; letter-spacing:.04em; padding:3px 8px; border-radius:4px; }}
.badge-indigo {{ background:#e0e7ff; color:#3730a3; font-size:11px; font-weight:700; letter-spacing:.04em; padding:3px 8px; border-radius:4px; }}
.chip-o2 {{ background:#eef2ff; color:#4338ca; font-size:11px; font-weight:600; padding:1px 6px; border-radius:3px; border:1px solid #c7d2fe; }}
.card-green {{ border-left-color:#16a34a; }}
.card-indigo {{ border-left-color:#4f46e5; }}
.card-amber {{ border-left-color:#f59e0b; }}
.countdown {{ font-size:12px; color:#dc2626; font-weight:600; }}
.hero-artist {{ font-size:18px; font-weight:700; margin-top:2px; }}
.hero-event {{ font-size:14px; color:#444; }}
.hero-meta {{ font-size:13px; color:#666; margin:6px 0; }}
.hero-window {{ font-size:14px; margin-top:8px; }}
.hero-general {{ font-size:13px; color:#666; }}
.hero-link {{ display:inline-block; margin-top:10px; font-size:13px; color:#2563eb; text-decoration:none; }}
.hero-link:hover {{ text-decoration:underline; }}
table {{ width:100%; border-collapse:collapse; margin:15px 0; font-size:14px; background:#fff; }}
th {{ background:#f5f5f5; font-weight:600; text-align:left; padding:10px 12px; border-bottom:2px solid #ddd; }}
td {{ padding:10px 12px; border-bottom:1px solid #eee; vertical-align:top; }}
tr:hover {{ background:#fafafa; }}
td a {{ color:#2563eb; text-decoration:none; }} td a:hover {{ text-decoration:underline; }}
.muted {{ color:#999; font-size:13px; }}
.new-tag {{ background:#dc2626; color:#fff; font-size:10px; font-weight:700; padding:2px 6px; border-radius:3px; vertical-align:middle; }}
.report-footer {{ margin-top:40px; padding-top:20px; border-top:1px solid #e0e0e0; font-size:12px; color:#999; }}
@media print {{ body {{ padding:20px; background:#fff; }} .hero-card,.kpi-card {{ break-inside:avoid; }} }}
</style>
</head>
<body>
<div class="report-header">
    <h1>UK Gig Tracker</h1>
    <div class="report-meta">
        Generated {esc(generated_at)} · {n_artists} artists tracked · O2 Priority windows estimated at 48h before general sale
    </div>
</div>

<div class="kpi-grid">
    <div class="kpi-card"><div class="kpi-value">{n_shows}</div><div class="kpi-label">Upcoming UK shows</div></div>
    <div class="kpi-card"><div class="kpi-value kpi-neutral">{n_priority}</div><div class="kpi-label">Priority windows ahead</div></div>
    <div class="kpi-card"><div class="kpi-value">{len(on_sale_now)}</div><div class="kpi-label">On sale now</div></div>
    <div class="kpi-card"><div class="kpi-value {new_kpi_class}">{n_new}</div><div class="kpi-label">New since last run</div></div>
</div>

<div class="section">
    <h2>🔔 Priority windows coming up — don't miss these</h2>
    <div class="hero-grid">{hero_cards}</div>
</div>

<div class="section">
    <h2>On sale now</h2>
    {on_sale_table}
</div>

<div class="section">
    <h2>All tracked UK shows</h2>
    {all_table}
</div>

<div class="report-footer">
    Data from the Ticketmaster Discovery API. O2 Priority has no public feed. <strong>Confirmed</strong> = Ticketmaster
    lists an explicit O2 presale. <strong>O2 venue · likely</strong> = the show is at an O2-branded venue (The O2,
    O2 Academy, O2 Apollo, etc.), where a Priority presale almost always runs. <strong>Estimate</strong> = the usual
    48h-before-general-sale pattern at a non-O2 venue. Windows are a guide — always confirm in the O2 Priority app.
    Re-run <code>tour_tracker.py</code> after new tour announcements to refresh.
</div>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    cfg = load_config()
    key = cfg["ticketmaster_api_key"].strip()
    artists = load_artists()
    prev_cache = load_cache()
    prev_ids = set(prev_cache.get("event_ids", []))

    print(f"Tracking {len(artists)} artists...\n")
    all_events = []
    for name in artists:
        print(f"  {name}")
        attraction_id, matched = resolve_attraction_id(name, key)
        if not attraction_id:
            print("    no music match found, skipping")
            continue
        if matched.lower() != name.lower():
            print(f"    matched to '{matched}'")
        evs = fetch_uk_events(attraction_id, key)
        print(f"    {len(evs)} UK show(s)")
        for ev in evs:
            all_events.append(build_event_record(name, ev))
        time.sleep(0.25)  # be gentle on rate limits

    # Dedupe by event id
    seen = {}
    for e in all_events:
        if e["id"] and e["id"] not in seen:
            seen[e["id"]] = e
    events = list(seen.values())

    # Flag new
    n_new = 0
    for e in events:
        if e["id"] not in prev_ids:
            e["_is_new"] = True
            n_new += 1

    generated_at = datetime.now(UK_TZ).strftime("%a %d %b %Y, %H:%M")
    html_out = render_dashboard(events, generated_at, len(artists), n_new)
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(html_out)

    # Save cache
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": generated_at,
            "event_ids": [e["id"] for e in events],
            "events": events,
        }, f, indent=2)

    print(f"\nDone. {len(events)} UK shows, {n_new} new since last run.")
    print(f"Open: {DASHBOARD_PATH}")


if __name__ == "__main__":
    main()
