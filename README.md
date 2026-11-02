# Docling Converter Service

[Docling](https://github.com/docling-project/docling) kullanarak belgeleri, görselleri, ses ve video dosyalarını **Markdown**'a dönüştüren ve isteğe bağlı olarak **chunk**'lara (RAG için parçalara) bölen küçük bir FastAPI servisi.

Servis tek başına çalışır: bir veritabanı, RabbitMQ veya başka bir servis gerekmez. Yalnızca Docker yeterlidir.

## İçindekiler

- [Hızlı başlangıç](#hızlı-başlangıç)
- [Desteklenen formatlar](#desteklenen-formatlar)
- [API](#api)
- [Yapılandırma](#yapılandırma)
- [start.sh komutları](#startsh-komutları)
- [Docker olmadan çalıştırma](#docker-olmadan-çalıştırma)
- [Opsiyonel: RabbitMQ consumer](#opsiyonel-rabbitmq-consumer)
- [Bilinmesi gerekenler](#bilinmesi-gerekenler)
- [Dosya yapısı](#dosya-yapısı)

## Hızlı başlangıç

Gereksinimler: Docker Engine (veya Docker Desktop) ve Docker Compose eklentisi. Disk alanı olarak imaj ve modeller için yaklaşık **15 GB** boş yer bırakın.

```sh
git clone <repo-url> docling-converter-service
cd docling-converter-service
./start.sh
```

`start.sh` sırasıyla şunları yapar:

1. Docker ve Compose'un kurulu/çalışır olduğunu kontrol eder.
2. `.env` yoksa `.env.example` şablonundan oluşturur.
3. Docling, RapidOCR ve Whisper modellerini kalıcı bir Docker volume'üne indirir (**yalnızca ilk çalıştırmada**, birkaç GB ve birkaç dakika sürebilir).
4. İmajı derler, servisi başlatır ve `/health` cevap verene kadar bekler.

Hazır olunca:

```sh
curl http://127.0.0.1:8000/health
# {"status":"healthy","service":"Docling Converter Service"}
```

Etkileşimli API dokümantasyonu (Swagger UI): <http://127.0.0.1:8000/docs>

## Desteklenen formatlar

| Tür | Uzantılar |
| --- | --- |
| Belge | `.pdf` `.docx` `.pptx` `.xlsx` `.html` |
| Düz metin | `.txt` `.md` `.csv` |
| Görsel (OCR) | `.png` `.jpg` `.jpeg` `.tif` `.tiff` `.bmp` `.webp` |
| Ses (Whisper ile transkripsiyon) | `.wav` `.mp3` `.m4a` `.aac` `.ogg` `.flac` |
| Video | `.mp4` `.avi` `.mov` `.mkv` `.webm` |

Desteklenmeyen bir uzantı gönderilirse `400` döner.

## API

Tüm uç noktalar `multipart/form-data` ile `file` alanında dosya bekler.

### `GET /health`

Servis sağlık kontrolü.

```sh
curl http://127.0.0.1:8000/health
```

### `POST /convert`

Dosyayı Markdown'a dönüştürür.

```sh
curl -X POST http://127.0.0.1:8000/convert \
  -F "file=@rapor.pdf"
```

| Parametre | Tür | Varsayılan | Açıklama |
| --- | --- | --- | --- |
| `downloadable` | query, `true`/`false` | `false` | `true` ise JSON yerine Markdown dosyası (`rapor.md`) indirilir |

Cevap:

```json
{
  "filename": "rapor.pdf",
  "convertedText": "# Başlık\n\nMarkdown içerik..."
}
```

Dosya olarak indirmek için:

```sh
curl -X POST "http://127.0.0.1:8000/convert?downloadable=true" \
  -F "file=@rapor.pdf" -OJ
```

`-OJ`, dosyayı sunucunun önerdiği adla (`rapor.md`) kaydeder.

### `POST /convert-and-chunk`

Dosyayı Markdown'a dönüştürür ve Docling `HybridChunker` ile chunk'lara böler.

| Parametre | Tür | Varsayılan | Açıklama |
| --- | --- | --- | --- |
| `max_tokens` | query, `1`–`10000` | `400` | Her chunk için en fazla token sayısı |
| `downloadable` | query, `true`/`false` | `false` | `true` ise JSON yerine zip dosyası (`rapor.zip`) indirilir |

```sh
curl -X POST "http://127.0.0.1:8000/convert-and-chunk?max_tokens=300" \
  -F "file=@rapor.pdf"
```

Cevap:

```json
{
  "filename": "rapor.pdf",
  "convertedText": "# Başlık\n\n...",
  "chunks": [
    { "chunkIndex": 0, "text": "Başlık\nİlk bölümün metni...", "pageNumbers": [1] },
    { "chunkIndex": 1, "text": "İkinci bölümün metni...", "pageNumbers": [1, 2] }
  ]
}
```

`downloadable=true` verilirse cevap JSON yerine zip olur:

```sh
curl -X POST "http://127.0.0.1:8000/convert-and-chunk?downloadable=true&max_tokens=300" \
  -F "file=@rapor.pdf" -OJ
```

```
rapor.zip
├── rapor.md                # tüm Markdown çıktısı
└── chunks/
    ├── chunk_001.txt       # her chunk ayrı bir .txt dosyası
    ├── chunk_002.txt
    └── ...
```

Zip içinde yalnızca chunk metinleri bulunur; `pageNumbers` bilgisi için JSON cevabını kullanın.

`pageNumbers`, sayfalı belgelerde (PDF vb.) chunk'ın geçtiği sayfaları verir; ses, video ve düz metinde boş liste olabilir.

### Hata cevapları

| Kod | Durum |
| --- | --- |
| `400` | Dosya adı yok veya uzantı desteklenmiyor |
| `422` | `max_tokens` aralık dışı, `downloadable` geçersiz veya `file` alanı eksik |
| `500` | Dönüştürme sırasında hata (`detail` alanında açıklama) |

Hatalar `downloadable=true` iken de JSON olarak döner.

### Python örneği

```python
import requests

with open("rapor.pdf", "rb") as f:
    res = requests.post(
        "http://127.0.0.1:8000/convert-and-chunk",
        params={"max_tokens": 400},
        files={"file": ("rapor.pdf", f)},
        timeout=600,
    )
res.raise_for_status()
data = res.json()
print(data["convertedText"][:200])
print(len(data["chunks"]), "chunk")
```

Dosya olarak indirme (Python):

```python
res = requests.post(
    "http://127.0.0.1:8000/convert-and-chunk",
    params={"downloadable": "true"},
    files={"file": ("rapor.pdf", open("rapor.pdf", "rb"))},
    timeout=600,
)
res.raise_for_status()
open("rapor.zip", "wb").write(res.content)
```

> Büyük PDF'ler, ses ve video dosyaları dakikalar sürebilir; istemci tarafında yüksek bir timeout kullanın.

## Yapılandırma

`.env` dosyası (`.env.example`'dan oluşur):

| Değişken | Varsayılan | Açıklama |
| --- | --- | --- |
| `BIND_ADDRESS` | `127.0.0.1` | Host üzerinde dinlenecek adres. Yalnızca yerel erişim için `127.0.0.1`; ağa açmak için `0.0.0.0` (bkz. [güvenlik notu](#bilinmesi-gerekenler)) |
| `API_PORT` | `8000` | Host üzerinde yayınlanan port |

`.env` içindeki `RABBITMQ_*`, `BACKEND_CALLBACK_URL` ve `CALLBACK_SHARED_SECRET` değerleri yalnızca [RabbitMQ consumer](#opsiyonel-rabbitmq-consumer) için kullanılır; bağımsız kullanımda yok sayılır.

Modeller `docling_converter_service_docling_models` adlı Docker volume'ünde saklanır, imaj yeniden derlense de korunur.

## start.sh komutları

| Komut | Açıklama |
| --- | --- |
| `./start.sh` | Modelleri (gerekirse) indirir, derler, başlatır |
| `./start.sh stop` | Servisi durdurur; model volume'ü silinmez |
| `./start.sh logs` | Logları canlı izler |
| `./start.sh status` | Konteyner durumunu gösterir |
| `./start.sh prefetch` | Modelleri yeniden indirir/doğrular |

Modelleri tamamen silmek için (bir sonraki başlatmada yeniden indirilir):

```sh
./start.sh stop
docker volume rm docling_converter_service_docling_models
```

## Docker olmadan çalıştırma

Python 3.11 ve sistemde `ffmpeg` (ses/video için) gerekir.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

# İlk kullanımdan önce modeller (isteğe bağlı; yoksa ilk istekte indirilir)
export HF_HOME="$PWD/models_cache"
docling-tools models download -o "$HF_HOME"
docling-tools models download rapidocr --rapidocr-backend-lang torch:ch -o "$HF_HOME"
docling-tools models download whisper_base -o "$HF_HOME/whisper"

uvicorn main:app --host 127.0.0.1 --port 8000
```

`HF_HOME`, `docling_processor.py` içindeki RapidOCR model yolunu da belirler (varsayılan `/app/models_cache`); Docker dışında mutlaka kendi dizininize ayarlayın.

## Opsiyonel: RabbitMQ consumer

Kodda (`consumer.py`) RabbitMQ'dan iş alıp sonucu bir backend'e callback ile bildiren bir consumer da bulunur; UCTMA backend'i ile entegrasyon için kullanılır. **Bağımsız kullanımda devre dışıdır** ve `start.sh` bunu başlatmaz.

Etkinleştirmek için `ENABLE_CONSUMER=true` verilmesi gerekir; bunu `docker-compose.rabbitmq.yml` yapar:

```sh
docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.rabbitmq.yml \
  up -d --build
```

Gereksinimler:

- Erişilebilir bir RabbitMQ ve `.env` içindeki `RABBITMQ_*` değerleri
- `uctma_default` Docker ağı (UCTMA backend compose'u oluşturur; değişiklik gerekirse `docker-compose.rabbitmq.yml` içindeki `networks` bölümünü düzenleyin)
- `.env` içinde en az 32 karakterlik `CALLBACK_SHARED_SECRET` (consumer bu olmadan başlamaz)
- `BACKEND_CALLBACK_URL`: durum/sonuç callback'lerinin gönderileceği adres

Consumer, kuyruktan `fileId`, `filePath`, `conversionType`, `maxTokens`, `fileType` (`URL` ise sayfa indirilir) alanlarını içeren JSON mesajları okur.

## Bilinmesi gerekenler

- **Kimlik doğrulama yoktur.** Servis varsayılan olarak yalnızca `127.0.0.1`'e bağlanır. Ağa açacaksanız (`BIND_ADDRESS=0.0.0.0`) önüne bir reverse proxy / API gateway ile kimlik doğrulama koyun.
- **Dosya boyutu sınırı yoktur.** Yüklenen dosya geçici diske yazılır ve işlem bitince silinir; boyut sınırını proxy katmanında uygulayın.
- **Dönüşümler sırayla çalışır.** Docling converter paylaşımlı olduğu için aynı anda gelen istekler kuyruğa girer; uzun bir dönüşüm sırasında `/health` yine de cevap verir.
- **Modeller internetten indirilir.** İlk kurulumda internet erişimi gerekir; sonrasında volume'deki modellerle çevrimdışı çalışır.
- **OCR dili:** RapidOCR yapılandırması `ch` (Çince/İngilizce) modeliyle gelir. Başka diller için `docling_processor.py` içindeki `rapid_ocr_options` ayarı değiştirilmelidir.
- CPU üzerinde çalışır; GPU yapılandırması yoktur.

## Dosya yapısı

```
.
├── main.py                      # FastAPI uygulaması (/health, /convert, /convert-and-chunk)
├── docling_processor.py         # Docling dönüştürme + chunk mantığı
├── consumer.py                  # Opsiyonel RabbitMQ consumer
├── docker-entrypoint.sh         # API'yi (ve istenirse consumer'ı) başlatır
├── Dockerfile
├── docker-compose.yml           # Bağımsız API
├── docker-compose.rabbitmq.yml  # Opsiyonel: RabbitMQ consumer override'ı
├── start.sh                     # Bağımsız başlatma betiği
├── requirements.txt
└── .env.example
```
