#!/bin/bash
# alembic_drift_cron.sh — run the FLE-134 drift check on a schedule, unattended.
#
# WHY A SCHEDULE AND NOT CI: the failure this catches (FLE-133) went unnoticed for a
# day precisely because nothing deployed. A CI step on `main` fires on push — i.e. only
# when a deploy is already happening — so it cannot see a divergence that sits there on
# a quiet day. Only a time-based run can. And every hosted option needs prod's
# DATABASE_URL in a third-party secret store, whereas here the Railway CLI is already
# authenticated and the dev Postgres already exists.
#
# Install (see server/DEPLOY.md):
#   launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.fletcher.alembic-drift.plist
#
# Exit codes are the script's own, passed through unchanged: 0 clean, 1 read-only
# attention, 2 DRIFT, 3 could not run. launchd ignores them, so the alert path is the
# log line, the board comment and the notification below, not the status.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVER="$REPO/server"
LOG="${ALEMBIC_DRIFT_LOG:-$HOME/Library/Logs/fletcher-alembic-drift.log}"
REF_URL="${ALEMBIC_DRIFT_REF_URL:-postgresql://gt:devpass@localhost:5433/postgres}"

# Where an exit>=2 alarm goes on the board. FLE-81 is the standing watchdog channel:
# it sits in `backlog` and a comment on it wakes the lead, which is the only delivery
# path here that reaches an agent. Deliberately NOT FLE-134 itself — that issue is
# walled in `recovery_needed`, where comment-wakes are recorded SKIPPED.
BOARD_API="${PAPERCLIP_API_URL:-http://127.0.0.1:3100}"
BOARD_ISSUE="${ALEMBIC_DRIFT_ISSUE_ID:-e04386e9-d75b-4313-b23e-ff30fdc39f3f}"
ALARM_STATE="${ALEMBIC_DRIFT_ALARM_STATE:-$(dirname "$LOG")/fletcher-alembic-drift-alarm.json}"
# A sustained outage should stay visible once a day, not once per kickstart. Same rc
# inside this window is logged and not re-posted; a CHANGED rc always posts.
ALARM_REPEAT_SEC="${ALEMBIC_DRIFT_ALARM_REPEAT_SEC:-21600}"

# post_board_alarm RC OUTPUT_FILE — comment the alarm onto the board.
#
# Why this exists (FLE-134's last piece): on 2026-10-29 this check caught prod going
# down on the first morning, with the right verdict and severity, and wrote exit=3 into
# a logfile nobody reads. The outage then sat three days. Detection already worked;
# delivery did not. A desktop notification cannot wake an agent.
#
# Everything here is best-effort and must never change rc: a board that is down is not
# a reason to misreport the database.
post_board_alarm() {
  local rc="$1" out="$2"
  /usr/bin/python3 - "$rc" "$out" "$BOARD_API" "$BOARD_ISSUE" "$ALARM_STATE" \
      "$ALARM_REPEAT_SEC" "$LOG" <<'PY' 2>&1 || echo "WARN: board alarm not posted"
import json, os, sys, time, urllib.request

rc, out, api, issue, state_path, repeat, logpath = sys.argv[1:8]
rc, repeat = int(rc), int(repeat)

api = api.rstrip("/")
if api.endswith("/api"):
    api = api[: -len("/api")]

# Degrade open: an unreadable or absent state file must post, never suppress.
try:
    with open(state_path) as fh:
        state = json.load(fh)
except (OSError, ValueError):
    state = {}
if state.get("rc") == rc and time.time() - float(state.get("at") or 0) < repeat:
    print("board alarm suppressed: same exit=%d already posted within %ds" % (rc, repeat))
    sys.exit(0)

verdict = {2: "DRIFT — prod's schema does not match the revision it is stamped at",
           3: "COULD NOT RUN — prod's alembic_version could not be read at all"}
tail = ""
try:
    with open(out) as fh:
        tail = fh.read()[-2500:]
except OSError:
    pass

body = (
    "## Prod schema-drift alarm — `alembic_drift` exited %d\n\n"
    "**%s**\n\n"
    "Posted by the daily FLE-134 drift check (`server/scripts/alembic_drift_cron.sh`), "
    "which fires on `exit >= 2` only. Exit 1 is for reading, not for paging.\n\n"
    "```\n%s\n```\n\n"
    "Full log: `%s`. Do not hand-run alembic against prod to \"fix\" this — that practice "
    "is what the check exists to catch. Triage the stamp first.\n"
) % (rc, verdict.get(rc, "exit %d" % rc), tail.strip() or "(no output captured)", logpath)

req = urllib.request.Request(
    "%s/api/issues/%s/comments" % (api, issue),
    data=json.dumps({"body": body}).encode(),
    method="POST",
)
req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req, timeout=25) as resp:
        resp.read()
