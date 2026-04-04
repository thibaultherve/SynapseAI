#!/usr/bin/env python3
"""
Poll all paper pages for new Notion comments in parallel.
Outputs JSON with only NEW (unanswered) comments.
Exit code 0 + empty "new_comments" = nothing to do.

Usage: python3 poll_comments.py
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Import notion_helper to load .env and get config
sys.path.insert(0, str(Path(__file__).parent))
import notion_helper as nh

DATA = nh.DATA_DIR
INDEX_FILE = DATA / "index.json"
STATE_FILE = DATA / "state.json"


def ensure_data_files():
    """Create data JSON files with empty defaults if they don't exist."""
    if not INDEX_FILE.exists():
        INDEX_FILE.write_text(json.dumps({"papers": {}, "tags": {}}, indent=2), encoding="utf-8")
    if not STATE_FILE.exists():
        STATE_FILE.write_text(json.dumps({"queue": {"pending_papers": [], "papers_to_crossref": [], "verify_needed": False}, "comments": {"answered_ids": [], "posted_ids": []}}, indent=2), encoding="utf-8")


def load_page_ids():
    """Extract just the page IDs from index.json (ignores all metadata)."""
    ensure_data_files()
    with open(INDEX_FILE) as f:
        data = json.load(f)
    return list(data.get("papers", {}).keys())


def load_skip_ids():
    """Load set of already-answered and bot-posted comment IDs from state.json."""
    if not STATE_FILE.exists():
        return set()
    with open(STATE_FILE) as f:
        state = json.load(f)
    comments = state.get("comments", {})
    answered = set(comments.get("answered_ids", []))
    posted = set(comments.get("posted_ids", []))
    return answered | posted


def get_bot_user_id():
    """Get the bot's Notion user ID from environment."""
    return nh.NOTION_BOT_USER_ID


def fetch_comments(page_id: str, api_key: str) -> dict:
    """Fetch comments for a single Notion page."""
    url = f"https://api.notion.com/v1/comments?block_id={page_id}"
    req = Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": nh.NOTION_VERSION,
    })
    try:
        with urlopen(req, timeout=15) as resp:
            return {"page_id": page_id, "data": json.loads(resp.read()), "error": None}
    except HTTPError as e:
        return {"page_id": page_id, "data": None, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"page_id": page_id, "data": None, "error": str(e)}


def main():
    api_key = nh.NOTION_API_KEY

    # Load page IDs and skip sets
    page_ids = load_page_ids()
    skip_ids = load_skip_ids()
    bot_user_id = get_bot_user_id()

    # Poll all pages in parallel
    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_comments, pid, api_key): pid for pid in page_ids}
        for future in as_completed(futures):
            result = future.result()
            if result["error"]:
                errors.append({"page_id": result["page_id"], "error": result["error"]})
                continue
            results.append(result)

    # Filter to new comments only (skip answered, posted, and bot's own comments)
    new_comments = []
    for result in results:
        comments = result["data"].get("results", [])
        for comment in comments:
            cid = comment.get("id")
            if not cid or cid in skip_ids:
                continue
            # Skip comments posted by the bot itself
            created_by_id = comment.get("created_by", {}).get("id")
            if bot_user_id and created_by_id == bot_user_id:
                continue
            new_comments.append({
                "comment_id": cid,
                "page_id": result["page_id"],
                "created_time": comment.get("created_time"),
                "rich_text": comment.get("rich_text", []),
                "created_by": comment.get("created_by", {}),
            })

    output = {
        "total_pages_polled": len(page_ids),
        "total_new_comments": len(new_comments),
        "new_comments": new_comments,
        "errors": errors,
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
