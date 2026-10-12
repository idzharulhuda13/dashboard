# dashboard

Static data stories published at **https://dashboard.idzharulhuda.com**. One page, one dataset, no runtime
dependencies: every figure is generated from stored source files at build time and then re-checked by two
acceptance gates. No network access at runtime, no API keys, no build step for the page itself.

Plate 01 shipped.

## Plate 01: Setengah Abad Gempa Indonesia

**10.516** earthquakes of magnitude 5.0 or larger recorded inside Indonesian territory between
**1976-01-04 10:56 WIB** and **2026-10-12 04:34 WIB**, filtered from a 12.689 row USGS catalogue by the
region field of each record.

Seven bands: the epicentre field, the province ranking, the depth distribution, the magnitude ladder, the
decade totals, the latest BMKG snapshot, and the method notes.

`index.html` is self-contained. Markup, CSS, JavaScript and the three embedded fonts live in one 396 KB
file, and the page makes no request beyond the file itself.

## Layout

    index.html              the published page, generated. Do not edit by hand.
    story.template.html     the page source, with the data and fonts injected at build time
    pipeline/build_source.py    raw USGS and BMKG files to the frozen event table
    pipeline/build_data.py      frozen table to data.json to index.html
    pipeline/verify.py          acceptance gate over the numbers
    pipeline/probe_browser.py   acceptance gate over the rendered page
    data/raw/                   source files exactly as downloaded
    data/out/                   the frozen table, the live snapshot, and the build report

## Build

    python3 pipeline/build_source.py
    python3 pipeline/build_data.py
    python3 pipeline/verify.py
    python3 pipeline/probe_browser.py

Every script is standard library only and deterministic: no wall clock, no randomness, no network. Running
the build twice from the same inputs produces a byte-identical `index.html`. Verified, not assumed.

## What the gates check

`verify.py` recomputes every figure independently from the frozen CSV without importing the generator, then
compares. It checks the headline figures, the arrays, the five largest events, the SVG field paths
character by character, the live block, the embedded data, the absence of an em dash, the absence of network
URLs, the dropped-record breakdown against the build report, that the record accounting closes
(`kept + dropped == raw`), that the displayed percentages sum to exactly 100,0, and that the four numeric
claims made in prose headlines are still true of the data.

`probe_browser.py` renders the page in real Chromium contexts at 390x844 and 1440x900 and measures it:
offline proof, console errors, horizontal overflow, font loading, WCAG contrast on the pairs that actually
render, keyboard order and focus, label overlap in the SVG, em dash count, and screenshots.

Exit codes: **0** passed, **1** a check failed, **2** the browser could not be started so no check ran.

Both gates were tested by making them fail. A gate that cannot fail is not a gate.

## Method, and what it cannot say

- **Scope.** An event is kept when the final comma separated field of the USGS `place` value is an
  Indonesian region. There is no substring matching anywhere, because substring matching on region names is
  how a catalogue quietly acquires foreign events.
- **Province.** Each event is assigned to the nearest Indonesian province centroid by haversine distance.
  This is a **proximity label, not an administrative boundary**.
- **Depth.** Shallow is under 70 km, intermediate is 70 km up to but not including 300 km, deep is 300 km or
  more. Percentages are allocated by the largest remainder method so the three displayed values sum to
  exactly 100,0 rather than 100,1.
- **Magnitude.** Classes are M5 through M9. The ladder chart uses a logarithmic bar length for the counts,
  and the chart says so on its own surface.
- **Decades.** The 2020s bar covers a partial period and is marked as such, with the range read from the
  data rather than typed into the copy.
- **The live block is a snapshot, not a feed.** It is the state of the BMKG endpoint when the page was
  built. The page says so instead of claiming to be live.
- **What the catalogue cannot support.** It holds events only, and no monitoring-network history. So it
  cannot measure how much of the rise in recorded events per decade is explained by a wider sensor network.
  The page states that limitation instead of asserting a cause.

## Sources

- USGS earthquake catalogue, FDSN event API, magnitude 5.0 or larger, bounding box 94.5 to 141.5 E and
  11.5 S to 6.5 N
- BMKG, latest and felt earthquake feeds
- Bapanas province reference file, 38 province centroids used for the proximity label

## Licence and attribution

The source data belongs to USGS and BMKG. Check their terms before reusing the raw files.
