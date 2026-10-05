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
# log line plus the notification below, not the status.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVER="$REPO/server"
LOG="${ALEMBIC_DRIFT_LOG:-$HOME/Library/Logs/fletcher-alembic-drift.log}"
REF_URL="${ALEMBIC_DRIFT_REF_URL:-postgresql://gt:devpass@localhost:5433/postgres}"

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
.venv/bin/python -m scripts.alembic_drift --from-railway --ref-url "$REF_URL"
rc=$?

echo "exit=$rc"
if [ "$rc" -ge 2 ]; then
  # The alarm. Exit 1 is for reading, not for paging — see the script's header.
  echo "ALERT: alembic drift check exit $rc — prod's stamp is not trustworthy"
  osascript -e 'display notification "Prod alembic stamp drift — see fletcher-alembic-drift.log" with title "Fletcher: DB DRIFT"' >/dev/null 2>&1 || true
fi
exit "$rc"
