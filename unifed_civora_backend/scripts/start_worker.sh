#!/usr/bin/env bash
set -euo pipefail

echo "Starting Civora Celery background worker..."
exec celery -A config worker --loglevel=info

