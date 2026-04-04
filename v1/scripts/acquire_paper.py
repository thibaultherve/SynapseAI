#!/usr/bin/env python3
"""
Download a paper's content (PDF or web page) and create source_info.json.

Usage:
    python3 acquire_paper.py <page_id> <url>

Detects content type from HTTP headers:
- application/pdf → downloads as original.pdf
- text/html → downloads HTML, extracts content with trafilatura → source_content.md

Creates:
- papers/{page_id}/metadata.json (with source info under "source" key)
- papers/{page_id}/original.pdf  OR  papers/{page_id}/extracted_text.md
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# Import notion_helper to load .env and get config
sys.path.insert(0, str(Path(__file__).parent))
import notion_helper as nh

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NeuroAI/1.0; research-bot)",
    "Accept": "application/pdf, text/html, */*",
}


def download_pdf(url: str, paper_dir: Path) -> dict:
    """Download a PDF file and extract text with pdfplumber."""
    import pdfplumber

    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=120) as resp:
        data = resp.read()
    
    # Save original PDF
    pdf_file = paper_dir / "original.pdf"
    pdf_file.write_bytes(data)
    
    # Extract text → extracted_text.md
    extracted_parts = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                extracted_parts.append(text)
    
    extracted_text = "\n\n".join(extracted_parts)
    output_file = paper_dir / "extracted_text.md"
    output_file.write_text(extracted_text, encoding="utf-8")
    
    return {
        "type": "pdf",
        "file": "original.pdf",
        "extracted_file": "extracted_text.md",
        "size_bytes": len(data),
        "extracted_chars": len(extracted_text),
    }


def download_and_extract_web(url: str, paper_dir: Path) -> dict:
    """Download HTML page and extract content as markdown using trafilatura."""
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=60) as resp:
        raw_html = resp.read()
        content_type = resp.headers.get("Content-Type", "")
        final_url = resp.url
    
    # Decode HTML
    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=")[-1].strip().rstrip(";").strip('"')
    try:
        html_text = raw_html.decode(charset)
    except (UnicodeDecodeError, LookupError):
        html_text = raw_html.decode("utf-8", errors="replace")
    
    # Extract with trafilatura
    import trafilatura
    markdown_content = trafilatura.extract(
        html_text,
        output_format="markdown",
        include_tables=True,
        include_links=True,
        include_images=False,
    )
    
    if not markdown_content:
        # Fallback: try plain text
        markdown_content = trafilatura.extract(
            html_text,
            output_format="txt",
            include_tables=True,
        )
    
    if not markdown_content:
        return {
            "type": "error",
            "error": "trafilatura could not extract content from HTML",
            "final_url": final_url,
        }
    
    # Save extracted content — named extracted_text.md so process skill has a uniform input
    output_file = paper_dir / "extracted_text.md"
    output_file.write_text(markdown_content, encoding="utf-8")
    
    return {
        "type": "web",
        "file": "extracted_text.md",
        "size_bytes": len(markdown_content.encode("utf-8")),
        "final_url": final_url,
        "extracted_chars": len(markdown_content),
    }


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: acquire_paper.py <page_id> <url>"}))
        sys.exit(1)
    
    page_id = sys.argv[1]
    url = sys.argv[2]
    
    paper_dir = nh.DATA_DIR / "papers" / page_id
    paper_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Probe content type with HEAD request first
        head_req = Request(url, method="HEAD", headers=HEADERS)
        content_type = ""
        content_disposition = ""
        final_url = url
        try:
            with urlopen(head_req, timeout=15) as resp:
                content_type = resp.headers.get("Content-Type", "").lower()
                content_disposition = resp.headers.get("Content-Disposition", "").lower()
                final_url = resp.url
        except Exception:
            # HEAD failed, guess from URL
            content_type = "application/pdf" if url.lower().endswith(".pdf") else ""

        is_pdf = (
            "pdf" in content_type
            or url.lower().endswith(".pdf")
            or final_url.lower().endswith(".pdf")
            or ".pdf" in content_disposition
            or ("octet-stream" in content_type and "/download/" in url.lower())
        )

        if is_pdf:
            result = download_pdf(url, paper_dir)
        else:
            result = download_and_extract_web(url, paper_dir)
            # If web extraction failed, try as PDF — some servers misreport content type
            if result.get("type") == "error" and "octet-stream" in content_type:
                result = download_pdf(url, paper_dir)
    
    except HTTPError as e:
        result = {"type": "error", "error": f"HTTP {e.code}: {e.reason}"}
    except URLError as e:
        result = {"type": "error", "error": f"URL Error: {e.reason}"}
    except Exception as e:
        result = {"type": "error", "error": str(e)}
    
    # Add metadata
    result["url"] = url
    result["page_id"] = page_id
    result["downloaded_at"] = datetime.now(timezone.utc).isoformat()
    
    # Update Notion status based on result
    if result.get("type") != "error" and (paper_dir / "extracted_text.md").exists():
        try:
            nh.set_status(page_id, "Downloaded")
        except Exception as e:
            result["status_update_error"] = str(e)
    elif result.get("type") == "error":
        try:
            nh.set_status(page_id, "Error")
        except Exception:
            pass

    # Save source info inside metadata.json
    meta_path = paper_dir / "metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        meta = {}
    meta["source"] = result
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
