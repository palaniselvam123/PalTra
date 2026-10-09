#!/usr/bin/env bash
# Outside watcher for PalTra: asks /api/health/deep and, when the app is not working
# during NSE market hours, rings the owner on Telegram through CallMeBot.
# Runs from GitHub Actions (.github/workflows/health-watch.yml), so it keeps working
# when the Fly machine is frozen or down. Read-only: it never touches an order.
#
# Environment:
#   HEALTH_URL       default https://paltra.fly.dev/api/health/deep
#   CALLMEBOT_USER   Telegram @username or phone to ring (a secret: never printed)
#   STREAK           failed runs in a row before this one (the workflow counts them)
#   TEST_CALL=1      ring once with a test message and stop (checks the setup)
#   DRY_RUN=1        say what would be done, call nobody
#   NOW_IST          "<weekday 1-7> <HHMM>" to pretend the time (tests)
# Exit 0 = healthy. Exit 1 = not working (the run shows red, which is the streak).
set -u
URL="${HEALTH_URL:-https://paltra.fly.dev/api/health/deep}"
STREAK="${STREAK:-0}"
FIRST_CALL_AT=2   # failed runs in a row before the first call (about 10 minutes at a 5-minute cadence)
REPEAT_EVERY=6    # then ring again every 6 failed runs (about 30 minutes)

ring() {
  local text="$1"
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "DRY RUN: would ring: $text"
    return 0
  fi
  if [ -z "${CALLMEBOT_USER:-}" ]; then
    echo "::error::CALLMEBOT_USER is not set, so nobody can be rung"
    return 1
  fi
  curl -sS -m 40 -G "http://api.callmebot.com/start.php" \
    --data-urlencode "user=${CALLMEBOT_USER}" \
    --data-urlencode "text=${text}" \
    --data-urlencode "lang=en-IN-Standard-B" \
    --data-urlencode "rpt=2" -o /tmp/callmebot.out || echo "CallMeBot did not answer"
  head -c 300 /tmp/callmebot.out 2>/dev/null | tr '\n' ' '
  echo
}

if [ "${TEST_CALL:-0}" = "1" ]; then
  ring "This is a test call from Pal Tra. If you can hear this, the alert works."
  exit 0
fi

body=/tmp/health_body.json
check() {
  code=$(curl -sS -m 25 -o "$body" -w '%{http_code}' "$URL" 2>/tmp/health_err) || code="000"
}
check
if [ "$code" != "200" ]; then
  sleep 10
  check
fi

if [ "$code" = "200" ]; then
  echo "Healthy (HTTP 200)."
  exit 0
fi

if [ "$code" = "000" ]; then
  reason="the app did not answer at all"
else
  reason=$(python3 - "$body" <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    print("; ".join(data.get("reasons") or []) or "it reported a problem")
except Exception:
    print("it answered with an error")
PY
)
  [ -n "$reason" ] || reason="it answered with an error"
fi
echo "NOT WORKING (HTTP $code): $reason"

stamp="${NOW_IST:-$(TZ=Asia/Kolkata date '+%u %H%M')}"
dow=${stamp%% *}
hm=$((10#${stamp##* }))
if [ "$dow" -ge 6 ] || [ "$hm" -lt 910 ] || [ "$hm" -gt 1540 ]; then
  echo "Outside NSE market hours (Mon-Fri 09:10-15:40 IST): no call. The run is red so it shows in Actions."
  exit 1
fi

n=$((STREAK + 1))
if [ "$n" -eq "$FIRST_CALL_AT" ] || { [ "$n" -gt "$FIRST_CALL_AT" ] && [ $(((n - FIRST_CALL_AT) % REPEAT_EVERY)) -eq 0 ]; }; then
  echo "Failed $n checks in a row: ringing."
  ring "Pal Tra alert. The trading app is not working. ${reason}. Open it and check your bots now."
else
  echo "Failed $n check(s) in a row: not ringing yet (first call at $FIRST_CALL_AT, then every $REPEAT_EVERY)."
fi
exit 1
