# dashboard

Static data stories published at **https://dashboard.idzharulhuda.com**. An index at the root
links to `gempa/` and `pisa/`, each plate still one self-contained page for one dataset. No runtime
dependencies: every figure is generated from stored source files at build time and then re-checked by two
acceptance gates. No network access at runtime, no API keys, no build step for the pages themselves.

Plate 01 and plate 02 shipped.

## Plate 01: Setengah Abad Gempa Indonesia

**10.516** earthquakes of magnitude 5.0 or larger recorded inside Indonesian territory between
**1976-01-04 10:56 WIB** and **2026-10-12 04:34 WIB**, filtered from a 12.689 row USGS catalogue by the
region field of each record.

The dataset is **append-only**: the tail moves as new earthquakes are recorded, and the past does not. A
scheduled job harvests new events daily, rebuilds the page, runs every gate, and publishes only if all of
them pass. Because the counts therefore change, the exact numbers are no longer frozen; what is enforced
instead is that they are always recomputed, and that the claims made in prose stay true of the data.

Seven bands: the epicentre field, the province ranking, the depth distribution, the magnitude ladder, the
decade totals, the latest BMKG snapshot, and the method notes.

`gempa/index.html` is self-contained. Markup, CSS, JavaScript and the five embedded fonts live in one ~410 KB
file, and the page makes no request beyond the file itself.

## Plate 02: PISA 2025

Indonesian student performance across science, reading, and mathematics in PISA 2025 compared against the
OECD average: the headline gap, two decades of trend (2006 to 2025), proficiency distribution and the
share below Level 2, score dispersion (P10 to P90), socio-economic disparity across ESCS quartiles, and
comparison against regional ASEAN peers.

Seven bands: the hero score gap and Level 2 benchmark, the two-decade trend, proficiency profiles across
the three domains, the score spread, socio-economic gradients, ASEAN peer rankings, and the method notes.

The numbers come from two frozen sources: the OECD public-use student file (`CY09_MS_STU_PUF.sav`),
extracted once by `pipeline_pisa/derive_from_puf.py`, and the official annex workbook (`mrq53f.xlsx`),
extracted once by `pipeline_pisa/extract_annex.py`. The resulting tables are committed under `data/pisa/`
as frozen CSVs.

Standard errors are computed from five plausible values and eighty replicate weights using Fay's balanced
repeated replication. The total variance is B plus U: between-imputation variance B across the five plausible
values, and sampling variance U across the eighty replicate weights. The formula is:

    SE = sqrt(U + B)
    where B = var(theta_m, ddof=1) over m=1..5
    U = 4 * mean_m(var_r(theta_mr, ddof=1)) over r=1..80
    (B = 0 for statistics without plausible values)

The annex is the authority for every published figure. The microdata reconciliation against the annex is
reported directly on the page, showing that the student microdata matches the official tables within
reconciliation tolerances.

The published page `pisa/index.html` is regenerated deterministically from `pisa.template.html`, the frozen CSVs,
and embedded fonts. A SHA-256 stamp over its sorted input hashes is embedded as a comment at the top of the
generated page. Both acceptance gates (`pipeline_pisa/verify.py` and `pipeline_pisa/probe_browser.py`) must
pass before publishing.

The build for plate 02 needs no third-party package at run time: `build_data.py`, `verify.py`, and
`test_verify.py` use the standard library only. Only the two extraction scripts need `pyreadstat`, `pandas`,
and `openpyxl`, and they are run by hand.

## Layout

    index.html              the hub linking to gempa/ and pisa/
    gempa/index.html          plate 01 published page, generated
    gempa.template.html       plate 01 page source, with data and fonts injected at build time
    gempa/data.json           the data payload for plate 01
    pisa/index.html          plate 02 published page, generated (payload embedded in page)
    pisa.template.html       plate 02 page source, with data and fonts injected at build time
    pipeline/               harvest, build, gate, push. The daily job for plate 01.
    pipeline_pisa/          derive_from_puf.py, extract_annex.py, build_data.py, verify.py,
                            test_verify.py, probe_browser.py
    data/pisa/              the frozen CSVs extracted from the annex and PUF microdata
    fonts/                  vendored WOFF2 fonts embedded into the templates
    data/raw/               plate 01 source files. The base harvest is never written to.
    data/out/               plate 01 derived table, live snapshot, and build report

Note that pisa has no data.json: its payload is embedded in the page.

## Keeping it current

One command does the whole loop, and it is the one the schedule runs:

    bash pipeline/publish.sh

It harvests, rebuilds, runs both gates, and pushes only if everything passed. Failures do not publish:
a bad harvest stops at the gate instead of reaching the site. Exit codes are **0** published or nothing to
publish, **1** a gate failed, **2** the repository or the machine was not in a state where pushing is safe.

New events land in `data/raw/usgs_append.csv`, not in the base file. The base harvest is the evidence for
every number the page has already published, so it stays byte-stable and the provenance hash in the footer
keeps meaning something. `build_source.py` merges the two and removes duplicates by USGS event id, with the
base winning, so a bad append cannot rewrite history.

`append_source.py` refuses a response that does not prove it is the answer to the query it sent: a CSV
content type, the expected columns, and every row inside the requested box and window. This is not
theoretical. A wrong path on the USGS host answers **HTTP 200 with an HTML documentation page**; parsed
naively it yields rows with no `place` field, every one of them is classified as foreign and dropped, and
the job reports success having harvested nothing. A harvest that cannot tell "no new earthquakes" from "the
request was not understood" is worse than no harvest.

## Build

    python3 pipeline/build_source.py
    python3 pipeline/build_data.py
    python3 pipeline/verify.py
    python3 pipeline/probe_browser.py

Every script is standard library only and deterministic: no wall clock, no randomness, no network except in
`append_source.py`, which is the only step that fetches anything. Running the build twice from the same
inputs produces a byte-identical `index.html`. Verified, not assumed.

## What the gates check

`verify.py` recomputes every figure independently from the event table without importing the generator, then
compares. It checks the headline figures, the arrays, the five largest events, the SVG field paths
character by character, the live block, the embedded data, the absence of an em dash, the absence of network
URLs, the dropped-record breakdown against the build report, that the record accounting closes
(`kept + dropped == raw`), that the displayed percentages sum to exactly 100,0, and that the four numeric
claims made in prose headlines are **still true of the data**. That last one matters more now than it did
when the page was static: "M9 CUMA SATU" is a claim about the current catalogue, so if a second magnitude 9
is ever recorded the build fails and asks for new copy rather than publishing a headline that has quietly
become false.

`build_data.py` holds invariants rather than frozen counts. While the dataset was a one-off snapshot it
asserted six exact totals, which was correct then and would now fail on the first legitimate append. What it
checks instead are relationships that survive growth: rows sorted by time, no duplicate event ids, band
labels from the known sets, every partition summing to the row total, the accounting closing, and the
reported last event matching the newest row. Growth changes the size of the parts; corruption changes how
they relate.

`probe_browser.py` renders the page in real Chromium contexts at 390x844 and 1440x900 and measures it:
offline proof, console errors, horizontal overflow, font loading, WCAG contrast on the pairs that actually
render, keyboard order and focus, label overlap in the SVG, em dash count, **the effective text size of every
label inside the epicentre SVG**, and screenshots.

That size check earns its place. SVG text is sized in viewBox units, so a fixed `font-size` in a fixed
`viewBox` scales with the rendered width: the same declaration produced 15,6 px on a desktop and **3,3 px on
a phone**, and the label-overlap check could not see it because overlap and size are different questions.
The map was unreadable on mobile for the whole life of the first version.

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
