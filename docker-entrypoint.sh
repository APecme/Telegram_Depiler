#!/bin/sh
set -eu

if [ "${TELEGRAM_DEPILER_APP_SUPERVISOR:-0}" = "1" ]; then
  exec python -m app.app_supervisor
fi

exec uvicorn app.main:api --host 0.0.0.0 --port 8000
