#!/usr/bin/env python3
"""
Spotify -> artists.txt importer  (optional companion to tour_tracker.py)
------------------------------------------------------------------------
Pulls YOUR top artists + the artists you follow on Spotify and merges them
into artists.txt, so UK Gig Tracker watches your real taste instead of a
hand-curated list. Existing entries and comments in artists.txt are kept;
only genuinely new artists are appended.

It uses the Spotify Authorization Code flow: a browser tab opens once, you
click "Agree", and a refresh token is cached in .spotify_cache.json so future
runs need no clicking.

Setup (one time, ~3 minutes):
    1. Go to https://developer.spotify.com/dashboard and log in.
    2. "Create app". Name/description: anything. Redirect URI: EXACTLY
       http://127.0.0.1:8888/callback   (add it and Save).
    3. Open the app -> Settings. Copy the Client ID and Client Secret.
    4. Paste them into config.json under a "spotify" block, e.g.:
           "spotify": {
             "client_id": "....",
             "client_secret": "....",
             "redirect_uri": "http://127.0.0.1:8888/callback"
           }
       (See config.example.json for the full shape.)
    5. pip install requests   (already needed by tour_tracker.py)

Run:
    python spotify_import.py
    python tour_tracker.py        # now sees your imported artists
"""

import base64
import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
ARTISTS_PATH = os.path.join(HERE, "artists.txt")
TOKEN_CACHE = os.path.join(HERE, ".spotify_cache.json")

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = "user-top-read user-follow-read"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_spotify_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(
            "No config.json found.\n"
            "  -> Copy config.example.json to config.json and fill in the spotify block.\n"
            "  -> See the header of this file for the 3-minute setup."
        )
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    sp = cfg.get("spotify") or {}
    cid = (sp.get("client_id") or "").strip()
    secret = (sp.get("client_secret") or "").strip()
    redirect = (sp.get("redirect_uri") or "http://127.0.0.1:8888/callback").strip()
    if not cid or cid.startswith("PASTE_") or not secret or secret.startswith("PASTE_"):
        sys.exit(
            "Add your Spotify client_id and client_secret to config.json first.\n"
            "  -> See the header of spotify_import.py for setup steps."
        )
    return cid, secret, redirect


# --------------------------------------------------------------------------- #
# OAuth — Authorization Code flow with a one-shot local callback server
# --------------------------------------------------------------------------- #
class _CallbackHandler(BaseHTTPRequestHandler):
    code = None
    error = None

    def do_GET(self):  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != urllib.parse.urlparse(_CallbackHandler.redirect_path).path:
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.code = (qs.get("code") or [None])[0]
        _CallbackHandler.error = (qs.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = ("Authorisation failed: " + _CallbackHandler.error
               if _CallbackHandler.error
               else "Spotify connected. You can close this tab and return to the terminal.")
        self.wfile.write(
            f"<html><body style='font-family:sans-serif;padding:40px'>"
            f"<h2>{msg}</h2></body></html>".encode("utf-8")
        )

    def log_message(self, *args):  # silence the default stderr spam
        pass


def _basic_auth_header(client_id, client_secret):
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def authorise(client_id, client_secret, redirect_uri):
    """Return an access token, refreshing or running the browser flow as needed."""
    # 1. Try a cached refresh token first — no browser needed.
    if os.path.exists(TOKEN_CACHE):
        try:
            with open(TOKEN_CACHE) as f:
                cached = json.load(f)
            refresh = cached.get("refresh_token")
            if refresh:
                tok = _refresh_token(client_id, client_secret, refresh)
                if tok:
                    return tok
        except Exception:
            pass  # fall through to full flow

    # 2. Full browser approval.
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8888
    _CallbackHandler.redirect_path = redirect_uri

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "show_dialog": "false",
    }
    auth_link = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    server = HTTPServer((host, port), _CallbackHandler)
    print("Opening Spotify in your browser to approve access...")
    print(f"  If it doesn't open, paste this into your browser:\n  {auth_link}\n")
    webbrowser.open(auth_link)

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # Block until the callback handler captures a code/error.
    while _CallbackHandler.code is None and _CallbackHandler.error is None:
        server.handle_request()
    server.shutdown()

    if _CallbackHandler.error:
        sys.exit(f"Spotify authorisation was denied: {_CallbackHandler.error}")

    return _exchange_code(client_id, client_secret, _CallbackHandler.code, redirect_uri)


