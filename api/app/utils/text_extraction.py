import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from app.papers.exceptions import ExtractionError

MAX_PDF_PAGES = 500
MAX_EXTRACTED_CHARS = 2_000_000
EXTRACTION_TIMEOUT = 60

_pdf_executor = ProcessPoolExecutor(max_workers=2)
_web_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="web-extract")


def _extract_pdf_sync(file_path: str) -> str:
    """Run in separate process to isolate memory/CPU."""
    import pdfplumber

    parts = []
    total_chars = 0
    with pdfplumber.open(file_path) as pdf:
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF has {len(pdf.pages)} pages, max is {MAX_PDF_PAGES}")
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                parts.append(text)
                total_chars += len(text)
                if total_chars > MAX_EXTRACTED_CHARS:
                    break
    return "\n\n".join(parts)[:MAX_EXTRACTED_CHARS]


async def extract_pdf_text(file_path: str) -> str:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_pdf_executor, _extract_pdf_sync, file_path),
            timeout=EXTRACTION_TIMEOUT,
        )
    except TimeoutError as exc:
        raise ExtractionError("EXTRACTION_TIMEOUT", "PDF extraction timed out") from exc


def _extract_web_sync(html_content: str) -> str:
    from trafilatura import extract

    text = extract(
        html_content, output_format="markdown", include_tables=True, include_links=True
    )
    if not text:
        raise ValueError("No content could be extracted from the page")
    return text[:MAX_EXTRACTED_CHARS]


async def extract_web_text(html_content: str) -> str:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_web_executor, _extract_web_sync, html_content),
            timeout=EXTRACTION_TIMEOUT,
        )
    except TimeoutError as exc:
        raise ExtractionError("EXTRACTION_TIMEOUT", "Web extraction timed out") from exc
