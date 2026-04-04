#!/usr/bin/env python3
"""
NeuroAI — Notion page operations CLI.

Usage:
    python scripts/notion_pages.py env
        → prints all Notion page IDs as JSON (for skills to consume)

    python scripts/notion_pages.py comment <page_id> <text> [--mention <user_id>]
        → posts a comment on a Notion page

    python scripts/notion_pages.py append <page_id> <markdown_file>
        → reads a markdown file and appends it as blocks to a Notion page

    python scripts/notion_pages.py blocks <page_id>
        → gets all blocks from a Notion page as JSON
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import notion_helper as nh


def cmd_env():
    """Print all Notion page IDs as JSON."""
    print(json.dumps({
        "NOTION_DATABASE_ID": nh.NOTION_DATABASE_ID,
        "NOTION_LOGS_DB_ID": nh.NOTION_LOGS_DB_ID,
        "NOTION_BOT_USER_ID": nh.NOTION_BOT_USER_ID,
        "NOTION_SYNTHESIS_PAGE_ID": nh.NOTION_SYNTHESIS_PAGE_ID,
        "NOTION_GAP_ANALYSIS_PAGE_ID": nh.NOTION_GAP_ANALYSIS_PAGE_ID,
        "NOTION_CONCORDANCE_PAGE_ID": nh.NOTION_CONCORDANCE_PAGE_ID,
    }, indent=2))


def cmd_comment(args):
    """Post a comment on a page."""
    if len(args) < 2:
        print("Error: comment requires <page_id> <text>")
        sys.exit(1)

    page_id = args[0]
    text = args[1]
    mention = None
    if "--mention" in args:
        idx = args.index("--mention")
        if idx + 1 < len(args):
            mention = args[idx + 1]

    result = nh.post_text_comment(page_id, text, mention_user_id=mention)
    print(json.dumps({"status": "ok", "comment_id": result.get("id")}))


def cmd_append(args):
    """Append markdown content as blocks to a page."""
    if len(args) < 2:
        print("Error: append requires <page_id> <markdown_file>")
        sys.exit(1)

    page_id = args[0]
    md_path = Path(args[1])
    md_content = md_path.read_text(encoding="utf-8")

    blocks = nh._md_to_child_blocks(md_content)
    if blocks:
        nh.append_blocks(page_id, blocks)
    print(json.dumps({"status": "ok", "blocks_written": len(blocks)}))


def cmd_blocks(args):
    """Get blocks from a page."""
    if len(args) < 1:
        print("Error: blocks requires <page_id>")
        sys.exit(1)

    blocks = nh.get_blocks(args[0])
    print(json.dumps(blocks, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python notion_pages.py env|comment|append|blocks [args...]")
        sys.exit(1)

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "env":
        cmd_env()
    elif cmd == "comment":
        cmd_comment(rest)
    elif cmd == "append":
        cmd_append(rest)
    elif cmd == "blocks":
        cmd_blocks(rest)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
