#!/bin/sh

RAPIDOCR_DIR=/app/models_cache/RapidOcr
LAYOUT_MODEL_DIR=/app/models_cache/docling-project--docling-layout-heron

if [ ! -f "$RAPIDOCR_DIR/PP-OCRv6_det_small.pth" ] \
    || [ ! -f "$RAPIDOCR_DIR/ch_ppocr_mobile_v2.0_cls_mobile.pth" ] \
    || [ ! -f "$RAPIDOCR_DIR/PP-OCRv6_rec_small.pth" ] \
    || [ ! -f "$RAPIDOCR_DIR/ppocrv6_dict.txt" ]; then
    echo "[MODELS] RapidOCR artifacts are missing; attempting a one-time download."
    if ! docling-tools models download rapidocr \
        --rapidocr-backend-lang torch:ch \
        -o /app/models_cache; then
        echo "[MODELS] RapidOCR download failed; starting the service anyway."
    fi
fi

if [ ! -d "$LAYOUT_MODEL_DIR" ]; then
    echo "[MODELS] Docling layout model is missing; attempting a one-time download."
    if ! docling-tools models download-hf-repo \
        docling-project/docling-layout-heron \
        -o /app/models_cache; then
        echo "[MODELS] Docling layout model download failed; starting the service anyway."
    fi
fi

# Varsayılan (bağımsız) mod: yalnızca HTTP API çalışır.
# ENABLE_CONSUMER=true ise RabbitMQ consumer da aynı konteynerde başlatılır.
if [ "${ENABLE_CONSUMER:-false}" = "true" ]; then
    uvicorn main:app --host 0.0.0.0 --port 8000 &
    exec python consumer.py
fi

exec uvicorn main:app --host 0.0.0.0 --port 8000
