#!/usr/bin/env python3
"""
NeuroAI — Verify & Sync.
Compares the Notion Research Papers database with local files
and removes orphaned local data (papers no longer in Notion).
"""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parent))
import notion_helper as nh

DATA_DIR = nh.DATA_DIR


# ── Notion fetch ─────────────────────────────────────────────────────────────

def fetch_all_notion_papers() -> dict[str, dict]:
    """Query the full Notion database with pagination. Returns {id: {title, status, url}}."""
    papers = {}
    cursor = None

    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        req = Request(
            f"https://api.notion.com/v1/databases/{nh.NOTION_DATABASE_ID}/query",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {nh.NOTION_API_KEY}",
                "Notion-Version": nh.NOTION_VERSION,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except HTTPError as e:
            print(f"Error querying Notion: HTTP {e.code} {e.reason}", file=sys.stderr)
            sys.exit(1)

        for page in data.get("results", []):
            page_id = page["id"]
            props = page.get("properties", {})

            title_parts = props.get("Title", {}).get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts) if title_parts else "Untitled"

            status_obj = props.get("Status", {}).get("status")
            status = status_obj.get("name", "") if status_obj else ""

            url = page.get("url", "")

            papers[page_id] = {"title": title, "status": status, "url": url}

        if data.get("has_more") and data.get("next_cursor"):
            cursor = data["next_cursor"]
        else:
            break

    return papers


# ── Local state loading ──────────────────────────────────────────────────────

def load_json(path: Path) -> dict | list:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_index() -> dict[str, dict]:
    data = load_json(DATA_DIR / "index.json")
    return data.get("papers", {})



def list_paper_dirs() -> set[str]:
    papers_dir = DATA_DIR / "papers"
    if not papers_dir.exists():
        return set()
    return {d.name for d in papers_dir.iterdir() if d.is_dir()}


# ── Cleanup ──────────────────────────────────────────────────────────────────

def cleanup_orphaned(notion_ids: set[str], index: dict,
                     local_dirs: set[str]) -> dict:
    """Remove local data for papers no longer in Notion. Returns summary."""
    orphaned = set(index.keys()) - notion_ids
    orphaned_dirs = local_dirs - notion_ids

    if not orphaned and not orphaned_dirs:
        return {"removed": [], "status": "clean"}

    removed = []

    # Remove from index.json
    index_path = DATA_DIR / "index.json"
    if index_path.exists():
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
        papers = index_data.get("papers", {})
        for pid in orphaned:
            if pid in papers:
                removed.append({"id": pid, "title": papers[pid].get("title", "?"), "cleaned": []})
                del papers[pid]
                removed[-1]["cleaned"].append("index.json")
        index_data["papers"] = papers
        index_path.write_text(json.dumps(index_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Remove from state.json queues
    state_path = DATA_DIR / "state.json"
    if state_path.exists():
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        queue = state_data.get("queue", {})
        changed = False
        for key in ("pending_papers", "papers_to_crossref"):
            lst = queue.get(key, [])
            new_lst = [pid for pid in lst if pid not in orphaned]
            if len(new_lst) != len(lst):
                queue[key] = new_lst
                changed = True
                for r in removed:
                    if r["id"] in set(lst) - set(new_lst):
                        r["cleaned"].append(f"state.json/{key}")
        if changed:
            state_data["queue"] = queue
            state_path.write_text(json.dumps(state_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Remove from analysis.json references
    analysis_path = DATA_DIR / "analysis.json"
    if analysis_path.exists():
        analysis_data = json.loads(analysis_path.read_text(encoding="utf-8"))
        changed = False
        for gap in analysis_data.get("gaps", []):
            sp = gap.get("supporting_papers", [])
            new_sp = [pid for pid in sp if pid not in orphaned]
            if len(new_sp) != len(sp):
                gap["supporting_papers"] = new_sp
                changed = True
        for conc in analysis_data.get("concordances", []):
            ps = conc.get("papers", [])
            new_ps = [pid for pid in ps if pid not in orphaned]
            if len(new_ps) != len(ps):
                conc["papers"] = new_ps
                changed = True
        if changed:
            analysis_path.write_text(json.dumps(analysis_data, indent=2, ensure_ascii=False), encoding="utf-8")
            for r in removed:
                r["cleaned"].append("analysis.json")

    # Remove paper directories
    all_orphaned_dirs = orphaned | orphaned_dirs
    for pid in sorted(all_orphaned_dirs):
        paper_dir = DATA_DIR / "papers" / pid
        if paper_dir.exists():
            shutil.rmtree(paper_dir)
            # Find or create entry in removed list
            entry = next((r for r in removed if r["id"] == pid), None)
            if entry:
                entry["cleaned"].append("papers/dir")
            else:
                removed.append({"id": pid, "title": "?", "cleaned": ["papers/dir"]})

    return {"removed": removed, "status": "cleaned"}


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Fetching all papers from Notion...", file=sys.stderr)
    notion = fetch_all_notion_papers()
    notion_ids = set(notion.keys())
    print(f"  → {len(notion)} papers in Notion", file=sys.stderr)

    index = load_index()
    local_dirs = list_paper_dirs()
    local_count_before = len(index)

    print(f"  → {local_count_before} papers in index.json", file=sys.stderr)
    print(f"  → {len(local_dirs)} local paper directories", file=sys.stderr)

    # Clean up orphaned data
    result = cleanup_orphaned(notion_ids, index, local_dirs)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "notion_count": len(notion),
        "local_count_before": local_count_before,
        "removed_count": len(result["removed"]),
        "cleanup": result,
    }

    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
