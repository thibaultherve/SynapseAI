"""
NeuroAI — Notion API helper.
Handles all Notion interactions so the LLM never touches curl/JSON.
"""

import json
import os
import re
import textwrap
from pathlib import Path
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# ── Config ──────────────────────────────────────────────────────────────────

WORKSPACE = Path(os.environ.get("NEUROAI_WORKSPACE", Path(__file__).resolve().parent.parent))
DATA_DIR = WORKSPACE / "data"

load_dotenv(WORKSPACE / ".env")

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
NOTION_LOGS_DB_ID = os.environ["NOTION_LOGS_DB_ID"]
NOTION_BOT_USER_ID = os.environ["NOTION_BOT_USER_ID"]
NOTION_SYNTHESIS_PAGE_ID = os.environ.get("NOTION_SYNTHESIS_PAGE_ID", "")
NOTION_GAP_ANALYSIS_PAGE_ID = os.environ.get("NOTION_GAP_ANALYSIS_PAGE_ID", "")
NOTION_CONCORDANCE_PAGE_ID = os.environ.get("NOTION_CONCORDANCE_PAGE_ID", "")
NOTION_VERSION = "2022-06-28"

NOTION_BLOCK_CHAR_LIMIT = 2000


def _api_key() -> str:
    return NOTION_API_KEY


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _post(url: str, body: dict) -> dict:
    r = requests.post(url, headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _patch(url: str, body: dict) -> dict:
    r = requests.patch(url, headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


# ── Page properties ─────────────────────────────────────────────────────────

def update_page_properties(
    page_id: str,
    title: str,
    authors: str,
    pub_date: str,
    tags_sub_domain: list[str],
    tags_technique: list[str],
    tags_pathology: list[str],
) -> dict:
    """Update the paper page's editable properties."""
    props = {
        "Title": {"title": [{"text": {"content": title[:2000]}}]},
        "Authors": {"rich_text": [{"text": {"content": authors[:2000]}}]},
        "Publication Date": {"date": {"start": pub_date}},
        "Sub-domain": {"multi_select": [{"name": t} for t in tags_sub_domain]},
        "Technique": {"multi_select": [{"name": t} for t in tags_technique]},
        "Pathology": {"multi_select": [{"name": t} for t in tags_pathology]},
    }
    return _patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        {"properties": props},
    )


def set_status(page_id: str, status: str = "Summarized") -> dict:
    return _patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        {"properties": {"Status": {"status": {"name": status}}}},
    )


# ── Markdown → Notion blocks ───────────────────────────────────────────────

def _rich_text(text: str, annotations: dict | None = None) -> list[dict]:
    """Split text into ≤2000-char rich_text segments."""
    chunks = textwrap.wrap(text, width=NOTION_BLOCK_CHAR_LIMIT, break_long_words=False, break_on_hyphens=False)
    if not chunks:
        chunks = [text]  # preserve empty-ish strings
    result = []
    for c in chunks:
        item = {"type": "text", "text": {"content": c}}
        if annotations:
            item["annotations"] = annotations
        result.append(item)
    return result


def _paragraph(text: str, annotations: dict | None = None) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich_text(text, annotations)}}


def _heading(level: int, text: str) -> dict:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": _rich_text(text)}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rich_text(text)}}


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _md_to_child_blocks(md: str) -> list[dict]:
    """
    Convert a simple markdown string into Notion child blocks.
    Handles: ## heading2, ### heading3, - bullets, numbered bold items, paragraphs.
    Skips H1 lines.
    """
    blocks: list[dict] = []
    lines = md.strip().splitlines()
    paragraph_buf: list[str] = []

    def flush_paragraph():
        if paragraph_buf:
            text = " ".join(paragraph_buf).strip()
            if text:
                blocks.append(_paragraph(text))
            paragraph_buf.clear()

    for line in lines:
        stripped = line.strip()

        # Skip H1 titles
        if stripped.startswith("# ") and not stripped.startswith("## "):
            flush_paragraph()
            continue

        if stripped.startswith("### "):
            flush_paragraph()
            blocks.append(_heading(3, stripped[4:].strip()))
        elif stripped.startswith("## "):
            flush_paragraph()
            blocks.append(_heading(2, stripped[3:].strip()))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            flush_paragraph()
            bullet_text = stripped[2:].strip()
            m = re.match(r"^\*\*(.+?)\*\*\s*(.*)", bullet_text, re.DOTALL)
            if m:
                bullet_text = m.group(1) + " " + m.group(2) if m.group(2) else m.group(1)
            blocks.append(_bullet(bullet_text))
        elif re.match(r"^\d+\.\s+\*\*", stripped):
            flush_paragraph()
            bullet_text = re.sub(r"^\d+\.\s+", "", stripped)
            m = re.match(r"^\*\*(.+?)\*\*\s*(.*)", bullet_text, re.DOTALL)
            if m:
                bullet_text = m.group(1) + " " + m.group(2) if m.group(2) else m.group(1)
            blocks.append(_bullet(bullet_text))
        elif stripped == "":
            flush_paragraph()
        else:
            paragraph_buf.append(stripped)

    flush_paragraph()
    return blocks


