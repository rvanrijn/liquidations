#!/usr/bin/env bash
# RSI 4h forward-test cron setup — idempotently installs the two crontab
# entries that drive RSI/rsi_4h_forward.py. Safe to re-run.
#
#   bash scripts/rsi_4h_cron.sh
#
# Both entries `cd` into the repo and source RSI/data/tg.env (gitignored) so
# Telegram alerts work. If tg.env is missing/empty the tool stays silent
# (no-op) and never errors — see telegram_notify().
#
#   1. poll     every 4h at :05 — books live + shadow trades, pushes entry alerts
#   2. compare  one-shot 2026-07-03 09:00 — regime-vs-live verdict to Telegram.
#               Fires on that date; REMOVE the line once it has run.
set -euo pipefail

REPO="/Users/rvanrijn/Developer/test/liquidations"
PY="/Users/rvanrijn/miniconda3/bin/python3"
ENVSRC='{ [ -f RSI/data/tg.env ] && . RSI/data/tg.env || true; }'

POLL="5 */4 * * * cd $REPO && $ENVSRC && $PY RSI/rsi_4h_forward.py once >> RSI/data/rsi_4h_cron.log 2>&1"
CMP="0 9 3 7 * cd $REPO && $ENVSRC && $PY RSI/rsi_4h_forward.py compare --push >> RSI/data/rsi_4h_cron.log 2>&1"

cur="$(crontab -l 2>/dev/null || true)"
add=""
grep -qF 'rsi_4h_forward.py once'    <<<"$cur" || add+="$POLL"$'\n'
grep -qF 'rsi_4h_forward.py compare' <<<"$cur" || \
  add+="# RSI 4h regime-vs-live check-in (one week); remove after it fires"$'\n'"$CMP"$'\n'

if [ -n "$add" ]; then
  printf '%s\n%s' "$cur" "$add" | crontab -
  echo "cron updated, added:"; printf '%s' "$add"
else
  echo "both RSI 4h cron entries already present — no change"
fi
