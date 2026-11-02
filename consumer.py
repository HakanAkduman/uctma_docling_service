import json
import os
import time

import pika
import requests
from docling_processor import process_file_with_docling, process_url_with_docling

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", 5672))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
QUEUE_NAME = os.getenv("RABBITMQ_QUEUE_FILE", "file_processing_queue")
RECONNECT_DELAY_SECONDS = 5
RABBITMQ_HEARTBEAT_SECONDS = int(os.getenv("RABBITMQ_HEARTBEAT_SECONDS", 600))

BACKEND_CALLBACK_URL = os.getenv("BACKEND_CALLBACK_URL")
CALLBACK_SHARED_SECRET = os.getenv("CALLBACK_SHARED_SECRET")


def format_user_error(error: Exception, file_path: str, original_file_name: str) -> str:
    if isinstance(error, FileNotFoundError):
        return "İşlenecek dosya bulunamadı. Dosyayı yeniden yükleyip tekrar deneyin."

    if isinstance(error, requests.Timeout):
        return "URL isteği zaman aşımına uğradı. Lütfen tekrar deneyin."

    if isinstance(error, requests.HTTPError):
        status_code = error.response.status_code if error.response is not None else None
        if status_code is not None:
            return f"URL sayfası HTTP {status_code} hatası döndürdü."
        return "URL sayfası isteği başarısız oldu."

    if isinstance(error, requests.RequestException):
        return "URL sayfasına erişilemedi. Adresi kontrol edip tekrar deneyin."

    message = str(error).strip()
    if file_path:
        message = message.replace(file_path, original_file_name or "dosya")
    return message or "Dönüştürme sırasında beklenmeyen bir hata oluştu."


def send_callback(payload: dict):
    try:
        res = requests.post(
            BACKEND_CALLBACK_URL,
            json=payload,
            headers={"X-UCTMA-Callback-Secret": CALLBACK_SHARED_SECRET},
            timeout=60,
        )
        print(
            f"[CALLBACK] Status: {res.status_code}, "
            f"Response: {res.text[:500]}"
        )
        res.raise_for_status()
        return True, False  #burada birden fazla değer döndürüyoruz, 
         # ilk değer callback'in başarılı olup olmadığını, ikinci değer ise kalıcı bir hata olup olmadığını belirtiyor.
         # bu sayede eğer callback başarısız olursa ve kalıcı bir hata değilse, mesajı tekrar kuyruğa atabiliriz.
         # false false dönerse, mesajı tekrar kuyruğa atabiliriz. true false veya false true şeklinde dönerse mesajı
         # tekrar değerlendirmeyi kabul edebiliriz. Aşağıdaki returnler de aynı mantıkla çalışıyor.
    except requests.HTTPError as e:
        # 4xx usually means the callback payload/endpoint is invalid for this
        # message and retrying it would create an endless poison-message loop.
        status_code = e.response.status_code if e.response is not None else None
        return False, status_code is not None and 400 <= status_code < 500
    except Exception as e:
        print(f"[CALLBACK ERROR] Backend'e ulaşılamadı: {e}")
        return False, False


def acknowledge_message(ch, delivery_tag):
    if ch.is_open:
        ch.basic_ack(delivery_tag=delivery_tag)


def reject_message(ch, delivery_tag):
    if ch.is_open:
        ch.basic_nack(delivery_tag=delivery_tag, requeue=True)

def callback(ch, method, properties, body):

    print(f"\n[*] RabbitMQ'dan mesaj alındı: {body.decode('utf-8')}")
    data = json.loads(body.decode('utf-8'))

    file_id = data.get("fileId")

    file_path = data.get("filePath")

    conversion_type = data.get("conversionType")

    max_tokens = data.get("maxTokens") or 400
    file_type = data.get("fileType")

    print(f"\n[*] RabbitMQ'dan mesaj alındı -> File ID: {file_id}")

    callback_sent, callback_is_permanent_error = send_callback({
        "fileId": file_id,
        "status": "PROCESSING",
        "convertedText": None,
        "chunks": [],
        "errorMessage": None
    })
    if not callback_sent:
        if callback_is_permanent_error:
            acknowledge_message(ch, method.delivery_tag)
        else:
            reject_message(ch, method.delivery_tag)
        return

    try:
        if file_type == "URL":
            result = process_url_with_docling(
                file_path,
                conversion_type,
                max_tokens,
            )
        else:
            result = process_file_with_docling(file_path, conversion_type, max_tokens)
        payload = {
            "fileId": file_id,
            "status": "COMPLETED",
            "convertedText": result.get("convertedText"),
            "chunks": result.get("chunks"),
            "errorMessage": None
        }
    except Exception as e:
        print(f"[ERROR] İşlem sırasında hata: {e}")
        callback_sent, callback_is_permanent_error = send_callback({
            "fileId": file_id,
            "status": "FAILED",
            "convertedText": None,
            "chunks": [],
            "errorMessage": format_user_error(
                e,
                file_path,
                data.get("originalFileName", ""),
            )
        })
        if callback_sent or callback_is_permanent_error:
            acknowledge_message(ch, method.delivery_tag)
        else:
            reject_message(ch, method.delivery_tag)
        return

    callback_sent, callback_is_permanent_error = send_callback(payload)
    if callback_sent or callback_is_permanent_error:
        acknowledge_message(ch, method.delivery_tag)
    else:
        reject_message(ch, method.delivery_tag)

def start_consumer():
    if not CALLBACK_SHARED_SECRET or len(CALLBACK_SHARED_SECRET) < 32:
        raise RuntimeError(
            "CALLBACK_SHARED_SECRET must be configured with at least 32 characters."
        )

    while True:
        conn = None
        try:
            creds = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            params = pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=creds,
                heartbeat=RABBITMQ_HEARTBEAT_SECONDS,
                blocked_connection_timeout=300,
            )
            conn = pika.BlockingConnection(params)
            channel = conn.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=callback)

            print(f"[*] RabbitMQ Consumer hazır. '{QUEUE_NAME}' kuyruğu dinleniyor...")
            channel.start_consuming()
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.ConnectionClosedByBroker) as e:
            print(
                f"[RABBITMQ] Bağlantı kapatıldı: {e}. "
                f"{RECONNECT_DELAY_SECONDS} saniye içinde yeniden bağlanılacak."
            )
            time.sleep(RECONNECT_DELAY_SECONDS)
        finally:
            if conn is not None and not conn.is_closed:
                conn.close()

if __name__ == "__main__":
    start_consumer()