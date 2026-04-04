#!/usr/bin/env python3
"""
Query Notion database for papers with Status = "New".
Outputs JSON with new paper IDs, titles, and URLs.
"""

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Import notion_helper to load .env and get config
sys.path.insert(0, str(Path(__file__).parent))
import notion_helper as nh


def main():
    api_key = nh.NOTION_API_KEY
    database_id = nh.NOTION_DATABASE_ID

    # Query for Status = "New"
    query_body = json.dumps({
        "filter": {
            "property": "Status",
            "status": {"equals": "New"}
        }
    }).encode()

    req = Request(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        data=query_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": nh.NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        print(json.dumps({"error": f"HTTP {e.code}: {e.reason}"}))
        sys.exit(1)

    # Extract paper info
    new_papers = []
    for page in data.get("results", []):
        page_id = page["id"]
        props = page.get("properties", {})

        # Title
        title_parts = props.get("Title", {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_parts) if title_parts else "Untitled"

        # URL
        paper_url = props.get("URL", {}).get("url")

        # Authors
        authors_parts = props.get("Authors", {}).get("rich_text", [])
        authors = "".join(a.get("plain_text", "") for a in authors_parts) if authors_parts else ""

        # Publication Date
        pub_date = props.get("Publication Date", {}).get("date", {})
        date_str = pub_date.get("start") if pub_date else None

        new_papers.append({
            "page_id": page_id,
            "title": title,
            "url": paper_url,
            "authors": authors,
            "date": date_str,
        })

    output = {
        "total_new": len(new_papers),
        "papers": new_papers,
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
