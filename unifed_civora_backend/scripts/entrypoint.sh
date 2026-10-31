#!/usr/bin/env bash
set -euo pipefail

echo "Applying Civora Backend database migrations..."
python manage.py migrate --noinput

echo "Starting Civora Backend API server..."
exec "$@"

