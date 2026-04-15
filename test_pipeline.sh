#!/usr/bin/env bash
# =============================================================================
# SynapseAI - test_pipeline.sh
# =============================================================================
# End-to-end pipeline test : upload d'un PDF et exercice d'un maximum
# d'endpoints de l'API.
#
# -----------------------------------------------------------------------------
# Ce qu'il se passe quand on ajoute un paper PDF (pipeline côté serveur) :
# -----------------------------------------------------------------------------
#
#   Client                     API                          Worker async
#   ──────                     ───                          ────────────
#   POST /api/papers/upload
#   (multipart PDF)    ───▶  validate_upload
#                              - magic bytes "%PDF"
#                              - size <= 100 MB (UPLOAD_MAX_SIZE)
#                            create_paper_from_pdf
#                              - écrit le fichier sur /data/uploads
#                              - INSERT paper (id, file_path, source_type=pdf)
#                              - INSERT paper_step × 6 (pending)
#                              - lance process_paper(paper_id) en tâche
#                                de fond (launch_processing) ───────────────┐
#                              - renvoie PaperResponse (201 Created)        │
#                                                                           ▼
#   Étapes de traitement (paper_step.status: pending → processing → done) :
#
#     1. uploading     (synchrone, marqué "done" avant le retour HTTP)
#     2. extracting    pdftotext / OCR → extracted_text, word_count
#     3. summarizing   Claude CLI      → title, authors, summaries,
#                                        keywords, journal, publication_date
#     4. tagging       Claude CLI      → tags (sub_domain, technique, ...)
#     5. embedding     sentence-xfmr   → paper_embedding rows (chunks × 768)
#     6. crossrefing   cosine + Claude → cross_reference (pairs avec
#                                        relation_type + strength)
#
#   À chaque changement de step, une ligne est écrite dans processing_event ;
#   GET /api/papers/:id/status streame ces events en SSE jusqu'à ce que le
#   statut dérivé devienne terminal (readable / enriched / error).
#
#   Statuts dérivés (compute_paper_status) :
#     pending     = tous en pending
#     processing  = au moins un en processing
#     readable    = summarizing + extracting done (utilisable pour lecture)
#     enriched    = tous done sauf crossrefing optionnel
#     error       = un step en error bloquant
#
# -----------------------------------------------------------------------------
# Ce que ce script teste :
# -----------------------------------------------------------------------------
#   /api/health
#   /api/papers  (GET, POST /upload, DELETE, PATCH, GET /:id, GET /:id/file,
#                 GET /:id/crossrefs)
#   /api/papers/:id/status     (SSE, bloquant jusqu'au terminal)
#   /api/papers/:id/steps
#   /api/search                (exact + semantic)
#   /api/search/similar/:id
#   /api/tags                  (GET, GET /:id/papers)
#   /api/graph                 (global + ETag + ego network)
#   /api/papers/:id/chat       (SSE, bref)
#   /api/papers/:id/chat/sessions
#   /api/chat/sessions/:id/messages
#   /api/insights              (GET list + filters, POST /refresh,
#                               GET/:id, PATCH /:id/rating, DELETE /:id)
#
# -----------------------------------------------------------------------------
# Usage :
#   ./test_pipeline.sh                      # full run
#   API=http://localhost:8000 ./test_pipeline.sh
#   PDF=other.pdf ./test_pipeline.sh
#   SKIP_CHAT=1 ./test_pipeline.sh          # skip chat SSE (coûteux)
#   SKIP_CLEAN=1 ./test_pipeline.sh         # ne pas purger les papers existants
#   SKIP_INSIGHTS=1 ./test_pipeline.sh      # skip la génération d'insights (Claude)
# =============================================================================

set -euo pipefail

API="${API:-http://localhost:8000}"
PDF="${PDF:-bastien-paper.pdf}"
SKIP_CHAT="${SKIP_CHAT:-0}"
SKIP_CLEAN="${SKIP_CLEAN:-0}"
SKIP_INSIGHTS="${SKIP_INSIGHTS:-0}"
SSE_TIMEOUT="${SSE_TIMEOUT:-600}"   # secondes max pour attendre la fin du pipeline

