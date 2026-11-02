#!/bin/sh
# Docling Converter Service - bağımsız (yalnızca HTTP API) başlatma betiği.
# RabbitMQ veya başka bir servis ayağa kaldırılmaz.
#
# Kullanım:
#   ./start.sh            Servisi derler ve başlatır (ilk seferde modelleri indirir)
#   ./start.sh stop       Servisi durdurur (model volume'ü korunur)
#   ./start.sh logs       Logları izler
#   ./start.sh status     Konteyner ve sağlık durumunu gösterir
#   ./start.sh prefetch   Modelleri yeniden indirir/doğrular

set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE="$ROOT_DIR/.env"
SERVICE=docling_converter_service
MODELS_MARKER=/app/models_cache/.prefetch-done

fail() {
    printf '[HATA] %s\n' "$*" >&2
    exit 1
}

command -v docker >/dev/null 2>&1 \
    || fail "Docker bulunamadı. Docker Engine veya Docker Desktop kurup tekrar deneyin: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 \
    || fail "Docker Compose eklentisi bulunamadı. 'docker compose version' çalışmalı."

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
        DOCKER="sudo docker"
    else
        fail "Docker daemon çalışmıyor veya erişim izniniz yok. Docker'ı başlatın ya da kullanıcıyı 'docker' grubuna ekleyin."
    fi
fi

for required_file in docker-compose.yml Dockerfile requirements.txt main.py docling_processor.py .env.example; do
    [ -f "$ROOT_DIR/$required_file" ] || fail "Gerekli dosya bulunamadı: $required_file"
done

if [ ! -f "$ENV_FILE" ]; then
    cp "$ROOT_DIR/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    printf '[AYAR] .env dosyası .env.example şablonundan oluşturuldu.\n'
fi

compose() {
    # Yalnızca temel compose dosyası kullanılır; RabbitMQ override dosyası dahil edilmez.
    $DOCKER compose --env-file "$ENV_FILE" \
        -f "$ROOT_DIR/docker-compose.yml" \
        --project-directory "$ROOT_DIR" "$@"
}

env_value() {
    sed -n "s/^$1=//p" "$ENV_FILE" | head -n 1
}

prefetch_models() {
    printf '[MODEL] Docling, RapidOCR ve Whisper modelleri indiriliyor (ilk seferde birkaç GB sürebilir)...\n'
    compose run --rm --no-deps --entrypoint sh "$SERVICE" -c "
        docling-tools models download -o /app/models_cache \
        && docling-tools models download rapidocr --rapidocr-backend-lang torch:ch -o /app/models_cache \
        && docling-tools models download whisper_base -o /app/models_cache/whisper \
        && touch $MODELS_MARKER
    "
}

models_ready() {
    compose run --rm --no-deps --entrypoint sh "$SERVICE" -c "[ -f $MODELS_MARKER ]" >/dev/null 2>&1
}

wait_healthy() {
    port=$(env_value API_PORT)
    port=${port:-8000}
    printf '[BEKLE] Servisin hazır olması bekleniyor (http://127.0.0.1:%s/health)...\n' "$port"
    attempt=0
    until $DOCKER exec "$SERVICE" python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" \
        >/dev/null 2>&1; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 90 ]; then
            fail "Servis 3 dakika içinde hazır olmadı. Logları kontrol edin: ./start.sh logs"
        fi
        sleep 2
    done
}

case "${1:-start}" in
    start)
        if ! models_ready; then
            prefetch_models || printf '[UYARI] Model indirme başarısız oldu. Eksik modeller ilk istekte internetten indirilmeye çalışılacak.\n' >&2
        fi
        printf '[BAŞLAT] Servis derleniyor ve başlatılıyor...\n'
        compose up -d --build
        wait_healthy
        port=$(env_value API_PORT)
        bind=$(env_value BIND_ADDRESS)
        printf '\n[HAZIR] Docling Converter Service çalışıyor.\n'
        printf 'API:      http://%s:%s\n' "${bind:-127.0.0.1}" "${port:-8000}"
        printf 'Swagger:  http://%s:%s/docs\n' "${bind:-127.0.0.1}" "${port:-8000}"
        printf 'Sağlık:   curl http://127.0.0.1:%s/health\n' "${port:-8000}"
        printf 'Durdurma: ./start.sh stop\n'
        ;;
    stop)
        compose down
        printf '[DURDU] Servis durduruldu. Model volume korundu.\n'
        ;;
    logs)
        compose logs -f --tail=100 "$SERVICE"
        ;;
    status)
        compose ps
        ;;
    prefetch)
        prefetch_models
        ;;
    *)
        fail "Bilinmeyen komut: $1 (start | stop | logs | status | prefetch)"
        ;;
esac
