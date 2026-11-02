import io
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

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


def output_stem(filename: str | None) -> str:
    """
    İndirilebilir çıktıların dosya adı gövdesini üretir.
    Yol bileşenleri atılır; uzantısız/boş isimde "converted" kullanılır.
    """

    stem = Path((filename or "").replace("\\", "/")).stem.strip()
    return stem or "converted"


def attachment_response(
    content: bytes,
    download_name: str,
    media_type: str,
) -> Response:
    """
    İçeriği dosya olarak indirilecek şekilde döndürür.
    Türkçe vb. karakterler için RFC 5987 (filename*) kullanılır.
    """

    ascii_name = download_name.encode("ascii", "replace").decode("ascii")
    ascii_name = ascii_name.replace("?", "_").replace('"', "_")

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(download_name)}"
            ),
        },
    )


def build_chunks_zip(stem: str, converted_text: str, chunks: list[dict]) -> bytes:
    """
    Markdown çıktısını ve her chunk'ı ayrı bir .txt dosyası olarak zip'ler.

    <stem>.md
    chunks/chunk_001.txt, chunks/chunk_002.txt, ...
    """

    width = max(3, len(str(len(chunks))))
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{stem}.md", converted_text)

        for position, chunk in enumerate(chunks, start=1):
            archive.writestr(
                f"chunks/chunk_{position:0{width}d}.txt",
                chunk["text"],
            )

    return buffer.getvalue()


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
async def convert(
    file: UploadFile = File(...),
    downloadable: bool = Query(
        default=False,
        description="true ise JSON yerine Markdown dosyası (.md) olarak indirilir.",
    ),
):
    try:
        result = await run_in_threadpool(
            convert_uploaded_file,
            file=file,
            conversion_type="CONVERT",
        )

        if downloadable:
            return attachment_response(
                content=result["convertedText"].encode("utf-8"),
                download_name=f"{output_stem(file.filename)}.md",
                media_type="text/markdown; charset=utf-8",
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
    downloadable: bool = Query(
        default=False,
        description=(
            "true ise JSON yerine zip olarak indirilir: Markdown dosyası (.md) "
            "ve chunks/ klasöründe her chunk için bir .txt dosyası."
        ),
    ),
):
    try:
        result = await run_in_threadpool(
            convert_uploaded_file,
            file=file,
            conversion_type="CONVERT_AND_CHUNK",
            max_tokens=max_tokens,
        )

        if downloadable:
            stem = output_stem(file.filename)

            return attachment_response(
                content=build_chunks_zip(
                    stem,
                    result["convertedText"],
                    result["chunks"],
                ),
                download_name=f"{stem}.zip",
                media_type="application/zip",
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