# ---- couleurs / helpers ----------------------------------------------------
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'
  YEL=$'\033[33m'; BLU=$'\033[34m'; CYA=$'\033[36m'; RST=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GRN=""; YEL=""; BLU=""; CYA=""; RST=""
fi

section() { echo ""; echo "${BOLD}${BLU}=== $* ===${RST}"; }
step()    { echo "${CYA}▶${RST} $*"; }
ok()      { echo "  ${GRN}✓${RST} $*"; }
warn()    { echo "  ${YEL}!${RST} $*"; }
fail()    { echo "  ${RED}✗${RST} $*"; }

pyjson() { python -c "import sys,json; d=json.load(sys.stdin); $1"; }
pretty() { python -m json.tool 2>/dev/null || cat; }

# Exécute curl, affiche le statut HTTP, renvoie le body.
# Usage: body=$(req GET /api/health [extra curl args...])
req() {
  local method=$1 path=$2; shift 2
  local tmp; tmp=$(mktemp)
  local code
  code=$(curl -s -o "$tmp" -w "%{http_code}" -X "$method" "$API$path" "$@")
  local body; body=$(cat "$tmp"); rm -f "$tmp"
  echo "  ${DIM}$method $path → $code${RST}" >&2
  if [[ "$code" =~ ^(2|3) ]]; then
    printf '%s' "$body"
    return 0
  fi
  fail "HTTP $code"
  echo "$body" | head -c 400 >&2; echo "" >&2
  printf '%s' "$body"
  return 1
}

# ---------------------------------------------------------------------------
section "0. Pré-requis"
# ---------------------------------------------------------------------------
[[ -f "$PDF" ]] || { fail "PDF introuvable : $PDF"; exit 1; }
command -v curl >/dev/null || { fail "curl manquant"; exit 1; }
command -v python >/dev/null || { fail "python manquant"; exit 1; }
ok "API=$API  PDF=$PDF"

# ---------------------------------------------------------------------------
section "1. Health check"
# ---------------------------------------------------------------------------
step "GET /api/health"
health=$(req GET /api/health)
echo "$health" | pretty

# ---------------------------------------------------------------------------
section "2. Nettoyage (suppression des papers existants)"
# ---------------------------------------------------------------------------
if [[ "$SKIP_CLEAN" == "1" ]]; then
  warn "SKIP_CLEAN=1, on ne purge pas"
else
  step "GET /api/papers?limit=100"
  ids=$(req GET "/api/papers?skip=0&limit=100" | pyjson "[print(p['id']) for p in d]" || true)
  count=0
  for id in $ids; do
    step "DELETE /api/papers/$id"
    req DELETE "/api/papers/$id" > /dev/null || true
    count=$((count + 1))
  done
  ok "$count paper(s) supprimé(s)"
fi

# ---------------------------------------------------------------------------
section "3. Upload du PDF"
# ---------------------------------------------------------------------------
step "POST /api/papers/upload ($PDF)"
upload=$(curl -s -w "\n%{http_code}" -X POST "$API/api/papers/upload" \
  -F "file=@$PDF" -F "source_type=pdf")
http_code=$(echo "$upload" | tail -n1)
body=$(echo "$upload" | sed '$d')
echo "  ${DIM}status=$http_code${RST}"
[[ "$http_code" == "201" ]] || { fail "upload a échoué"; echo "$body" | head -c 400; exit 1; }

paper_id=$(echo "$body" | pyjson "print(d['id'])")
ok "paper_id=$paper_id"
echo "$body" | pretty | head -20

# ---------------------------------------------------------------------------
section "4. SSE : attente de la fin du pipeline"
# ---------------------------------------------------------------------------
# Le serveur émet des events JSON à chaque transition de step + un event
# final { type: complete, status: ... } quand on atteint un statut terminal.
step "GET /api/papers/$paper_id/status (SSE, timeout=${SSE_TIMEOUT}s)"
echo "  ${DIM}Pipeline: uploading → extracting → summarizing → tagging → embedding → crossrefing${RST}"
echo ""

