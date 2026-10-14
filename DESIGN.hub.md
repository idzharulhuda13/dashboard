# DESIGN.hub.md — the hub at the site root (dashboard.idzharulhuda.com)

Contract for the front door of the site. Written by Arc before any writer runs; the writer treats it as direction,
the Hallmark audit grades the result against it, and the hub's own gate grades its numbers.

## What this page is for

A reader arrives from a link or a search, knowing nothing. In one screen they must understand that this is a small
collection of data stories built from public data, see the two stories that exist, and be able to enter either one
in a single click. The page's second job is quiet proof: every figure on a plate is recomputed by an acceptance
gate before it publishes, and the hub says so.

## Relationship to the plates

Same house, different object. The plates are composed posters: seven stacked bands, each with a two-tone headline
(accent span over a main span) above a chart. The hub must NOT reuse that rhythm, that band header shape, or a card
grid. It is an index: one masthead, two full-width entry rows, a small footer. Warm ground and ink are shared with
the house; each plate's accent is used only as that entry's own colour.

## Tokens

  --ground #F7F1E6   --panel #FFFBF3   --ink #1A1410   --muted #6B5B4A   --line #DCCFB8
  --accent-gempa #A83F14   --accent-pisa #2D4ECF
  --font-display 'Chakra Petch'   --font-mono 'JetBrains Mono'   (both embedded as base64 woff2, no network)
No other colour may appear. No gradient, no glow, no shadow, no capsule badge, no decorative side stripe.

## Macrostructure: Index Ledger

  masthead    a mono label, then the site name in the display face (sentence case, large), then one plain sentence
              saying what the site is.
  entry row   1 of 2, full width, separated by a hairline rule. Two columns at wide widths, stacked at narrow:
                left  - mono plate label ("Pelat 01"), the dashboard name in the display face, one line saying
                        what it shows, one plain fact line, and the link itself
                right - a "window": a small inline SVG drawn from that plate's own frozen data (below)
  footer      sources named (USGS, OECD PISA), the PISA licence (CC BY 4.0), and the sentence that every figure is
              recomputed by an acceptance gate before publishing.

## The two windows (each a real visual, not decoration)

  gempa window   a miniature of the plate's own epicentre field: the map outline and epicentre dots from the paths
                 in gempa/data.json (`field`). No numbers in this window; it is the plate's signature shape.
  pisa window    three short horizontal bars: the share of students below the minimum proficiency threshold in
                 mathematics, reading and science, each with the OECD share as a thin comparison tick. The three
                 Indonesian values reach the page as data-fig spans fed from data/pisa/annex_proficiency.csv.

## Rules the gate enforces

- Every number shown is a span[data-fig] fed by the payload; the template's fallback text for each span must equal
  the payload's display string, so a reader without JavaScript sees the same number.
- pipeline_hub/verify.py recomputes every payload number from data/pisa/*.csv, checks the gempa window's paths
  against gempa/data.json, and fails the build when anything drifts.
- No earthquake total is printed anywhere on the hub: that series is append-only and the daily job would falsify it.
- Two entries only. A link exists only for a page that exists.

## Dials

  ENERGY 2 / RHYTHM 2 / MOTION 1

  ENERGY 2: the masthead speaks clearly, the entries breathe; nothing shouts and nothing is demure.
  RHYTHM 2: the two rows share their structure (that is what an index is) but the two windows differ in kind, so
            the page never reads as one repeated block.
  MOTION 1: hover and focus states only. No scroll reveal, no parallax, no animation loop.

## Type behavior

The display face carries the site name and the two dashboard names. The mono face carries the labels, the plate
numbers, the numbers inside the pisa window and the footer's small print. Body text is the display face at a
comfortable size with generous leading. No uppercase with wide tracking except the two plate labels and the
masthead's mono label. Minimum rendered text size anywhere: 12px. Body contrast at least 4.5:1.
