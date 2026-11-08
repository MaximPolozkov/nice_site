#!/usr/bin/env bash
# Обновление после загрузки новой версии кода:  sudo bash deploy/update.sh
# База (том data) и сертификаты при пересборке сохраняются.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T worker python cli.py backup || true
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps
