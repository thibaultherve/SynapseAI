"""
NeuroAI — Paper processing CLI.

Usage:
    python scripts/process_paper.py init
        → prints compact JSON with paper_id, source_type, text_path, tags

    python scripts/process_paper.py publish <page_id>
        → reads generated files, pushes to Notion, updates index/state/logs
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow importing notion_helper from same directory
sys.path.insert(0, str(Path(__file__).parent))
import notion_helper as nh

WORKSPACE = nh.WORKSPACE
DATA = nh.DATA_DIR

DEFAULT_STATE = {"queue": {"pending_papers": [], "papers_to_crossref": [], "verify_needed": False}, "comments": {"answered_ids": [], "posted_ids": []}}
DEFAULT_INDEX = {"papers": {}, "tags": {}}


def ensure_json(path: Path, default):
    """Create a JSON file with default content if it doesn't exist."""
    if not path.exists():
        path.write_text(json.dumps(default, indent=2, ensure_ascii=False), encoding="utf-8")


# ── INIT ────────────────────────────────────────────────────────────────────

def cmd_init():
    ensure_json(DATA / "state.json", DEFAULT_STATE)
    ensure_json(DATA / "index.json", DEFAULT_INDEX)
    state = json.loads((DATA / "state.json").read_text(encoding="utf-8"))
    queue = state["queue"]["pending_papers"]

    if not queue:
        print("HEARTBEAT_OK")
        return

    entry = queue[0]
    page_id = entry if isinstance(entry, str) else entry["page_id"]
    paper_dir = DATA / "papers" / page_id

    # Source info (now inside metadata.json under "source" key)
    metadata = json.loads((paper_dir / "metadata.json").read_text(encoding="utf-8"))
    source_info = metadata.get("source", {})

    # Tags registry (now inside index.json)
    index = json.loads((DATA / "index.json").read_text(encoding="utf-8"))
    tags = index.get("tags", {})

    # Compact output for LLM consumption
    source_type_fallback = entry.get("source_type", "unknown") if isinstance(entry, dict) else "unknown"
    out = {
        "page_id": page_id,
        "source_type": source_info.get("type", source_type_fallback),
        "text_path": str(paper_dir / "extracted_text.md"),
        "text_chars": source_info.get("extracted_chars", 0),
        "remaining_in_queue": len(queue) - 1,
        "tags": tags,
    }
    print(json.dumps(out, indent=2))


# ── PUBLISH ─────────────────────────────────────────────────────────────────

def cmd_publish(page_id: str):
    paper_dir = DATA / "papers" / page_id
    errors = []

    # ── Read generated files ────────────────────────────────────────────
    metadata = json.loads((paper_dir / "metadata.json").read_text(encoding="utf-8"))
    short_summary = (paper_dir / "short_summary.md").read_text(encoding="utf-8").strip()
    detailed_summary = (paper_dir / "detailed_summary.md").read_text(encoding="utf-8")
    key_findings = (paper_dir / "key_findings.md").read_text(encoding="utf-8")

    # Strip markdown H1 from short_summary if present
    lines = short_summary.splitlines()
    if lines and lines[0].startswith("# "):
        short_summary = "\n".join(lines[1:]).strip()

    title = metadata["title"]
    authors = ", ".join(metadata["authors"])
    authors_short = metadata.get("authors_short", authors)
    pub_date = metadata["date"]
    tags = metadata.get("tags", {})

    # ── 1. Update Notion page properties ────────────────────────────────
    try:
        nh.update_page_properties(
            page_id,
            title=title,
            authors=authors,
            pub_date=pub_date,
            tags_sub_domain=tags.get("sub_domain", []),
            tags_technique=tags.get("technique", []),
            tags_pathology=tags.get("pathology", []),
        )
    except Exception as e:
        errors.append(f"properties: {e}")

    # ── 2. Write content as toggle heading blocks ───────────────────────
    try:
        blocks = nh.build_page_blocks(short_summary, detailed_summary, key_findings)
        nh.append_blocks(page_id, blocks)
    except Exception as e:
        errors.append(f"blocks: {e}")

    # ── 3. Set status to Summarized ─────────────────────────────────────
    try:
        nh.set_status(page_id, "Summarized")
    except Exception as e:
        errors.append(f"status: {e}")

    # ── 4. Update index.json ────────────────────────────────────────────
    try:
        index_path = DATA / "index.json"
        ensure_json(index_path, DEFAULT_INDEX)
        index = json.loads(index_path.read_text(encoding="utf-8"))

        key_topics = metadata.get("keywords", metadata.get("key_topics", []))

        index["papers"][page_id] = {
            "title": title,
            "authors_short": authors_short,
            "date": pub_date,
            "tags": tags,
            "key_topics": key_topics,
            "dir": f"data/papers/{page_id}",
            "short_summary": short_summary[:600],
            "notion_url": f"https://www.notion.so/{page_id.replace('-', '')}",
        }
        index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        errors.append(f"index: {e}")

    # ── 5. Update state.json: remove from pending, add to crossref ──────
    try:
        state_path = DATA / "state.json"
        ensure_json(state_path, DEFAULT_STATE)
        state = json.loads(state_path.read_text(encoding="utf-8"))

        state["queue"]["pending_papers"] = [
            p for p in state["queue"]["pending_papers"]
            if (p if isinstance(p, str) else p["page_id"]) != page_id
        ]
        if page_id not in state["queue"]["papers_to_crossref"]:
            state["queue"]["papers_to_crossref"].append(page_id)

        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        errors.append(f"state: {e}")

    # ── 6. Log run ──────────────────────────────────────────────────────
    status = "success" if not errors else "partial"
    run_entry = {
        "skill": "neuroai-process",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "paper_id": page_id,
        "paper_title": title,
        "status": status,
        "actions": [
            "metadata",
            "short_summary",
            "detailed_summary",
            "key_findings",
            "notion_properties_updated",
            f"notion_blocks_added ({len(blocks)} blocks)",
            f"index_updated ({len(index['papers'])} papers total)",
            "crossref_queued",
        ],
        "errors": errors if errors else None,
    }

    # Local log
    try:
        logs_path = DATA / "logs.json"
        ensure_json(logs_path, [])
        runs = json.loads(logs_path.read_text(encoding="utf-8"))
        runs.append(run_entry)
        logs_path.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        errors.append(f"local_log: {e}")

    # Notion log
    try:
        notion_status = "Success" if not errors else "Partial"
        nh.log_run(
            run_title=f"neuroai-process: {title[:60]}",
            status=notion_status,
            pages_updated=f"{page_id} ({authors_short})",
            errors="; ".join(errors) if errors else "None",
        )
    except Exception as e:
        errors.append(f"notion_log: {e}")

    # ── Output ──────────────────────────────────────────────────────────
    pending = state["queue"]["pending_papers"]
    result = {
        "status": status,
        "page_id": page_id,
        "title": title,
        "blocks_written": len(blocks),
        "index_total": len(index["papers"]),
        "remaining_in_queue": len(pending),
        "errors": errors if errors else None,
    }
    print(json.dumps(result, indent=2))


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python process_paper.py init | publish <page_id>")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "init":
        cmd_init()
    elif cmd == "publish":
        if len(sys.argv) < 3:
            print("Error: publish requires a page_id")
            sys.exit(1)
        cmd_publish(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
