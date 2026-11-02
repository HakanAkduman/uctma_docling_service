FROM python:3.11-slim

# Sistem bağımlılıkları (PDF, görsel ve OCR işleme için gerekli)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    libgomp1 \
    libpq-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Kütüphaneleri kopyalayıp yüklüyoruz
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --default-timeout=1000 -r requirements.txt

# Tüm proje kodlarını (main.py, docling.py, consumer.py vb.) kopyalıyoruz
COPY . .

# Shared Volume için gerekli dizinleri oluşturuyoruz
RUN mkdir -p /app/uploads/chunks
RUN mkdir -p /app/models_cache

# Docling / HuggingFace modellerinin indirileceği dizin
ENV HF_HOME=/app/models_cache
ENV XDG_CACHE_HOME=/app/models_cache
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Varsayılan olarak yalnızca HTTP API çalışır (port 8000).
# ENABLE_CONSUMER=true verilirse RabbitMQ consumer da aynı konteynerde başlar.
EXPOSE 8000
CMD ["sh", "/app/docker-entrypoint.sh"]