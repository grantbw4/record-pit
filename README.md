# Record Pit

Grant Williams’s most listened to albums, songs, and artists from an Amazon Music export. Each view contains 100 records. Circle area is proportional to listening time within the current view. Toss a record to move it through the pile, or use the rankings to find and select it.

## Run locally

From this directory, run `python3 -m http.server 8765` and open `http://localhost:8765/` in a browser. GitHub Pages can serve the root of the main branch without a build step.

## Refresh the snapshot

Use Python 3.11 or newer. Keep the original Amazon ZIP outside this repository.

```sh
python3 tools/import-amazon.py "/path/to/Amazon Music.zip"
python3 tools/resolve-art.py --cache /tmp/record-pit-art-cache.json
```

The importer reads the archive locally and writes aggregate rankings to `data.js`. It uses exact catalog IDs where possible. Album totals also include a smaller share of unique playlist matches on verified artist and song title. The source data lists coverage for each view and the share of listening time attributed to albums through playlist matches. IDs and names in `tools/verified-albums.json` come from public release tracklists.

Album rankings combine recognized deluxe editions with the standard release. Song details keep the resolved release name. Use `--audit /tmp/record-pit-audit.json` to rank unresolved IDs by listening time for the next import. The audit contains private listening information and belongs outside the repository.

With permission to look up unresolved track IDs at Amazon Music, run `python3 tools/fetch-amazon-catalog.py /tmp/record-pit-audit.json /tmp/record-pit-releases.json`. This sends only catalog IDs and confirms the album matches against Amazon’s tracklists. Keep the results local and pass `--catalog /tmp/record-pit-releases.json` to the importer and artwork resolver. The public snapshot contains the resulting rankings.

The song rankings include tracks with a resolved artist and album. The cover resolver uses public catalog metadata, with artwork from the Cover Art Archive and Amazon Music. It keeps its lookup cache outside the project. The ball pit shows cover art or artist images without visible labels. Original sticker illustrations appear while images load or if an image can’t be reached.

The public snapshot includes ranked item names, catalog IDs, totals, and the overall coverage dates. Raw listening events, individual timestamps, searches, and account or device information are excluded.
