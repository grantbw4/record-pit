#!/usr/bin/env python3
"""Build the public Record Pit snapshot without extracting the Amazon archive."""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


MOJIBAKE = re.compile(r"[ÃÂâðï][\u0080-\u00bf€™‘’“”]|[\u0080-\u009f]")
EDITION_SUFFIX = re.compile(
    r"\s*(?:\((?:deluxe|expanded)(?:\s+(?:version|edition))?\)|"
    r"\[(?:deluxe|expanded)(?:\s+(?:version|edition))?\]|"
    r"\s+(?:[-–—]\s+)?(?:deluxe|expanded)(?:\s+(?:version|edition))?)$", re.I
)
EDITION_ALIASES = {
    ("the great divide: the last of the bugs", "noah kahan"): "The Great Divide",
    ("stick season (we'll all be here forever)", "noah kahan"): "Stick Season",
    ("older (and wiser)", "lizzy mcalpine"): "Older",
    ("sos deluxe: lana", "sza"): "SOS",
}


def archive_rows(archive, filename, required=False):
    matches = [name for name in archive.namelist() if name.rsplit("/", 1)[-1] == filename]
    if len(matches) > 1 or (required and not matches):
        raise ValueError(f"Expected one {filename} in the export")
    if matches:
        with io.TextIOWrapper(archive.open(matches[0]), encoding="utf-8-sig", newline="") as stream:
            yield from csv.DictReader(stream)


def value(text):
    text = (text or "").strip()
    # Some catalog names mix in text originally decoded with the wrong encoding.
    # Adopt a round trip only for suspicious text and only if the markers diminish.
    marks = len(MOJIBAKE.findall(text))
    if marks:
        for encoding in ("latin-1", "cp1252"):
            try:
                candidate = text.encode(encoding).decode("utf-8")
            except UnicodeError:
                continue
            if len(MOJIBAKE.findall(candidate)) < marks:
                text = candidate
                break
    return "" if text.casefold() in {"not available", "not applicable"} else text


def catalog_id(text):
    text = value(text)
    return text if re.fullmatch(r"[A-Z0-9]{10}", text) else ""


def title(text):
    return re.sub(r"\s+\[(?:Explicit|Clean)\]$", "", value(text), flags=re.IGNORECASE).strip()


