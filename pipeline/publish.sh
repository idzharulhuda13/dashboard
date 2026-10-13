#!/usr/bin/env bash
#
# Append, rebuild, gate, publish. One run of the whole loop.
#
# Order matters and every step is a gate. Nothing is pushed unless the data, the figure
# recomputation, and the rendered page all pass, so a bad harvest stops here instead of
# reaching the public site.
#
#   1. append_source   fetch new events, refresh the BMKG snapshots
#   2. build_source    rebuild the derived event table from base + appends
#   3. build_data      rebuild data.json and the page, checking the growth invariants
#   4. verify          recompute every published figure from the CSV, independently
#   5. probe_browser   render the page and measure it at 390px and 1440px
#   6. publish         commit and push, only if the artefacts actually changed
#
# Exit codes: 0 published or nothing to publish; 1 a gate failed; 2 the repository was not
# in a state where pushing is safe (dirty tree from something else, or a diverged remote).
#
# Costs nothing to run: no model is called anywhere in this loop.

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
LOG="${LOG:-$ROOT/data/out/publish.log}"

# Which interpreter to use. A scheduled job does not inherit an interactive shell's PATH, so
# `python3` there can resolve to an interpreter without the gate's dependencies. Callers may
# pass PYTHON; otherwise pick the first candidate that can actually import what the gates need,
# and say so rather than failing later with a misleading message.
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in "$(command -v python3 2>/dev/null)" /usr/bin/python3 /usr/local/bin/python3; do
    [ -n "$cand" ] && [ -x "$cand" ] || continue
    if "$cand" -c "import playwright" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
# No interpreter has the browser gate's dependency. That is a machine problem, and it is better
# to say so than to let the probe report a defect in a page it never rendered.
if [ -z "$PY" ]; then
  echo "ABORT: no python3 found that can import playwright, so the page cannot be measured" \
    | tee -a "$LOG" >&2
  exit 2
fi

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
die() { log "ABORT: $*"; exit "${2:-1}"; }

mkdir -p "$(dirname "$LOG")"
log "=== publish start (interpreter: $PY) ==="

# --- 1. harvest -------------------------------------------------------------------------
if ! "$PY" pipeline/append_source.py >>"$LOG" 2>&1; then
  die "append_source failed; nothing was published" 1
fi
NEW=$("$PY" -c "import json;print(json.load(open('data/out/append_report.json'))['new_events'])" 2>/dev/null || echo "?")
log "harvest: ${NEW} new event(s)"

# --- 2. derive --------------------------------------------------------------------------
"$PY" pipeline/build_source.py >>"$LOG" 2>&1 || die "build_source failed" 1
"$PY" pipeline/build_data.py   >>"$LOG" 2>&1 || die "build_data failed its invariants" 1
log "build: ok"

# --- 3. gates ---------------------------------------------------------------------------
"$PY" pipeline/verify.py >>"$LOG" 2>&1
V=$?
if [ "$V" -ne 0 ]; then
  log "verify output:"
  "$PY" pipeline/verify.py 2>&1 | grep -E "FAIL|TOTAL" | tee -a "$LOG"
  die "verify failed (exit $V); nothing was published" 1
fi
log "verify: pass"

"$PY" pipeline/probe_browser.py >>"$LOG" 2>&1
P=$?
case "$P" in
  0) log "probe: pass ($(tail -1 "$LOG"))" ;;
  2) die "probe could not start a browser (INFRA-FAIL); the page was never measured" 2 ;;
  *) die "probe found a defect in the rendered page (exit $P)" 1 ;;
esac

# --- 4. publish -------------------------------------------------------------------------
# Something else may have touched the tree; refuse rather than sweep it into a release.
if [ -n "$(git status --porcelain -- ':!data/out' ':!data/raw' ':!p01/index.html' ':!p01/data.json')" ]; then
  git status --short | head -10 | tee -a "$LOG"
  die "working tree has changes outside the generated artefacts; commit or revert them first" 2
fi

git add -A p01/index.html p01/data.json data/raw data/out 2>/dev/null
if git diff --cached --quiet; then
  log "nothing changed; no commit"
  log "=== publish end (no-op) ==="
  exit 0
fi

TOTAL=$("$PY" -c "import json;print(json.load(open('p01/data.json'))['fig']['total']['d'])" 2>/dev/null || echo "?")
LAST=$("$PY" -c "import json;print(json.load(open('data/out/build_report.json'))['last_event'])" 2>/dev/null || echo "?")
git -c user.name=idzharulhuda13 -c user.email=idzharulhuda@gmail.com \
    commit -q -m "Append gempa baru: total ${TOTAL}, terakhir ${LAST}" || die "commit failed" 2

# Another writer may have pushed (the Pages UI does this when the custom domain changes), so
# rebase rather than force. A conflict means a human should look, not that we should win.
if ! git pull --rebase --quiet origin main >>"$LOG" 2>&1; then
  git rebase --abort >>"$LOG" 2>&1
  die "remote diverged and the rebase conflicted; resolve by hand" 2
fi

if ! git push --quiet origin main >>"$LOG" 2>&1; then
  die "push failed" 2
fi

log "published: total ${TOTAL}, last event ${LAST}, $(git log --oneline -1)"
log "=== publish end (ok) ==="
exit 0