def _toggle_heading_3(title: str, children: list[dict]) -> dict:
    """Create a toggleable heading_3 with nested children."""
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {
            "rich_text": _rich_text(title),
            "is_toggleable": True,
            "children": children,
        },
    }


def build_page_blocks(
    short_summary: str,
    detailed_summary: str,
    key_findings: str,
) -> list[dict]:
    """
    Build the full list of Notion blocks for a paper page.
    Structure: divider → 3 toggle heading_3 sections → footer.
    """
    blocks = [_divider()]

    # Short Summary toggle
    short_children = [_paragraph(short_summary)]
    blocks.append(_toggle_heading_3("Short Summary", short_children))

    # Detailed Summary toggle
    detail_children = _md_to_child_blocks(detailed_summary)
    blocks.append(_toggle_heading_3("Detailed Summary", detail_children))

    # Key Findings toggle
    findings_children = _md_to_child_blocks(key_findings)
    blocks.append(_toggle_heading_3("Key Findings", findings_children))

    # Footer
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    blocks.append(_paragraph(f"Processed by NeuroAI — {now}", {"italic": True, "color": "gray"}))
    return blocks


def append_blocks(page_id: str, blocks: list[dict]) -> dict:
    """Append blocks to a Notion page. Handles batching (max 100 per call)."""
    results = []
    for i in range(0, len(blocks), 100):
        batch = blocks[i : i + 100]
        r = _patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            {"children": batch},
        )
        results.append(r)
    return results[-1] if results else {}


# ── Comments ──────────────────────────────────────────────────────────────────

def post_comment(page_id: str, rich_text: list[dict]) -> dict:
    """Post a comment on a Notion page."""
    return _post(
        "https://api.notion.com/v1/comments",
        {"parent": {"page_id": page_id}, "rich_text": rich_text},
    )


def post_text_comment(page_id: str, text: str, mention_user_id: str | None = None) -> dict:
    """Post a simple text comment, optionally mentioning a user first."""
    rt = []
    if mention_user_id:
        rt.append({"type": "mention", "mention": {"user": {"id": mention_user_id}}})
        rt.append({"type": "text", "text": {"content": " "}})
    for chunk in textwrap.wrap(text, width=NOTION_BLOCK_CHAR_LIMIT, break_long_words=False, break_on_hyphens=False) or [text]:
        rt.append({"type": "text", "text": {"content": chunk}})
    return post_comment(page_id, rt)


# ── Read page blocks ─────────────────────────────────────────────────────────

def get_blocks(block_id: str) -> list[dict]:
    """Get all child blocks of a block/page."""
    blocks = []
    url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
    while url:
        r = requests.get(url, headers=_headers(), timeout=30)
        r.raise_for_status()
        data = r.json()
        blocks.extend(data.get("results", []))
        url = data.get("next_cursor")
        if url:
            url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100&start_cursor={url}"
        else:
            break
    return blocks


# ── Logging ─────────────────────────────────────────────────────────────────

def log_run(
    run_title: str,
    status: str = "Success",
    pages_updated: str = "",
    errors: str = "None",
) -> dict:
    """Create a log entry in the NeuroAI Logs Notion database."""
    props = {
        "Run": {"title": [{"text": {"content": run_title[:100]}}]},
        "Status": {"status": {"name": status}},
        "Timestamp": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
        "Pages Updated": {"rich_text": [{"text": {"content": pages_updated[:2000]}}]},
        "New Papers": {"number": 0},
        "Errors": {"rich_text": [{"text": {"content": errors[:2000]}}]},
    }
    return _post(
        "https://api.notion.com/v1/pages",
        {"parent": {"database_id": NOTION_LOGS_DB_ID}, "properties": props},
    )