def _exchange_code(client_id, client_secret, code, redirect_uri):
    r = requests.post(
        TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code,
              "redirect_uri": redirect_uri},
        headers={"Authorization": _basic_auth_header(client_id, client_secret)},
        timeout=20,
    )
    if r.status_code != 200:
        sys.exit(f"Token exchange failed ({r.status_code}): {r.text}")
    payload = r.json()
    _save_refresh(payload.get("refresh_token"))
    return payload["access_token"]


def _refresh_token(client_id, client_secret, refresh):
    r = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        headers={"Authorization": _basic_auth_header(client_id, client_secret)},
        timeout=20,
    )
    if r.status_code != 200:
        return None
    payload = r.json()
    # Spotify may or may not return a new refresh token; keep the old if absent.
    _save_refresh(payload.get("refresh_token") or refresh)
    return payload["access_token"]


def _save_refresh(refresh):
    if not refresh:
        return
    try:
        with open(TOKEN_CACHE, "w") as f:
            json.dump({"refresh_token": refresh}, f)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Spotify data
# --------------------------------------------------------------------------- #
def _get(token, path, **params):
    r = requests.get(f"{API_BASE}/{path}",
                     headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=20)
    if r.status_code != 200:
        print(f"  Spotify API {r.status_code} for {path}: {r.text[:120]}")
        return None
    return r.json()


def fetch_top_artists(token):
    names = []
    for term in ("short_term", "medium_term", "long_term"):
        data = _get(token, "me/top/artists", limit=50, time_range=term)
        for a in ((data or {}).get("items") or []):
            if a.get("name"):
                names.append(a["name"])
    return names


def fetch_followed_artists(token):
    names = []
    after = None
    while True:
        params = {"type": "artist", "limit": 50}
        if after:
            params["after"] = after
        data = _get(token, "me/following", **params)
        block = (data or {}).get("artists") or {}
        for a in block.get("items") or []:
            if a.get("name"):
                names.append(a["name"])
        after = (block.get("cursors") or {}).get("after")
        if not after or not block.get("items"):
            break
    return names


# --------------------------------------------------------------------------- #
# Merge into artists.txt
# --------------------------------------------------------------------------- #
def read_existing():
    """Return (raw_lines, set_of_lowercased_existing_names)."""
    if not os.path.exists(ARTISTS_PATH):
        return [], set()
    with open(ARTISTS_PATH, encoding="utf-8") as f:
        lines = f.read().splitlines()
    existing = {ln.strip().lower() for ln in lines
                if ln.strip() and not ln.strip().startswith("#")}
    return lines, existing


def merge(new_names):
    lines, existing = read_existing()

    # Dedupe incoming list case-insensitively while preserving first-seen order.
    seen, ordered = set(), []
    for n in new_names:
        key = n.strip().lower()
        if key and key not in seen:
            seen.add(key)
            ordered.append(n.strip())

    to_add = [n for n in ordered if n.lower() not in existing]
    if not to_add:
        print("Nothing new to add — artists.txt already covers your Spotify artists.")
        return 0

    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.append(f"# --- imported from Spotify ({len(to_add)} new) ---")
    lines.extend(to_add)

    with open(ARTISTS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(to_add)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    client_id, client_secret, redirect_uri = load_spotify_config()
    token = authorise(client_id, client_secret, redirect_uri)

    print("Fetching your top artists...")
    top = fetch_top_artists(token)
    print(f"  {len(top)} top-artist entries (across short/medium/long term)")

    print("Fetching artists you follow...")
    followed = fetch_followed_artists(token)
    print(f"  {len(followed)} followed artists")

    added = merge(top + followed)
    print(f"\nDone. Added {added} new artist(s) to artists.txt.")
    if added:
        print("Next:  python tour_tracker.py")


if __name__ == "__main__":
    main()