# On capture le stream, on l'affiche au fil de l'eau et on sort dès qu'on
# voit "type":"complete" ou timeout. L'option --max-time de curl coupera
# au besoin.
sse_log=$(mktemp)
set +e
curl -N -s --max-time "$SSE_TIMEOUT" "$API/api/papers/$paper_id/status" \
  | tee "$sse_log" \
  | while IFS= read -r line; do
      echo "$line"
      if [[ "$line" == *'"type": "complete"'* || "$line" == *'"type":"complete"'* ]]; then
        # kill parent curl: on laisse --max-time ou la fermeture serveur
        # faire le travail ; grep juste pour marqueur
        sleep 1
        pkill -P $$ curl 2>/dev/null || true
        break
      fi
    done
set -e

if grep -q '"type".*"complete"' "$sse_log"; then
  final_status=$(grep '"type".*"complete"' "$sse_log" | tail -1 \
    | python -c "import sys,json,re; l=sys.stdin.read(); m=re.search(r'data: (\{.*\})', l); print(json.loads(m.group(1)).get('status','?'))" 2>/dev/null || echo "?")
  ok "Pipeline terminé — statut=$final_status"
else
  warn "Pas d'event 'complete' reçu dans $SSE_TIMEOUT s — on continue quand même"
fi
rm -f "$sse_log"

# ---------------------------------------------------------------------------
section "5. Papers : détails + liste + steps + fichier"
# ---------------------------------------------------------------------------
step "GET /api/papers/$paper_id"
paper=$(req GET "/api/papers/$paper_id")
echo "$paper" | pyjson "
print('  title         :', d.get('title'))
print('  authors_short :', d.get('authors_short'))
print('  journal       :', d.get('journal'))
print('  publication   :', d.get('publication_date'))
print('  doi           :', d.get('doi'))
print('  tags          :', [t.get('name') for t in d.get('tags', [])])
print('  word_count    :', d.get('word_count'))
print('  summary.short :', (d.get('short_summary') or '')[:120].replace('\n',' '))
"

step "GET /api/papers?limit=10"
req GET "/api/papers?skip=0&limit=10" | pyjson "print(f'  {len(d)} paper(s) listé(s)')"

