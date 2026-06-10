import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.pdf.downloader import download_pdf
from app.adapters.pdf.parser import PyPdfParser
from app.adapters.storage.minio import minio_storage
from app.models.order_file import OrderFile

logger = logging.getLogger(__name__)
pdf_parser = PyPdfParser()


async def process_pdf_for_order(
    db: AsyncSession,
    order_id: str,
    pdf_url: str,
    pdf_name: str,
) -> OrderFile:
    content, sha256 = await download_pdf(pdf_url)

    storage_key = f"pdfs/{sha256}.pdf"

    existing = await _find_existing_by_sha256(db, sha256)
    if existing:
        file_record = OrderFile(
            order_id=order_id,
            original_name=pdf_name,
            source_url=pdf_url,
            storage_key=existing.storage_key,
            sha256=sha256,
            content_type="application/pdf",
            size_bytes=len(content),
            parse_status=existing.parse_status,
            parsed_text=existing.parsed_text,
        )
        db.add(file_record)
        await db.flush()
        await db.refresh(file_record)
        return file_record

    await minio_storage.upload(storage_key, content)

    parse_result = await pdf_parser.parse(content, pdf_name)

    file_record = OrderFile(
        order_id=order_id,
        original_name=pdf_name,
        source_url=pdf_url,
        storage_key=storage_key,
        sha256=sha256,
        content_type="application/pdf",
        size_bytes=len(content),
        parse_status=_parse_status_from_result(parse_result),
        parsed_text=parse_result.text if not parse_result.error_code else None,
        error_code=parse_result.error_code,
        error_message=parse_result.error_message,
    )
    db.add(file_record)
    await db.flush()
    await db.refresh(file_record)
    return file_record


def _parse_status_from_result(result) -> str:
    if result.error_code == "PDF_ENCRYPTED":
        return "FAILED"
    if result.error_code == "OCR_REQUIRED":
        return "OCR_REQUIRED"
    if result.error_code:
        return "FAILED"
    if result.is_scanned:
        return "OCR_REQUIRED"
    return "READY"


async def _find_existing_by_sha256(db: AsyncSession, sha256: str) -> OrderFile | None:
    from sqlalchemy import select

    result = await db.execute(
        select(OrderFile).where(OrderFile.sha256 == sha256).limit(1)
    )
    return result.scalars().first()
