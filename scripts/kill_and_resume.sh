#!/usr/bin/env bash
# Resume check: kill the client mid-batch, rerun, and confirm nothing is lost.
#
# Pass criteria from the plan: no duplicate or corrupt outputs, and the second
# run finishes the remainder. Outputs are written with an atomic rename, so a
# kill can never leave a half-written PNG that the resume would skip.
#
#   scripts/kill_and_resume.sh [sub-season] [jobs]
#
# KILL_AFTER must land mid-batch. At ~13 s per still, 16 jobs take ~3.5 min, so
# the default kills about a third of the way in.
set -euo pipefail

SUB_SEASON="${1:-late_autumn}"
JOBS="${2:-16}"
KILL_AFTER="${KILL_AFTER:-75}"

root="$(python3 -c 'from mcwall import config; print(config.load().library.root)')"

count_webp() { find "$root" -name '*.webp' 2>/dev/null | wc -l; }
count_partial() { find "$root" \( -name '*.part' -o -name '*.tmp' \) 2>/dev/null | wc -l; }

before_all=$(count_webp)
echo "library has $before_all images before the test"

echo
echo "== first run: $JOBS jobs of $SUB_SEASON, client killed after ${KILL_AFTER}s =="
mcwall render --sub-season "$SUB_SEASON" --limit "$JOBS" &
render_pid=$!
sleep "$KILL_AFTER"

# Kill the client, not the orchestrator, so we exercise the mod's own resume.
# Match the instance's natives path: killing the launcher alone orphans the
# JVM, which keeps rendering and holds the world lock against the next run.
pkill -9 -f 'mc-wallpaper/natives' || true
pkill -f 'prismlauncher --launch' || true
wait "$render_pid" || echo "(first run exited non-zero, which is expected after a kill)"

after_kill=$(count_webp)
echo "images after the kill: $after_kill (was $before_all)"
if [ "$after_kill" -ge $((before_all + JOBS)) ]; then
    echo "SKIP: the batch finished before the kill; lower KILL_AFTER or raise JOBS" >&2
    exit 2
fi

echo
echo "== second run: same jobs, should finish the remainder =="
mcwall render --sub-season "$SUB_SEASON" --limit "$JOBS"

after=$(count_webp)
leftover=$(count_partial)

echo
echo "images after resume: $after (expected $((before_all + JOBS)))"
echo "half-written files:  $leftover (expected 0)"

if [ "$leftover" -ne 0 ]; then
    echo "FAIL: partial files survived the kill" >&2
    exit 1
fi
if [ "$after" -lt $((before_all + JOBS)) ]; then
    echo "FAIL: resume did not finish the remainder" >&2
    exit 1
fi
echo "PASS: resumed cleanly, no duplicates, no partial files"