step "GET /api/papers/$paper_id/steps"
req GET "/api/papers/$paper_id/steps" | pyjson "
for s in d:
    print(f\"  {s['step']:12} {s['status']}\")
"

step "GET /api/papers/$paper_id/file (HEAD seulement)"
size=$(curl -sI "$API/api/papers/$paper_id/file" | awk '/[Cc]ontent-[Ll]ength/ {print $2}' | tr -d '\r' || echo "?")
ok "PDF téléchargeable — Content-Length=${size:-?}"

step "GET /api/papers/$paper_id/crossrefs"
req GET "/api/papers/$paper_id/crossrefs?limit=10" | pyjson "
print(f'  {len(d)} crossref(s)')
for x in d[:5]:
    rp = x.get('related_paper', {})
    print(f\"  - {x.get('relation_type'):14} {x.get('strength'):8} → {rp.get('title', '?')[:60]}\")
"

# ---------------------------------------------------------------------------
section "6. PATCH paper metadata"
# ---------------------------------------------------------------------------
step "PATCH /api/papers/$paper_id (journal override)"
req PATCH "/api/papers/$paper_id" \
  -H "Content-Type: application/json" \
  -d '{"journal":"Test Pipeline Journal"}' \
  | pyjson "print('  journal =', d.get('journal'))"

# ---------------------------------------------------------------------------
section "7. Tags"
# ---------------------------------------------------------------------------
step "GET /api/tags"
tags_json=$(req GET /api/tags)
echo "$tags_json" | pyjson "
for cat, lst in d.items():
    names = ', '.join(t['name'] for t in lst[:5])
    print(f'  {cat:12} ({len(lst):2}) : {names}')
"

first_tag_id=$(echo "$tags_json" | pyjson "
import itertools
all_tags = list(itertools.chain.from_iterable(d.values()))
print(all_tags[0]['id'] if all_tags else '')
")
if [[ -n "$first_tag_id" ]]; then
  step "GET /api/tags/$first_tag_id/papers"
  req GET "/api/tags/$first_tag_id/papers" | pyjson "print(f'  {len(d)} paper(s) avec ce tag')"
else
  warn "Aucun tag disponible (skip /tags/:id/papers)"
fi

# ---------------------------------------------------------------------------
section "8. Search — exact & semantic"
# ---------------------------------------------------------------------------
step "POST /api/search (mode=exact, q=the)"
req POST /api/search \
  -H "Content-Type: application/json" \
  -d '{"query":"the","mode":"exact","limit":5}' \
  | pyjson "
print(f\"  total_count={d['total_count']}  mode={d['mode']}\")
for r in d['results'][:3]:
    print(f\"  - score={r['relevance_score']:.3f}  {r.get('title','?')[:60]}\")
"

step "POST /api/search (mode=semantic, tolerance=3)"
req POST /api/search \
  -H "Content-Type: application/json" \
  -d '{"query":"neural network brain","mode":"semantic","tolerance":3,"limit":5}' \
  | pyjson "
print(f\"  total_count={d['total_count']}  mode={d['mode']}\")
for r in d['results'][:3]:
    print(f\"  - score={r['relevance_score']:.3f}  {r.get('title','?')[:60]}\")
"

step "GET /api/search/similar/$paper_id"
req GET "/api/search/similar/$paper_id" | pyjson "
print(f'  {len(d)} paper(s) similaires')
for r in d[:3]:
    print(f\"  - score={r['relevance_score']:.3f}  {r.get('title','?')[:60]}\")
"

# ---------------------------------------------------------------------------
section "9. Graph"
# ---------------------------------------------------------------------------
step "GET /api/graph (global)"
graph_hdrs=$(mktemp)
graph=$(curl -s -D "$graph_hdrs" "$API/api/graph")
etag=$(grep -i '^etag:' "$graph_hdrs" | awk '{print $2}' | tr -d '\r' || echo "")
rm -f "$graph_hdrs"
echo "$graph" | pyjson "print(f\"  nodes={d['node_count']}  edges={d['edge_count']}\")"
ok "ETag = $etag"

if [[ -n "$etag" ]]; then
  step "GET /api/graph (avec If-None-Match)"
  code=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "If-None-Match: $etag" "$API/api/graph")
  [[ "$code" == "304" ]] && ok "304 Not Modified" || warn "attendu 304, reçu $code"
fi

step "GET /api/graph/paper/$paper_id?depth=2"
req GET "/api/graph/paper/$paper_id?depth=2" | pyjson "
print(f\"  ego(depth=2): nodes={d['node_count']}  edges={d['edge_count']}\")
"

# ---------------------------------------------------------------------------
section "10. Chat (SSE : envoi d'un message + attente de la réponse)"
# ---------------------------------------------------------------------------
# Format SSE émis par le serveur :
#   event: chat\ndata: {"type":"session","session_id":N}\n\n
#   event: chat\ndata: {"type":"content","text":"..."}\n\n   (répété)
#   event: chat\ndata: {"type":"done"}\n\n
# curl -N ferme la connexion quand le serveur clôt le stream (après "done").
# --max-time sert de garde-fou si jamais Claude traîne.
if [[ "$SKIP_CHAT" == "1" ]]; then
  warn "SKIP_CHAT=1, on saute"
else
  CHAT_TIMEOUT="${CHAT_TIMEOUT:-120}"
  QUESTION="${QUESTION:-Résume ce papier en deux phrases.}"

  step "POST /api/papers/$paper_id/chat (question = « $QUESTION »)"
  echo "  ${DIM}timeout=${CHAT_TIMEOUT}s — stream jusqu'à l'event 'done'${RST}"

  chat_log=$(mktemp)
  body=$(python -c "import json,sys; print(json.dumps({'content': sys.argv[1]}))" "$QUESTION")

  set +e
  curl -N -s --max-time "$CHAT_TIMEOUT" \
    -X POST "$API/api/papers/$paper_id/chat" \
    -H "Content-Type: application/json" \
    -H "Accept: text/event-stream" \
    -d "$body" \
    > "$chat_log"
  set -e

  # Analyse du flux SSE : on comptabilise par type, on récupère session_id,
  # on reconstruit la réponse de l'assistant à partir des chunks "content".
  python - "$chat_log" <<'PYEOF'
import json, re, sys, textwrap
path = sys.argv[1]
content_lines = []
session_id = None
counts = {"session": 0, "content": 0, "done": 0, "error": 0, "other": 0}
errors = []
with open(path, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = obj.get("type", "other")
        if t == "session":
            session_id = obj.get("session_id")
            counts["session"] += 1
        elif t == "content":
            content_lines.append(obj.get("text", ""))
            counts["content"] += 1
        elif t == "done":
            counts["done"] += 1
        elif t == "error":
            counts["error"] += 1
            errors.append(obj.get("message") or obj.get("code") or raw)
        else:
            counts["other"] += 1

print(f"  session_id={session_id}  chunks={counts['content']}  done={counts['done']}  errors={counts['error']}")
if errors:
    for e in errors[:3]:
        print(f"  ERROR: {e}")

full = "".join(content_lines).strip()
if full:
    print("  ─── Réponse de l'assistant ──────────────────────────────")
    wrapped = textwrap.fill(full, width=78, initial_indent="  ", subsequent_indent="  ")
    print(wrapped)
    print("  ─────────────────────────────────────────────────────────")
else:
    print("  (aucun contenu reçu)")

# Expose session_id au shell via fichier
if session_id is not None:
    with open(path + ".session", "w") as g:
        g.write(str(session_id))
PYEOF

  first_session=""
  [[ -f "${chat_log}.session" ]] && first_session=$(cat "${chat_log}.session")
  rm -f "$chat_log" "${chat_log}.session"

  step "GET /api/papers/$paper_id/chat/sessions"
  sessions=$(req GET "/api/papers/$paper_id/chat/sessions")
  echo "$sessions" | pyjson "
for s in d:
    print(f\"  session id={s['id']}  scope={s['scope']}  msgs={s.get('message_count', 0)}\")
"
  [[ -z "$first_session" ]] && first_session=$(echo "$sessions" | pyjson "print(d[0]['id'] if d else '')")

  if [[ -n "$first_session" ]]; then
    step "GET /api/chat/sessions/$first_session/messages (historique persisté)"
    req GET "/api/chat/sessions/$first_session/messages?limit=10" | pyjson "
print(f'  {len(d)} message(s) en DB')
for m in d:
    c = m['content'].replace(chr(10),' ')
    c = c[:100] + ('…' if len(c) > 100 else '')
    print(f\"  [{m['role']:9}] {c}\")
"
  else
    warn "Pas de session_id remonté"
  fi
fi

# ---------------------------------------------------------------------------
section "11. Insights (corpus-level analytics)"
# ---------------------------------------------------------------------------
# Les insights sont générés à partir des cross-references récentes (fenêtre
# INSIGHT_LOOKBACK_HOURS = 24h). Avec un seul paper uploadé dans ce test il
# n'y a probablement aucune crossref, donc l'appel /refresh renverra
# status=skipped — c'est attendu. Le but est juste de vérifier que les
# endpoints répondent et que le contrat JSON tient la route.
if [[ "$SKIP_INSIGHTS" == "1" ]]; then
  warn "SKIP_INSIGHTS=1, on saute"
else
  step "POST /api/insights/refresh"
  # Rate-limit: 1/10min. En cas de 429 on continue, c'est juste un échauffement.
  refresh_tmp=$(mktemp)
  refresh_code=$(curl -s -o "$refresh_tmp" -w "%{http_code}" \
    -X POST "$API/api/insights/refresh")
  refresh_body=$(cat "$refresh_tmp"); rm -f "$refresh_tmp"
  echo "  ${DIM}POST /api/insights/refresh → $refresh_code${RST}"
  case "$refresh_code" in
    200)
      echo "$refresh_body" | pyjson "
print(f\"  status={d['status']}  new={d['insights_new']}  merged={d['insights_merged']}  skipped={d['skipped']}\")
print(f\"  hash={d['hash'][:16]}…\")
"
      ;;
    409)
      warn "409 — une génération est déjà en cours (debouncer lock)"
      ;;
    429)
      warn "429 — rate limit (1/10min) atteint, pas de refresh ce run"
      ;;
    *)
      fail "Code inattendu : $refresh_code"
      echo "$refresh_body" | head -c 200
      ;;
  esac

  step "GET /api/insights?limit=10"
  insights=$(req GET "/api/insights?limit=10")
  insight_count=$(echo "$insights" | pyjson "print(len(d))")
  echo "$insights" | pyjson "
for i in d[:5]:
    title = (i.get('title') or '')[:60]
    papers = len(i.get('supporting_papers', []))
    print(f\"  [{i['type']:13} {i['confidence']:6}] rating={i.get('rating')}  papers={papers}  {title}\")
if not d:
    print('  (aucun insight — attendu si pas de crossref récente)')
"

  step "GET /api/insights?type=trend&confidence=high&limit=5"
  req GET "/api/insights?type=trend&confidence=high&limit=5" \
    | pyjson "print(f'  {len(d)} insight(s) (type=trend, confidence=high)')"

  # Si on a au moins un insight on exerce le GET/:id + PATCH rating + DELETE
  if [[ "$insight_count" -gt 0 ]]; then
    first_insight_id=$(echo "$insights" | pyjson "print(d[0]['id'])")

    step "GET /api/insights/$first_insight_id"
    req GET "/api/insights/$first_insight_id" | pyjson "
print(f\"  id={d['id']}  type={d['type']}  confidence={d['confidence']}\")
print(f\"  title   : {(d.get('title') or '')[:80]}\")
print(f\"  content : {(d.get('content') or '')[:80]}\")
print(f\"  supporting_papers: {len(d.get('supporting_papers', []))}\")
"

    step "PATCH /api/insights/$first_insight_id/rating (rating=1)"
    req PATCH "/api/insights/$first_insight_id/rating" \
      -H "Content-Type: application/json" \
      -d '{"rating":1}' \
      | pyjson "print(f\"  rating now = {d.get('rating')}\")"

    step "PATCH /api/insights/$first_insight_id/rating (rating=null → clear)"
    req PATCH "/api/insights/$first_insight_id/rating" \
      -H "Content-Type: application/json" \
      -d '{"rating":null}' \
      | pyjson "print(f\"  rating now = {d.get('rating')}\")"

    step "DELETE /api/insights/$first_insight_id"
    del_code=$(curl -s -o /dev/null -w "%{http_code}" \
      -X DELETE "$API/api/insights/$first_insight_id")
    echo "  ${DIM}DELETE → $del_code${RST}"
    [[ "$del_code" == "204" ]] && ok "insight supprimé (204)" \
      || warn "attendu 204, reçu $del_code"
  else
    warn "Aucun insight persisté — skip GET/:id, PATCH, DELETE"
  fi
fi

# ---------------------------------------------------------------------------
section "12. Récap"
# ---------------------------------------------------------------------------
ok "paper_id testé : $paper_id"
ok "Tous les endpoints ciblés ont été sollicités."
echo ""
echo "${DIM}Astuce : ENV SKIP_CHAT=1 pour aller plus vite, SKIP_CLEAN=1 pour conserver les papers, SKIP_INSIGHTS=1 pour éviter Claude sur /refresh.${RST}"
