#!/usr/bin/env python3
"""Resolve track IDs from a private audit to public Amazon Music release tracklists."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from html import unescape
import json
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


ID = re.compile(r"[A-Z0-9]{10}")
BASE = "https://music.amazon.com"
LOCK = threading.Lock()
NEXT_REQUEST = 0.0
STOP = threading.Event()

class AmazonRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not newurl.startswith(BASE + "/embed/"):
            return None
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch(asin):
    global NEXT_REQUEST
    with LOCK:
        if STOP.is_set():
            return ""
        time.sleep(max(0, NEXT_REQUEST - time.monotonic()))
        NEXT_REQUEST = time.monotonic() + .24
    request = Request(f"{BASE}/embed/{asin}/", headers={
        "User-Agent": "RecordPit/1.0 (https://grantbradywilliams.com/projects)"
    })
    try:
        with build_opener(AmazonRedirects()).open(request, timeout=8) as response:
            if not response.geturl().startswith(BASE + "/embed/"):
                return ""
            return response.read(1_000_000).decode("utf-8")
    except HTTPError as error:
        if error.code in {403, 429}:
            STOP.set()
        return ""
    except (URLError, TimeoutError):
        return ""


def field(html, name):
    match = re.search(r'<input id="' + name + r'"[^>]*\bvalue="([^"]*)"', html)
    return unescape(match[1]).strip() if match else ""


def track_album(asin):
    html = fetch(asin)
    album = field(html, "ALBUM_ASIN")
    if field(html, "ASIN") == asin and field(html, "TYPE") == "track" and ID.fullmatch(album):
        return asin, album
    return asin, None


def album_tracklist(asin):
    html = fetch(asin)
    if field(html, "ASIN") != asin or field(html, "TYPE") != "album":
        return None
    title = re.search(r'<a id="contentTitle"[^>]*>(.*?)</a>', html, re.S)
    artist = re.search(r'<div class="headerMetaData grey truncate">\s*<a href="[^"]*/artists/([A-Z0-9]{10})[^"]*"[^>]*>(.*?)</a>', html, re.S)
    art = re.search(r'<img src="(https://m.media-amazon.com/[^"]+)"[^>]*alt="Cover Art"', html)
    tracks = re.findall(r'data-asin="([A-Z0-9]{10})" id="trackId_\d+"', html)
    if not (title and artist and tracks):
        return None
    return {
        "asin": asin, "name": unescape(title[1]).strip(), "artist": unescape(artist[2]).strip(),
        "artistAsin": artist[1], "source": f"{BASE}/embed/{asin}/", "trackAsins": sorted(set(tracks)),
        "cover": unescape(art[1]) if art else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=Path)
    parser.add_argument("output", type=Path, help="Write public release tracklists here")
    parser.add_argument("--limit", type=int, default=400, choices=range(1, 1001), metavar="1–1000")
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()
    if args.audit.resolve() == args.output.resolve():
        parser.error("Use separate paths for the private audit and the public output")
    ids = [
        row["catalogId"] for row in json.loads(args.audit.read_text())["unmatchedAlbumTracks"]
        if ID.fullmatch(row["catalogId"])
    ][args.start:args.start + args.limit]
    pairs = {}
    with ThreadPoolExecutor(max_workers=5) as workers:
        for i, (track, album) in enumerate(workers.map(track_album, ids), 1):
            if album:
                pairs.setdefault(album, set()).add(track)
            if i % 50 == 0 or i == len(ids):
                print(f"Tracks checked: {i}; distinct release candidates: {len(pairs)}", flush=True)
        releases = []
        for i, release in enumerate(workers.map(album_tracklist, sorted(pairs)), 1):
            if release and pairs[release["asin"]].intersection(release["trackAsins"]):
                releases.append(release)
            if i % 25 == 0 or i == len(pairs):
                args.output.write_text(json.dumps(releases, indent=2) + "\n")
                print(f"Releases checked: {i}; verified: {len(releases)}", flush=True)
        if not pairs:
            args.output.write_text("[]\n")
    if STOP.is_set():
        print("Amazon refused or rate limited requests; the remaining lookups were stopped.")


if __name__ == "__main__":
    main()
