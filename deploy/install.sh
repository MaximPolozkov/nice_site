#!/usr/bin/env bash
# Установка сайта «Ниши недели» на VPS (Ubuntu 22.04/24.04, Debian 12).
#
#   sudo bash deploy/install.sh                       — .env уже лежит в папке проекта
#   sudo bash deploy/install.sh /opt/kodeverks/.env   — взять ключи из .env старого сайта
#
# Домен по умолчанию kodverks.ru; другой: sudo DOMAIN=example.ru bash deploy/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DOMAIN="${DOMAIN:-kodverks.ru}"
OLD_ENV="${1:-}"

say()  { echo -e "\n== $*"; }
warn() { echo "   !! $*"; }

say "1. Docker"
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
systemctl enable --now docker >/dev/null

say "2. Файрвол: SSH, HTTP, HTTPS"
if command -v ufw >/dev/null; then
  ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443 >/dev/null
  ufw --force enable >/dev/null
fi

say "3. Настройки .env"
if [ -n "$OLD_ENV" ]; then
  [ -f "$OLD_ENV" ] || { echo "Нет файла $OLD_ENV"; exit 1; }
  if [ -f .env ]; then
    # .env уже есть (например, готовый kodverks.env): заполняем из старого только пустые
    # и отсутствующие ключи, заполненное не трогаем
    cp .env ".env.bak.$(date +%s)"
    awk 'NR==FNR {
           if (match($0, /^[A-Z_]+=/)) { k = substr($0, 1, RLENGTH-1); v = substr($0, RLENGTH+1)
             gsub(/^["'"'"']|["'"'"']$/, "", v)
             if (v != "" && v !~ /^change-me/) old[k] = v }
           next }
         match($0, /^[A-Z_]+=/) { k = substr($0, 1, RLENGTH-1); v = substr($0, RLENGTH+1)
           seen[k] = 1
           if (v == "" && (k in old)) { print k "=" old[k]; next } }
         { print }
         END { first = 1
           for (k in old) if (!(k in seen)) {
             if (first) { print ""; print "# === Из старого .env ==="; first = 0 }
             print k "=" old[k] } }' "$OLD_ENV" .env > .env.new
    mv .env.new .env
  else
    cp "$OLD_ENV" .env
    # Дописываем настройки бота ниш, которых нет в старом .env (существующие ключи не трогаем)
    ADD=$(awk -F= 'NR==FNR { if ($0 ~ /^[A-Z_]+=/) have[$1]=1; next }
                   /^[A-Z_]+=/ && !($1 in have) { print }' .env .env.example \
          | grep -vE '^(DOMAIN|SECRET_KEY|ADMIN_PASSWORD|VK_ADMIN_PEER_IDS|VK_COMMUNITY_URL)=' || true)
    if [ -n "$ADD" ]; then
      { echo; echo "# === Ниши недели (добавлено установщиком) ==="; echo "$ADD"; } >> .env
    fi
  fi
  echo "   ключи взяты из $OLD_ENV"
fi
[ -f .env ] || { echo "Нет файла .env. Скопируйте .env.example в .env и заполните, или передайте путь к старому .env."; exit 1; }

set_var() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
get_var() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '"'"'" || true; }

set_var DOMAIN "$DOMAIN"
set_var COOKIE_SECURE true
SK=$(get_var SECRET_KEY)
if [ -z "$SK" ] || [[ "$SK" == change-me* ]] || [ ${#SK} -lt 32 ]; then
  set_var SECRET_KEY "$(openssl rand -hex 32)"; echo "   сгенерирован SECRET_KEY"
fi
PW=$(get_var ADMIN_PASSWORD); [ -z "$PW" ] && PW=$(get_var FIRST_ADMIN_PASSWORD)
if [ ${#PW} -lt 10 ] || [[ "$PW" == change-me* ]]; then
  PW=$(openssl rand -base64 12 | tr -d '/+=')
  set_var ADMIN_PASSWORD "$PW"
  echo "   пароль панели: $PW   (сохраните его; он записан в .env)"
fi
if [ -z "$(get_var WORDSTAT_TOKEN)" ] && [ -z "$(get_var DEMO_MODE)" ]; then
  set_var DEMO_MODE 1
  warn "WORDSTAT_TOKEN не задан — включён демо-режим. Получите токен, впишите его и поставьте DEMO_MODE=0"
fi
chmod 600 .env

say "4. Порты 80 и 443"
BUSY=$(ss -ltnp '( sport = :80 or sport = :443 )' 2>/dev/null | tail -n +2 || true)
if [ -n "$BUSY" ] && ! docker compose ps caddy 2>/dev/null | grep -q Up; then
  warn "порты заняты другим процессом:"
  echo "$BUSY"
  warn "Остановите старый сайт (в его папке: docker compose down) и запустите установку снова."
  exit 1
fi

say "5. DNS"
IP=$(curl -fsS https://api.ipify.org || true)
DNS=$(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | head -1 || true)
echo "   IP сервера: ${IP:-?}   $DOMAIN → ${DNS:-нет записи}"
[ -n "$IP" ] && [ "$IP" != "$DNS" ] && warn "A-запись ещё не указывает на сервер — HTTPS выпустится, когда DNS обновится."

say "6. Сборка и запуск"
docker compose up -d --build

say "7. Проверка"
for _ in $(seq 1 30); do
  docker compose ps web | grep -q healthy && break; sleep 3
done
docker compose ps

say "8. Ежедневная копия базы (4:00)"
cat > /etc/cron.d/niche-site-backup <<CRON
0 4 * * * root cd $(pwd) && docker compose exec -T worker python cli.py backup >/dev/null 2>&1
CRON

echo
echo "Готово: https://$DOMAIN   панель: https://$DOMAIN/admin"
echo "Журнал бота: docker compose logs -f worker"