except Exception as exc:
    # One line, not a traceback. This log's job is to carry the database verdict;
    # a 30-line stack from the delivery path buries the thing worth reading, which
    # is the exact failure mode FLE-134 exists to stop.
    print("WARN: board alarm POST failed (%s: %s)" % (type(exc).__name__, exc))
    sys.exit(1)
print("board alarm posted to issue %s" % issue)

# Only stamp after a confirmed post, so a failed delivery retries tomorrow instead
# of being suppressed as already-reported.
tmp = state_path + ".tmp"
with open(tmp, "w") as fh:
    json.dump({"rc": rc, "at": time.time()}, fh)
os.replace(tmp, state_path)
PY
}

mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
echo "================ $(date -u '+%Y-%m-%dT%H:%M:%SZ') ================"

# A stale checkout makes CHECK 2 unverifiable: it reads the deployed commit's migration
# files out of local git history, so that commit has to be present. Fetch, never merge —
# this must not touch the working tree other agents and Hernan are using.
git -C "$REPO" fetch --quiet origin 2>&1 || echo "WARN: git fetch failed; CHECK 2 may not resolve the deployed commit"

# CHECK 3 needs a non-prod Postgres to build the reference in. Start the existing dev
# container if it is merely stopped; never create one, never touch anything else.
if command -v docker >/dev/null 2>&1; then
  if docker ps --format '{{.Names}}' | grep -qx gt-postgres; then
    :
  elif docker ps -a --format '{{.Names}}' | grep -qx gt-postgres; then
    echo "starting the stopped gt-postgres container for CHECK 3"
    docker start gt-postgres >/dev/null 2>&1 || echo "WARN: could not start gt-postgres"
    sleep 5
  fi
fi

# Railway's CLI resolves the project from a linked directory, and the link is per-dir
# state that can go stale. Re-assert it; harmless when already correct.
( cd "$SERVER" && railway link --project fletcher --environment production \
    --service guitar-trainer-v2 >/dev/null 2>&1 ) \
  || echo "WARN: railway link failed; CHECK 2 will report UNVERIFIED"

cd "$SERVER" || exit 3

# tee so the run's own output lands in the log AND is available verbatim to the board
# comment below. pipefail is on, so read python's status off PIPESTATUS, not $? (which
# would be tee's).
OUT="$(mktemp -t fletcher-alembic-drift)"
.venv/bin/python -m scripts.alembic_drift --from-railway --ref-url "$REF_URL" 2>&1 | tee "$OUT"
rc=${PIPESTATUS[0]}

echo "exit=$rc"
if [ "$rc" -ge 2 ]; then
  # The alarm. Exit 1 is for reading, not for paging — see the script's header.
  echo "ALERT: alembic drift check exit $rc — prod's stamp is not trustworthy"
  osascript -e 'display notification "Prod alembic stamp drift — see fletcher-alembic-drift.log" with title "Fletcher: DB DRIFT"' >/dev/null 2>&1 || true
  # The delivery path that actually reaches an agent.
  post_board_alarm "$rc" "$OUT"
fi
rm -f "$OUT"
exit "$rc"
