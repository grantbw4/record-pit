#!/usr/bin/env python3
"""Find front covers for public catalog entries in the generated snapshot."""

import argparse
import concurrent.futures
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_COVERS = Path(__file__).with_name("catalog-covers.json")
ARTIST_COVERS = Path(__file__).with_name("artist-covers.json")
USER_AGENT = "RecordPit/1.0 (https://grantbradywilliams.com/projects)"
MB_API = "https://musicbrainz.org/ws/2"
CAA_API = "https://coverartarchive.org"
CATALOG_SUFFIX = re.compile(r"\s*\[(?:explicit|clean)\]\s*$", re.IGNORECASE)
MB_LOCK = threading.Lock()
next_search = 0.0


def remove_catalog_suffix(text):
    return CATALOG_SUFFIX.sub("", text).strip()


def normalize(text):
    text = unicodedata.normalize("NFKD", remove_catalog_suffix(text)).casefold()
    return "".join(char for char in text if char.isalnum())


def pair_key(artist, title):
    return f"{normalize(artist)}:{normalize(title)}"


def query_catalog(url):
    global next_search
    for attempt in range(3):
        if url.startswith(MB_API):
            with MB_LOCK:
                time.sleep(max(0.0, next_search - time.monotonic()))
                next_search = time.monotonic() + 1.1
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if attempt == 2 or error.code not in {429, 502, 503, 504}:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == 2:
                raise
        time.sleep(2 ** (attempt + 1))


def escape_phrase(value):
    return re.sub(r'(["\\])', r'\\\1', remove_catalog_suffix(value))


def match_release_groups(artist, title):
    query = f'releasegroup:"{escape_phrase(title)}" AND artist:"{escape_phrase(artist)}"'
    params = urllib.parse.urlencode({"query": query, "fmt": "json", "limit": 10})
    body = query_catalog(f"{MB_API}/release-group?{params}") or {}
    for result in body.get("release-groups", []):
        if normalize(result["title"]) != normalize(title):
            continue
        credit = result.get("artist-credit", [])
        display_credit = "".join(x.get("name", "") + x.get("joinphrase", "") for x in credit)
        canonical_credit = "".join(
            x.get("artist", {}).get("name", "") + x.get("joinphrase", "") for x in credit
        )
        if normalize(artist) in {normalize(display_credit), normalize(canonical_credit)}:
            yield result


def front_cover(path):
    body = query_catalog(f"{CAA_API}/{path}")
    if not body:
        return None
    for entry in body.get("images", []):
        if not entry.get("front") or not entry.get("approved"):
            continue
        thumbs = entry.get("thumbnails", {})
        url = thumbs.get("250") or thumbs.get("small")
        if url and url.startswith("https://coverartarchive.org/"):
            return {
                "url": url,
                "source": body.get("release") or f"https://musicbrainz.org/{path}",
                "sourceName": "Cover Art Archive",
            }
    return None


def find_cover(pair):
    artist, title = pair
    matches = list(match_release_groups(artist, title))
    for match in matches[:2]:
        card = front_cover(f"release-group/{match['id']}")
        if card:
            return card
        for release in match.get("releases", [])[:2]:
            card = front_cover(f"release/{release['id']}")
            if card:
                return card
    return None


def load_snapshot(path):
    source = path.read_text(encoding="utf-8")
    if "export const snapshot =" in source:
        source = source.split("export const snapshot =", 1)[1].strip().removesuffix(";")
    return json.loads(source)


def collect_pairs(snapshot):
    views = snapshot["views"]
    requests = {}
    cards = {}
    albums_by_artist = {}
    for album in views["album"]:
        if not album.get("artist"):
            continue
        pair = (album["artist"], album["title"])
        requests.setdefault(pair_key(*pair), pair)
        cards[album["id"]] = [pair_key(*pair)]
        albums_by_artist.setdefault(normalize(album["artist"]), []).append(pair)

    for song in views["song"]:
        if not song.get("album") or not (song.get("albumArtist") or song.get("artist")):
            continue
        pair = (song.get("albumArtist") or song["artist"], song["album"])
        requests.setdefault(pair_key(*pair), pair)
        cards[song["id"]] = [pair_key(*pair)]

    for artist in views["artist"]:
        pairs = list(albums_by_artist.get(normalize(artist["title"]), []))
        if artist.get("album"):
            pairs.insert(0, (artist.get("albumArtist") or artist["title"], artist["album"]))
        for pair in pairs:
            requests.setdefault(pair_key(*pair), pair)
        cards[artist["id"]] = [pair_key(*pair) for pair in pairs]
        if artist.get("catalogId"):
            cards[artist["id"]].append(f"artist:{artist['catalogId']}")
    return requests, cards


def atomic_write(path, content):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_artwork(path, cards, cache):
    artwork = {}
    for card_id, keys in cards.items():
        cover = next((cache[key] for key in keys if cache.get(key)), None)
        if cover:
            artwork[card_id] = cover
    atomic_write(path, "export const artwork = " + json.dumps(artwork, indent=2) + ";\n")
    return len(artwork)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data.js")
    parser.add_argument("--out", type=Path, default=ROOT / "art.js")
    parser.add_argument("--cache", type=Path, help="Optional local lookup cache. Keep it outside the repository.")
    parser.add_argument("--limit", type=int, help="Optional limit on new catalog lookups.")
    parser.add_argument("--catalog", action="append", type=Path, default=[],
                        help="Use cover links from locally verified Amazon Music tracklists")
    args = parser.parse_args()

    requests, cards = collect_pairs(load_snapshot(args.snapshot))
    cache = json.loads(args.cache.read_text(encoding="utf-8")) if args.cache and args.cache.exists() else {}
    for entry in json.loads(CATALOG_COVERS.read_text(encoding="utf-8")):
        cache[pair_key(entry["artist"], entry["title"])] = {
            "url": entry["image"],
            "source": entry.get("source") or f"https://music.amazon.com/albums/{entry['catalogId']}",
            "sourceName": entry.get("sourceName", "Amazon Music"),
        }
    for catalog in args.catalog:
        for entry in json.loads(catalog.read_text(encoding="utf-8")):
            if entry.get("cover"):
                cache.setdefault(pair_key(entry["artist"], entry["name"]), {
                    "url": entry["cover"], "source": entry["source"], "sourceName": "Amazon Music",
                })
    if ARTIST_COVERS.exists():
        for entry in json.loads(ARTIST_COVERS.read_text(encoding="utf-8")):
            cache[f"artist:{entry['catalogId']}"] = {
                "url": entry["image"],
                "source": entry["source"],
                "sourceName": entry.get("sourceName", "Amazon Music"),
            }
    pending = [(key, pair) for key, pair in requests.items() if key not in cache]
    pending = pending[:args.limit]
    print(f"Resolving {len(pending)} album covers for {len(cards)} cards", flush=True)
    write_artwork(args.out, cards, cache)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        future_keys = {pool.submit(find_cover, pair): key for key, pair in pending}
        for index, future in enumerate(concurrent.futures.as_completed(future_keys), 1):
            key = future_keys[future]
            try:
                cache[key] = future.result()
            except (ValueError, urllib.error.URLError, TimeoutError) as error:
                print(f"Catalog request failed for {requests[key]}: {error}", flush=True)
            if args.cache:
                atomic_write(args.cache, json.dumps(cache))
            if index % 10 == 0 or index == len(pending):
                count = write_artwork(args.out, cards, cache)
                print(f"{index}/{len(pending)} complete. {count} cards have covers.", flush=True)


if __name__ == "__main__":
    main()
