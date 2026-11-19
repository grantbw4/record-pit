#!/usr/bin/env python3
"""Resolve track IDs from a private audit to public Amazon Music release tracklists."""

import argparse
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from html import unescape
import json
import os
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


def resolve(ids, output, cached_releases=()):
    releases = {release["asin"]: release for release in cached_releases}
    confirmed = {track for release in releases.values() for track in release["trackAsins"]}
    pending = {}
    gate = threading.Lock()
    published = set(releases)

    def resolve_track(asin):
        with gate:
            if STOP.is_set():
                return "stopped"
            if asin in confirmed:
                return "cached"
        _, album = track_album(asin)
        if not album:
            return "unavailable"
        with gate:
            if album in releases:
                return "cached" if asin in confirmed else "unconfirmed"
            owner = album not in pending
            result = pending.setdefault(album, Future())
        if owner:
            try:
                result.set_result(album_tracklist(album))
            except Exception as error:
                result.set_exception(error)
                raise
        release = result.result()
        if not release or asin not in release["trackAsins"]:
            return "unconfirmed"
        with gate:
            releases[album] = release
            confirmed.update(release["trackAsins"])
        return "verified"

    def checkpoint():
        with gate:
            additions = [releases[asin] for asin in sorted(releases.keys() - published)]
        temporary = output.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as report:
            json.dump(additions, report, indent=2)
            report.write("\n")
        temporary.replace(output)
        return len(additions)

    counts = Counter()
    with ThreadPoolExecutor(max_workers=5) as workers:
        for index, status in enumerate(workers.map(resolve_track, ids), 1):
            counts[status] += 1
            if index % 50 == 0 or index == len(ids):
                print(f"{index}/{len(ids)} IDs handled; {checkpoint()} new public tracklists; "
                      f"{counts['cached']} skipped or reused; "
                      f"{counts['unavailable'] + counts['unconfirmed']} could not be verified", flush=True)
    checkpoint()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=Path)
    parser.add_argument("output", type=Path, help="Write public release tracklists here")
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--cached-catalog", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.start < 0 or args.limit < 1:
        parser.error("Use a non-negative start and a positive limit")
    if args.output.resolve() in {p.resolve() for p in [args.audit, *args.cached_catalog]}:
        parser.error("Use separate paths for the private inputs and catalog output")
    ids = [
        row["catalogId"] for row in json.loads(args.audit.read_text())["unmatchedAlbumTracks"]
        if ID.fullmatch(row["catalogId"])
    ][args.start:args.start + args.limit]
    cache = [release for path in args.cached_catalog for release in json.loads(path.read_text())]
    resolve(ids, args.output, cache)
    if STOP.is_set():
        print("Amazon refused or rate limited requests; the remaining lookups were stopped.")


if __name__ == "__main__":
    main()