def key(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def one(values):
    return next(iter(values)) if len(values) == 1 else None


def preferred(counter):
    return min(counter, key=lambda item: (-counter[item], item)) if counter else ""


def combine_album_editions(group):
    combined = {}
    for (name_key, artist_key), entry in group.items():
        display = preferred(entry["names"])
        base = EDITION_ALIASES.get((name_key, artist_key))
        if base is None:
            base = EDITION_SUFFIX.sub("", display).strip()
            if (key(base), artist_key) not in group:
                base = display
        identity = (key(base), artist_key)
        album = combined.setdefault(identity, {
            "seconds": 0, "events": 0, "names": Counter(), "credits": Counter(),
            "tracks": Counter(), "catalogIds": Counter(),
        })
        album["seconds"] += entry["seconds"]
        album["events"] += entry["events"]
        album["names"][base] += entry["seconds"]
        for field in ("credits", "tracks", "catalogIds"):
            album[field].update(entry[field])
    for identity, album in combined.items():
        standard = group.get(identity)
        if standard and standard["catalogIds"]:
            album["catalogIds"] = standard["catalogIds"]
    return combined


def public_id(mode, group):
    encoded = json.dumps(group, ensure_ascii=False).encode("utf-8")
    return f"{mode}-{hashlib.sha256(encoded).hexdigest()[:18]}"


def duration(row):
    milliseconds = max(0, int(row["Listen Duration in Milliseconds"]))
    track_length = int(row["Track Length in Milliseconds"])
    return min(milliseconds, track_length) if track_length > 0 else milliseconds


def add(group, identity, display, credit, milliseconds, track, entity_id, album=None):
    card = group.setdefault(identity, {
        "seconds": 0, "events": 0, "names": Counter(), "credits": Counter(),
        "tracks": Counter(), "catalogIds": Counter(), "albums": Counter(),
        "releaseTracks": defaultdict(Counter),
    })
    card["seconds"] += milliseconds
    card["events"] += 1
    card["names"][display] += milliseconds
    if credit:
        card["credits"][credit] += milliseconds
    if track:
        card["tracks"][track] += milliseconds
    if entity_id:
        card["catalogIds"][entity_id] += milliseconds
    if album:
        card["albums"][album] += milliseconds
        if entity_id:
            card["releaseTracks"][album][entity_id] += milliseconds


def top_cards(mode, group, limit):
    ranked = sorted(group.items(), key=lambda entry: (-entry[1]["seconds"], entry[0]))[:limit]
    cards = []
    for identity, entry in ranked:
        card = {
            "id": public_id(mode, identity),
            "title": preferred(entry["names"]),
            "artist": preferred(entry["credits"]),
            "seconds": round(entry["seconds"] / 1000, 1),
            "events": entry["events"],
        }
        if entry["catalogIds"]:
            card["catalogId"] = preferred(entry["catalogIds"])
        if mode != "song" and entry["tracks"]:
            card["topTrack"] = preferred(entry["tracks"])
        if mode in {"song", "artist"} and entry["albums"]:
            release = preferred(entry["albums"])
            album, album_artist, album_id = release
            card.update(album=album, albumArtist=album_artist)
            if album_id:
                card["albumCatalogId"] = album_id
            if mode == "song":
                card["catalogId"] = preferred(entry["releaseTracks"][release])
        cards.append(card)
    return cards


def load_verified_albums():
    path = Path(__file__).with_name("verified-albums.json")
    mapping = {}
    for release in json.loads(path.read_text(encoding="utf-8")):
        album = (title(release["name"]), value(release["artist"]), catalog_id(release["asin"]))
        if not all(album):
            raise ValueError(f"Incomplete verified album metadata in {path.name}")
        for asin in release["trackAsins"]:
            if not catalog_id(asin) or (asin in mapping and mapping[asin] != album):
                raise ValueError(f"Invalid or conflicting verified track ID in {path.name}: {asin}")
            mapping[asin] = album
    return mapping


def build(archive, limit, timezone, audit=None):
    verified_albums = load_verified_albums()
    catalog = defaultdict(set)
    artist_aliases = defaultdict(Counter)
    named_artists = defaultdict(set)
    album_ids = defaultdict(set)
    playlist_evidence = defaultdict(set)
    known_ids = defaultdict(set)
    track_labels = defaultdict(Counter)
    track_artists = defaultdict(set)
    track_families = defaultdict(set)

    def album_key(album):
        return key(album[0]), key(album[1])

    def album_from(evidence):
        if not evidence or len({album_key(album) for album in evidence}) != 1:
            return None
        return min(evidence, key=lambda album: (not album[2], album))

    for row in archive_rows(archive, "Followed Artists and Accounts.csv"):
        asin = catalog_id(row["ASIN"])
        name = value(row["Product Name"])
        if asin and name and row["Entity Type"] == "ARTIST":
            named_artists[asin].add(name)
        if asin and name and row["Entity Type"] == "TRACK":
            track_labels[asin][title(name)] += 1

    for row in archive_rows(archive, "Year In Review.csv"):
        if row["Insight Type"] != "topArtists":
            continue
        try:
            item = json.loads(row["Product Name"])
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict) or "parentAsin" in item:
            continue
        asin = catalog_id(item.get("ASIN"))
        name = value(item.get("productName"))
        if asin and name:
            named_artists[asin].add(name)

    for row in archive_rows(archive, "Saved Music.csv"):
        asin = catalog_id(row["ASIN"])
        artist_id = catalog_id(row["Artist ASIN"])
        artist = value(row["Artist Name"])
        track = title(row["Title"])
        album = (title(row["Album Name"]), value(row["Album Artist Name"]), catalog_id(row["Album ASIN"]))
        if artist_id and artist:
            base_credit = re.sub(r"\s+\[feat\..*\]$", "", artist, flags=re.IGNORECASE)
            artist_aliases[artist_id][base_credit] += 1
        if asin and track:
            track_labels[asin][track] += 1
        if asin and artist_id:
            known_ids[asin].add(artist_id)
        if album[0] and album[1]:
            if asin:
                catalog[asin].add(album)
            if album[2]:
                album_ids[album_key(album)].add(album[2])
    for album in set(verified_albums.values()):
        album_ids[album_key(album)].add(album[2])

    for row in archive_rows(archive, "Playlists.csv"):
        track = title(row["Track Name"])
        artist = value(row["Track Artist Name"])
        album = title(row["Track Album Name"])
        if track and artist and album:
            release_key = (key(album), key(artist))
            playlist_evidence[(key(artist), key(track))].add(
                (album, artist, one(album_ids[release_key]) or "")
            )

    def artist_name(asin):
        direct = named_artists[asin]
        if len({key(name) for name in direct}) == 1:
            return min(direct)
        aliases = artist_aliases[asin]
        if len({key(name) for name in aliases}) == 1:
            return preferred(aliases)
        for candidate in sorted(aliases, key=lambda name: (len(name), name)):
            base = key(candidate)
            if all(key(other) == base or any(
                key(other).startswith(base + separator) for separator in (" & ", ",", " and ", " x ")
            ) for other in aliases):
                return candidate
        return ""

    events = {}
    for row in archive_rows(archive, "Listening History.csv", required=True):
        # Deduplicate identical rows before counting or weighting corroborating metadata.
        row_key = tuple(row.values())
        events[row_key] = row
        asin = catalog_id(row["ASIN"])
        artist_id = catalog_id(row["Artist ASIN"])
        if asin and artist_id:
            track_artists[asin].add(artist_id)
        family_id = catalog_id(row["Title Set ASIN"])
        if asin and family_id:
            track_families[asin].add(family_id)

    names = {asin: artist_name(asin) for asin in named_artists.keys() | artist_aliases.keys()}

    def event_metadata(row):
        asin = catalog_id(row["ASIN"])
        artist_id = catalog_id(row["Artist ASIN"]) or one(known_ids[asin]) or one(track_artists[asin]) or ""
        family_id = catalog_id(row["Title Set ASIN"]) or one(track_families[asin]) or ""
        track = title(row["Product Name"]) or preferred(track_labels[asin])
        return asin, artist_id, family_id, track

    matched = Counter()
    match_methods = Counter()
    missing = {mode: Counter() for mode in ("album", "song", "artist")}
    unmatched_tracks = {}
    snapshots = {mode: {} for mode in ("album", "song", "artist")}
    first_date = last_date = None
    total_milliseconds = 0
    for row in events.values():
        played_at = datetime.fromisoformat(row["Date"])
        first_date = played_at if first_date is None else min(first_date, played_at)
        last_date = played_at if last_date is None else max(last_date, played_at)
        milliseconds = duration(row)
        if not milliseconds:
            continue
        total_milliseconds += milliseconds
        asin, artist_id, family_id, track = event_metadata(row)
        artist = names.get(artist_id, "")

        # A recording family can appear on several releases. Use exact catalog IDs
        # first and only fall back to a single playlist release for the known artist/title.
        direct = catalog[asin]
        if asin in verified_albums:
            album = verified_albums[asin]
            method = "Verified Amazon Music catalog"
        elif direct:
            album = album_from(direct)
            method = "Saved track ASIN"
        else:
            album = album_from(playlist_evidence[(key(artist), key(track))])
            method = "Playlist match"
        if album:
            match_methods[method] += milliseconds
            ranked_album = album
            if album[2] == "B0GY9DSVVC" and track and key(track) != "intro" and not key(track).endswith("(commentary)"):
                # Amazon's track-by-track release interleaves the songs and spoken commentary.
                ranked_album = ("The Great Divide", "Noah Kahan", "B0GJTSV9HS")
            add(snapshots["album"], album_key(ranked_album), *ranked_album[:2], milliseconds, track, ranked_album[2])
            matched["album"] += milliseconds
        elif audit is not None:
            reason = "conflicting saved releases" if direct else "no unique local release"
            missing["album"][reason] += milliseconds
            if asin:
                candidate = unmatched_tracks.setdefault(asin, {"milliseconds": 0, "artist": artist, "title": track})
                candidate["milliseconds"] += milliseconds
        if artist:
            add(snapshots["artist"], (artist_id,), artist, "", milliseconds, track, artist_id, album)
            matched["artist"] += milliseconds
        elif audit is not None:
            reason = "artist ID has no resolved name" if artist_id else "no artist ID"
            missing["artist"][reason] += milliseconds
        if track and artist and asin and album:
            add(snapshots["song"], (family_id or asin, key(track), artist_id),
                track, artist, milliseconds, track, asin, album)
            matched["song"] += milliseconds
        elif audit is not None:
            absent = [name for name, found in (
                ("album", album), ("artist", artist), ("track ID", asin), ("title", track)
            ) if not found]
            missing["song"][", ".join(absent)] += milliseconds

    if total_milliseconds == 0 or first_date is None or last_date is None:
        raise ValueError("No listening time found in the history")
    if audit is not None:
        ranked = sorted(unmatched_tracks.items(), key=lambda item: (-item[1]["milliseconds"], item[0]))
        audit.update({
            "totalHours": total_milliseconds / 3_600_000,
            "unassignedHours": {
                mode: {reason: hours / 3_600_000 for reason, hours in counts.most_common()}
                for mode, counts in missing.items()
            },
            "unmatchedAlbumTrackCount": len(ranked),
            "unmatchedAlbumTracks": [
                {"catalogId": asin, "artist": item["artist"], "title": item["title"],
                 "hours": round(item["milliseconds"] / 3_600_000, 4)}
                for asin, item in ranked
            ],
        })
    snapshots["album"] = combine_album_editions(snapshots["album"])
    summary = {
        "startDate": first_date.astimezone(timezone).date().isoformat(),
        "endDate": last_date.astimezone(timezone).date().isoformat(),
        "totalEvents": len(events),
        "coverage": {mode: round(matched[mode] / total_milliseconds, 5) for mode in snapshots},
        "inferredAlbumShare": round(match_methods["Playlist match"] / total_milliseconds, 5),
        "views": {mode: top_cards(mode, groups, limit) for mode, groups in snapshots.items()},
    }
    print("Album coverage by match:", ", ".join(
        f"{method} {time / total_milliseconds:.2%}" for method, time in match_methods.items()
    ))
    print("Coverage:", ", ".join(f"{mode} {share:.2%}" for mode, share in summary["coverage"].items()))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Amazon Music export ZIP (kept outside the repository)")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data.js")
    parser.add_argument("--timezone", default="America/Los_Angeles", help="Timezone for coverage dates")
    parser.add_argument("--audit", type=Path, help="Write a private report of unresolved catalog IDs and listening totals")
    args = parser.parse_args()

    if args.audit and (
        args.audit.resolve() == args.out.resolve() or
        args.audit.resolve().is_relative_to(Path(__file__).resolve().parents[1])
    ):
        parser.error("Write the private audit outside the public project and use a separate path for the snapshot")
    audit = {} if args.audit else None
    with zipfile.ZipFile(args.archive) as archive:
        snapshot = build(archive, 100, ZoneInfo(args.timezone), audit)
    if args.audit:
        # Refuse to overwrite an existing private audit.
        with os.fdopen(os.open(args.audit, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as report:
            json.dump(audit, report, ensure_ascii=False, indent=2)
            report.write("\n")
    content = json.dumps(snapshot, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.out.parent, delete=False) as file:
        file.write(f"// Generated by tools/import-amazon.py. Aggregate metrics and catalog metadata only.\nexport const snapshot = {content};\n")
    os.replace(file.name, args.out)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
