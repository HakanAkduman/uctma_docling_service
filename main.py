import os
import shutil
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from docling_processor import (
    process_file_with_docling,
    SUPPORTED_EXTENSIONS,
)

app = FastAPI(
    title="Docling Converter Service",
    description=(
        "Docling kullanarak belgeleri, ses dosyalarını ve video dosyalarını "
        "Markdown'a dönüştüren ve isteğe bağlı olarak chunk'lara bölen servis."
    ),
    version="1.1.0",
)

# Docling converter nesnesi paylaşımlıdır; dönüşümler sırayla çalıştırılır.
# Dönüşüm ayrı bir thread'de yürüdüğü için /health bu sırada da cevap verir.
_conversion_lock = threading.Lock()


@app.get("/health", summary="Servis sağlık kontrolü")
async def health():
    return {
        "status": "healthy",
        "service": "Docling Converter Service",
    }


def save_uploaded_file(file: UploadFile) -> str:
    """
    Yüklenen dosyayı geçici bir dosyaya kaydeder.
    Dosya uzantısını koruyarak Docling'in formatı tanımasını sağlar.
    """

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Dosya adı bulunamadı.",
        )

    suffix = Path(file.filename).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))

        raise HTTPException(
            status_code=400,
            detail=(
                f"Desteklenmeyen dosya formatı: {suffix or '(uzantısız)'}. "
                f"Desteklenen formatlar: {supported}"
            ),
        )

    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as tmp:
            tmp_path = tmp.name
            shutil.copyfileobj(file.file, tmp)

        return tmp_path

    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def convert_uploaded_file(
    file: UploadFile,
    conversion_type: str,
    max_tokens: int = 400,
) -> dict:
    """
    Dosyayı kaydeder, Docling ile işler ve geçici dosyayı temizler.
    """

    tmp_path = save_uploaded_file(file)

    try:
        with _conversion_lock:
            return process_file_with_docling(
                file_path=tmp_path,
                conversion_type=conversion_type,
                max_tokens=max_tokens,
            )

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post(
    "/convert",
    summary="Desteklenen dosyaları Markdown'a dönüştürür",
)
async def convert(file: UploadFile = File(...)):
    try:
        result = await run_in_threadpool(
            convert_uploaded_file,
            file=file,
            conversion_type="CONVERT",
        )

        return JSONResponse(
            content={
                "filename": file.filename,
                "convertedText": result["convertedText"],
            }
        )

    except HTTPException:
        raise

    except Exception as e:
        print(f"[CONVERT ERROR] {file.filename}: {e}")

        raise HTTPException(
            status_code=500,
            detail=f"Dönüştürme sırasında hata oluştu: {str(e)}",
        )

    finally:
        await file.close()


@app.post(
    "/convert-and-chunk",
    summary="Dosyayı dönüştürür ve chunk'lara böler",
)
async def convert_and_chunk(
    file: UploadFile = File(...),
    max_tokens: int = Query(default=400, ge=1, le=10000),
):
    try:
        result = await run_in_threadpool(
            convert_uploaded_file,
            file=file,
            conversion_type="CONVERT_AND_CHUNK",
            max_tokens=max_tokens,
        )

        return JSONResponse(
            content={
                "filename": file.filename,
                "convertedText": result["convertedText"],
                "chunks": result["chunks"],
            }
        )

    except HTTPException:
        raise

    except Exception as e:
        print(f"[CONVERT AND CHUNK ERROR] {file.filename}: {e}")

        raise HTTPException(
            status_code=500,
            detail=f"Dönüştürme veya chunking sırasında hata oluştu: {str(e)}",
        )

    finally:
        await file.close()
