#!/usr/bin/env bash
# Quick script: delete all papers, upload a PDF, stream SSE status
set -euo pipefail

API="http://localhost:8000"

echo "=== Deleting all papers ==="
ids=$(curl -s "$API/api/papers?skip=0&limit=100" | python -c "
import sys, json
for p in json.load(sys.stdin):
    print(p['id'])
" 2>/dev/null || true)

for id in $ids; do
  echo "  DELETE $id"
  curl -s -X DELETE "$API/api/papers/$id" > /dev/null
done
echo "  Done."

echo ""
echo "=== Uploading bastien-paper.pdf ==="
response=$(curl -s -X POST "$API/api/papers/upload" \
  -F "file=@bastien-paper.pdf" \
  -F "source_type=pdf")

paper_id=$(echo "$response" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  Paper ID: $paper_id"

echo ""
echo "=== Streaming SSE status ==="
curl -N -s "$API/api/papers/$paper_id/status"
