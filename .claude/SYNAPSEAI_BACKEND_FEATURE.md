# SynapseAI Backend - Spec Technique Complete

> **Projet** : SynapseAI — Plateforme de recherche scientifique augmentee par IA
> **Scope** : Backend (FastAPI + PostgreSQL + Claude CLI + Processing Pipeline)
> **Auteur** : Thibault Herve
> **Date** : 2026-04-04
> **Status** : v4 — Post bulletproof FastAPI review (Phase 1 implementee)
> **Changelog** : v3 extrait le frontend dans `SYNAPSEAI_FRONTEND_FEATURE.md`
> **Frontend** : Voir `.claude/SYNAPSEAI_FRONTEND_FEATURE.md` pour la spec frontend complete (Bulletproof React)

---

## 1. Overview

### Objectif

SynapseAI est une plateforme web qui permet a un labo de recherche de :

1. **Uploader** des papers scientifiques (PDF ou URL/DOI)
2. **Analyser automatiquement** chaque paper (resumes court/long, key findings, tags)
3. **Chatter** avec un paper via un panel lateral contextualise (+ toggle corpus entier)
4. **Visualiser** le graphe de connaissances (cross-references entre papers)
5. **Generer de l'intelligence** : l'accumulation de papers amene l'IA a proposer des theories, hypotheses, et directions de recherche

### Resume technique (Backend)

| Composant | Technologie |
|-----------|-------------|
| Backend | FastAPI (Python 3.12+) |
| ORM | SQLAlchemy 2.0 (async, mapped_column) |
| Migrations | Alembic |
| Schemas | Pydantic v2 |
| Database | PostgreSQL 16 + pgvector |
| IA (LLM) | Claude CLI (plan Max, subprocess async) |
| Embeddings | sentence-transformers (local, all-MiniLM-L6-v2) — upgrades possibles : nomic-embed-text, BGE-M3 |
| Infra | Docker Compose (dev) / Hetzner VPS (prod) |
| Tests | pytest-asyncio + httpx AsyncClient |
| Rate Limiting | slowapi (in-memory) |

> **Stack frontend :** voir `.claude/SYNAPSEAI_FRONTEND_FEATURE.md`

### Architecture Backend

```
React SPA (voir SYNAPSEAI_FRONTEND_FEATURE.md)
    | REST + SSE
FastAPI (Python) <-- CE FICHIER
    +-- PostgreSQL + pgvector
    +-- Claude CLI (asyncio.create_subprocess_exec, Max plan)
    +-- sentence-transformers (embeddings locaux, run_in_threadpool)
    +-- Fichiers (PDFs uploades)
```

---

## 2. Contexte et Motivation

### Systeme actuel (NeuroAI v1)

Le systeme v1 a ete deplace dans le dossier `v1/` a la racine du workspace. Il utilise :
- **Notion** comme UI et source de verite pour les papers
- **Claude Code skills** (.md) pour orchestrer le processing
- **Fichiers JSON plats** (index.json, state.json, analysis.json) comme base de donnees locale
- **Scripts Python** pour interagir avec l'API Notion

### Problemes identifies

1. **Notion comme bottleneck** : API limitee a 3 req/s, pas de full-text search, pas de stockage d'embeddings, pas de custom UI
2. **JSON plats** : pas de transactions ACID, pas de requetes, drift entre Notion et local (d'ou verify_sync)
3. **Single-user** : les skills tournent sur une seule machine, un seul utilisateur
4. **Pas de semantic search** : le crossref compare les tags manuellement, pas les embeddings
5. **Pas de vraie intelligence** : le systeme identifie des gaps mais ne propose pas de theories

### Ce que SynapseAI resout

- **PostgreSQL** remplace Notion + JSON avec integrite referentielle, FTS, et vector search
- **React** remplace Notion comme UI avec des features impossibles dans Notion (graph, chat, PDF viewer)
- **Multi-utilisateur** natif
- **Embeddings** pour le semantic search et le RAG
- **Research Intelligence** : l'IA accumule les connaissances et genere des hypotheses

---

## 3. Specifications Fonctionnelles

### 3.1 Upload de Paper

**Modes d'upload :**

1. **Drag & drop PDF** : l'utilisateur glisse un fichier PDF dans la zone d'upload
2. **Coller une URL** : l'utilisateur colle une URL de page web ou un lien direct vers un PDF
3. **Coller un DOI** : l'utilisateur colle un DOI (ex: `10.1038/s41586-024-xxxxx`), le systeme resout l'URL automatiquement

**Flow apres upload :**

```
Upload/URL/DOI
    -> [downloading] Telechargement du contenu
    -> [extracting]  Extraction du texte (pdfplumber pour PDF, trafilatura pour web)
    -> [summarizing]  Claude CLI genere short_summary + detailed_summary + key_findings
    -> [tagging]     Claude CLI assigne/cree des tags (depuis la taxonomie existante en DB)
    -> [embedding]   sentence-transformers genere les embeddings par chunks
    -> [crossrefing] Claude CLI compare avec les papers similaires (via embeddings)
    -> [done]        Paper disponible dans la liste
```

**Status en temps reel :** chaque etape envoie un evenement SSE au frontend. Le frontend affiche une barre de progression avec le nom de l'etape courante.

**Gestion d'erreurs :**
- PDF protege/paywall -> status "error", message affiche, lien original preserve
- URL inaccessible -> status "error", retry possible
- DOI non resolu -> status "error", possibilite d'uploader manuellement
- Extraction vide -> status "error", possibilite de coller le texte manuellement

**Metadonnees auto-extraites par Claude :**
- Titre, auteurs, date de publication, journal, DOI
- Tags (sub_domain, technique, pathology, topic)
- Keywords

**Validation upload :**
- Taille max : 100MB
- Types acceptes : PDF (application/pdf), HTML
- Verification MIME type reel (pas juste l'extension)
- Validation via dependency `validate_upload` reutilisable

### 3.2 Viewer de Paper

**Layout :** page dediee par paper.

**Contenu principal (panneau gauche, ~60%) :**
- Si PDF source -> viewer PDF integre (react-pdf) avec navigation par pages
- Si web source -> contenu markdown rendu (extracted_text)
- Lien vers l'URL originale toujours visible en haut
- Metadonnees : titre, auteurs, date, journal, DOI, tags (cliquables pour filtrer)

**Resumes (panneau droit ou onglets) :**
- Short summary (4-10 phrases)
- Detailed summary (800-1200 mots, sections collapsibles)
- Key findings (3-7 items numerotes avec donnees quantitatives)

**Chat (panneau droit, toggle) :**
- Voir section 3.3

### 3.3 Chat par Paper

**UX :** panel lateral a droite du viewer. Toggle entre resumes et chat.

**Fonctionnement :**

1. L'utilisateur tape une question
2. FastAPI recupere le contexte :
   - **Mode "paper"** (defaut) : resumes + key findings + chunks pertinents du paper + cross-refs directes
   - **Mode "corpus"** : semantic search sur tous les papers (pgvector), top-K chunks les plus pertinents
3. FastAPI appelle Claude CLI avec le contexte en prompt (via `asyncio.create_subprocess_exec`)
4. La reponse est streamee en SSE vers le frontend
5. La conversation est sauvegardee en DB (chat_sessions + chat_messages)

**Toggle scope :** un switch visible "Ce paper" / "Tout le corpus" en haut du chat.

**Comportement attendu :**
- Claude repond en expert, avec des citations precises (numero de paper, section)
- En mode corpus, Claude peut croiser les infos de plusieurs papers
- L'historique de conversation persiste (on peut reprendre une conversation)
- Le chat utilise le plan Max via Claude CLI (zero cout supplementaire)

### 3.4 Liste des Papers

**Vue principale :** tableau/grille de tous les papers.

**Colonnes/infos affichees :**
- Titre (tronque si long)
- Auteurs (format court)
- Date de publication
- Tags (badges colores par categorie)
- Status (uploading, processing, done, error)
- Date d'ajout

**Filtres :**
- Par tag (multi-select par categorie)
- Par status
- Par date (range picker)
- Full-text search (barre de recherche -> FTS PostgreSQL)
- Semantic search (barre de recherche -> pgvector, toggle "recherche intelligente")

**Tri :** par date d'ajout, date de publication, titre, nombre de cross-refs.

### 3.5 Semantic Search

**Input :** une barre de recherche avec un toggle "exact" / "intelligent".

- **Exact** : PostgreSQL FTS (tsvector). Cherche les mots exacts dans titre + texte.
- **Intelligent** : sentence-transformers encode la query -> pgvector cosine similarity sur les embeddings des chunks. Retourne les papers les plus semantiquement proches.

**Output :** liste de papers tries par pertinence, avec le passage le plus pertinent mis en surbrillance pour chaque resultat.

### 3.6 Tags (Taxonomie IA-managed)

**Principe :** les tags sont geres par l'IA (Claude), pas par les utilisateurs directement.

**Tables :**
- `tags` : id, name, category, description
- `paper_tags` : paper_id, tag_id

**Workflow de tagging :**

1. Quand Claude traite un paper, il recoit la **liste complete des tags existants** depuis la DB
2. Claude choisit les tags pertinents parmi les existants OU propose un nouveau tag
3. Si nouveau tag -> INSERT dans `tags` + liaison dans `paper_tags`
4. Si tag existant -> liaison dans `paper_tags` uniquement
5. Pas de doublons possibles (contrainte UNIQUE sur name+category)

**Rename/merge de tags :**
- Claude peut proposer un rename quand il detecte une incoherence
- UPDATE sur `tags.name` -> tous les papers avec ce tag sont automatiquement mis a jour (FK)
- Merge : changer les `paper_tags` du tag doublon vers le tag cible, puis DELETE le doublon

**Categories de tags :**
- `sub_domain` : Molecular, Cellular, Systems, Clinical, Computational, etc.
- `technique` : scRNA-seq, ATAC-seq, MERFISH, MRI, etc.
- `pathology` : MS, Alzheimer's, Healthy, Aging, etc.
- `topic` : tags libres thematiques (oligodendrocyte, epigenetics, dopamine, etc.)

### 3.7 Cross-References & Graphe

**Cross-referencement automatique :**

Quand un paper est processe et embede :
1. pgvector trouve les K papers les plus proches (embeddings)
2. **Gate cosinus** : seules les paires avec similarite cosinus > 0.7 sont envoyees a Claude (evite le scaling quadratique)
3. Claude CLI analyse les paires filtrees et qualifie la relation :
   - Type : concordance, contradiction, extends, methodology_complement
   - Force : strong, moderate, weak
   - Description textuelle de la connexion
4. Les cross-refs sont stockees dans la table `cross_references`

**Graphe interactif :**

- **Library** : Cytoscape.js (concu pour les reseaux biologiques, adapte a la recherche)
- **Noeuds** : papers (taille = nombre de connexions, couleur = sub_domain principal)
- **Aretes** : cross-references (epaisseur = force, couleur = type, tooltip = description)
- **Interactions** :
  - Zoom/pan
  - Clic sur noeud -> ouvre le paper
  - Hover -> affiche titre + resume court
  - Layout automatique (force-directed)
- **Filtres** :
  - Par tag (montrer/cacher des groupes)
  - Par type de relation
  - Par force minimum
  - Par date (slider temporel)

### 3.8 Research Intelligence

C'est le **killer feature**. L'accumulation de papers amene l'IA a generer de l'intelligence emergente.

**Types d'insights :**

| Type | Description | Declencheur |
|------|-------------|-------------|
| `gap` | Trou identifie dans la litterature | >=3 papers sur un sujet sans reponse a une question cle |
| `concordance` | Convergence entre etudes independantes | >=2 papers arrivent a la meme conclusion par des methodes differentes |
| `contradiction` | Resultats contradictoires | >=2 papers avec des conclusions incompatibles |
| `hypothesis` | Hypothese generee par l'IA | Synthese de patterns detectes dans le corpus -> prediction testable |
| `theory` | Modele theorique | Agregation de plusieurs hypotheses -> framework coherent |
| `direction` | Direction de recherche suggeree | Combinaison de gaps + techniques disponibles -> projet faisable |

**Workflow :**

Apres chaque cross-referencement :
1. Claude recoit le corpus complet d'insights existants + les nouvelles cross-refs
2. Claude evalue si de nouveaux insights emergent
3. Chaque insight est stocke avec : type, titre, contenu, evidence, confidence, supporting_papers (via junction table `insight_papers`), rating (thumbs up/down)
4. Le dashboard affiche les insights groupes par type

**Regles de generation :**
- `gap` : seulement si >=3 papers soutiennent le constat (conservatif)
- `hypothesis` : doit etre formulee comme une prediction testable avec des experiences concretes
- `theory` : seulement si >=2 hypotheses convergent + >=5 papers de soutien
- `direction` : doit inclure des methodes specifiques et un plan d'experience esquisse
- Confidence : `high` (evidence directe forte), `moderate` (inference raisonnable), `speculative` (extrapolation creative)
- Les insights precedemment valides (rating = 1) sont passes en contexte aux futurs prompts pour calibrer le niveau de profondeur attendu

**Dashboard Research Intelligence :**
- Vue par type d'insight
- Pour chaque insight : titre, resume, papers de soutien (cliquables), confiance
- Filtrable par confiance, par tags des papers associes
- Timeline : quand l'insight a emerge

### 3.9 Processing Pipeline (Claude CLI Integration)

**Comment FastAPI communique avec Claude CLI (async) :**

```python
import asyncio
import json

async def call_claude(prompt: str, timeout: int = 120) -> str:
    """Appelle Claude CLI en mode non-interactif via le plan Max (async)."""
    process = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt, "--output-format", "json", "--max-turns", "1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        process.communicate(), timeout=timeout
    )
    if process.returncode != 0:
        raise ClaudeError(stderr.decode())
    return json.loads(stdout.decode())


async def stream_claude(prompt: str):
    """Appelle Claude CLI en streaming (pour le chat, async)."""
    process = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt, "--output-format", "stream-json",
        stdout=asyncio.subprocess.PIPE,
    )
    async for line in process.stdout:
        yield line.decode()
```

**Prompts par tache :**

| Tache | Input contexte (depuis DB) | Output attendu |
|-------|---------------------------|----------------|
| Resumes | extracted_text | JSON: {title, authors, date, short_summary, detailed_summary, key_findings, keywords} |
| Tagging | extracted_text + short_summary + liste des tags existants | JSON: {tags: [{id_existant}, {new: name, category}]} |
| Cross-ref | paper A summary + paper B summary + key_findings des deux | JSON: {relation_type, strength, description} |
| Insights | tous les insights existants + nouvelles cross-refs + summaries des papers impliques | JSON: [{type, title, content, evidence, confidence, supporting_papers}] |
| Chat | question + contexte (resumes, chunks pertinents, cross-refs) | Texte libre (streame) |

**Gestion des limites du plan Max :**
- Les taches batch (resumes, cross-ref, insights) sont queuees et executees sequentiellement
- Le chat a la priorite (interactif)
- Si rate limit atteint -> queue les requetes, frontend affiche "SynapseAI reflechit..."
- Pas de parallelisme sur les appels Claude CLI (une seule instance a la fois pour respecter les limites)

---

## 4. Architecture Technique

### 4.1 Structure du Monorepo (organisation par domaine)

```
synapseai/
+-- api/                              # Backend FastAPI
|   +-- app/
|   |   +-- __init__.py
|   |   +-- main.py                   # FastAPI app, lifespan, CORS, 3 exception handlers, include routers
|   |   +-- config.py                 # Settings splits: DatabaseConfig, UploadConfig, AppConfig
|   |   |
|   |   +-- core/                     # Infrastructure partagee
|   |   |   +-- __init__.py
|   |   |   +-- database.py           # AsyncEngine + async_sessionmaker + get_db (commit/rollback)
|   |   |   +-- base.py               # DeclarativeBase avec naming convention
|   |   |   +-- schemas.py            # AppBaseModel (custom BaseModel partagee), ErrorResponse
|   |   |   +-- exceptions.py         # Base exceptions (NotFoundError, ConflictError, etc.)
|   |   |   +-- enums.py              # StrEnum pour champs contraints (PaperStatus, SourceType, etc.)
|   |   |   +-- dependencies.py       # Dependencies partagees (rate limiter, etc.)
|   |   |
|   |   +-- papers/                   # Domaine Papers
|   |   |   +-- __init__.py
|   |   |   +-- router.py             # APIRouter prefix=/api/papers
|   |   |   +-- schemas.py            # PaperCreate, PaperResponse, PaperList, PaperUpdate
|   |   |   +-- models.py             # Paper, PaperTag (association)
|   |   |   +-- service.py            # CRUD papers, pipeline orchestration
|   |   |   +-- dependencies.py       # get_paper_or_404, validate_upload
|   |   |   +-- exceptions.py         # PaperNotFoundError, InvalidDOIError, ExtractionError
|   |   |
|   |   +-- tags/                     # Domaine Tags
|   |   |   +-- __init__.py
|   |   |   +-- router.py             # APIRouter prefix=/api/tags
|   |   |   +-- schemas.py            # TagResponse, TagMergeRequest
|   |   |   +-- models.py             # Tag
|   |   |   +-- service.py            # CRUD tags, merge, rename
|   |   |   +-- dependencies.py       # get_tag_or_404
|   |   |   +-- exceptions.py         # TagNotFoundError, DuplicateTagError
|   |   |
|   |   +-- chat/                     # Domaine Chat
|   |   |   +-- __init__.py
|   |   |   +-- router.py             # APIRouter prefix=/api (chat routes)
|   |   |   +-- schemas.py            # ChatMessageCreate, ChatMessageResponse, SessionResponse
|   |   |   +-- models.py             # ChatSession, ChatMessage
|   |   |   +-- service.py            # Chat + RAG, context retrieval
|   |   |   +-- dependencies.py       # get_session_or_404
|   |   |   +-- exceptions.py         # SessionNotFoundError
|   |   |
|   |   +-- search/                   # Domaine Search
|   |   |   +-- __init__.py
|   |   |   +-- router.py             # APIRouter prefix=/api/search
|   |   |   +-- schemas.py            # SearchRequest, SearchResult
|   |   |   +-- service.py            # FTS + semantic search
|   |   |   +-- exceptions.py         # SearchError
|   |   |
|   |   +-- insights/                 # Domaine Research Intelligence
|   |   |   +-- __init__.py
|   |   |   +-- router.py             # APIRouter prefix=/api/insights
|   |   |   +-- schemas.py            # InsightResponse, InsightList
|   |   |   +-- models.py             # Insight, InsightPaper (junction)
|   |   |   +-- service.py            # Insight generation, rating
|   |   |   +-- dependencies.py       # get_insight_or_404
|   |   |   +-- exceptions.py         # InsightNotFoundError
|   |   |
|   |   +-- graph/                    # Domaine Knowledge Graph
|   |   |   +-- __init__.py
|   |   |   +-- router.py             # APIRouter prefix=/api/graph
|   |   |   +-- schemas.py            # GraphData, NodeResponse, EdgeResponse
|   |   |   +-- service.py            # Graph data aggregation
|   |   |
|   |   +-- processing/              # Domaine Processing Pipeline
|   |   |   +-- __init__.py
|   |   |   +-- router.py             # APIRouter prefix=/api (SSE status route)
|   |   |   +-- schemas.py            # ProcessingEventResponse
|   |   |   +-- models.py             # ProcessingEvent, PaperEmbedding, CrossReference
|   |   |   +-- service.py            # Pipeline orchestration
|   |   |   +-- claude_service.py     # Claude CLI async wrapper (call_claude, stream_claude)
|   |   |   +-- embedding_service.py  # sentence-transformers (run_in_threadpool)
|   |   |   +-- crossref_service.py   # Cross-referencement
|   |   |   +-- exceptions.py         # ClaudeError, EmbeddingError, CrossRefError
|   |   |
|   |   +-- utils/                    # Utilitaires partages
|   |       +-- __init__.py
|   |       +-- text_extraction.py    # pdfplumber + trafilatura
|   |       +-- chunking.py           # Text splitting pour embeddings
|   |       +-- doi_resolver.py       # DOI -> URL resolution
|   |
|   +-- alembic/                      # DB migrations
|   |   +-- env.py                    # Async migration setup
|   |   +-- script.py.mako
|   |   +-- versions/
|   |       +-- 001_initial_schema.py
|   +-- alembic.ini
|   +-- requirements.txt
|   +-- Dockerfile
|   +-- tests/                        # Tests (miroir de la structure source)
|       +-- conftest.py               # Fixtures: async client, test DB, session
|       +-- core/
|       |   +-- test_health.py
|       |   +-- test_database.py
|       +-- papers/
|       |   +-- test_router.py
|       |   +-- test_service.py
|       +-- tags/
|       |   +-- test_router.py
|       +-- chat/
|       |   +-- test_router.py
|       +-- search/
|       |   +-- test_router.py
|       +-- processing/
|       |   +-- test_claude_service.py
|       |   +-- test_embedding_service.py
|       +-- insights/
|       |   +-- test_router.py
|       +-- graph/
|           +-- test_router.py
|
+-- web/                              # Frontend React (voir SYNAPSEAI_FRONTEND_FEATURE.md)
|
+-- skills/                           # Claude Code skills (adaptees)
|   +-- process-paper/
|   |   +-- SKILL.md
|   +-- crossref/
|   |   +-- SKILL.md
|   +-- insights/
|   |   +-- SKILL.md
|   +-- chat/
|       +-- SKILL.md
|
+-- design-system.pen
+-- design.pen
+-- docker-compose.yml
+-- docker-compose.prod.yml
+-- .env.example
+-- .gitignore
+-- README.md
```

### 4.2 Endpoints API

Chaque endpoint specifie `response_model`, `status_code`, et `responses` (erreurs documentees).

```
# Papers
POST   /api/papers              # Upload (multipart PDF ou JSON {url/doi})
                                # response_model=PaperResponse, status_code=201
                                # responses={400: InvalidUpload, 413: TooLarge}
GET    /api/papers              # Liste (filtres: tags, status, date, q)
                                # response_model=PaperList, status_code=200
GET    /api/papers/:id          # Detail complet
                                # response_model=PaperResponse, status_code=200
                                # responses={404: PaperNotFound}
DELETE /api/papers/:id          # Suppression (CASCADE tout)
                                # status_code=204
                                # responses={404: PaperNotFound}
GET    /api/papers/:id/file     # Telecharger le PDF/fichier original
                                # FileResponse, status_code=200
                                # responses={404: PaperNotFound}
PATCH  /api/papers/:id          # Update metadonnees manuelles
                                # response_model=PaperResponse, status_code=200
                                # responses={404: PaperNotFound}

# Processing Status (SSE)
GET    /api/papers/:id/status   # Stream SSE des etapes de processing
                                # StreamingResponse (text/event-stream)

# Tags
GET    /api/tags                # Liste tous les tags (groupes par categorie)
                                # response_model=list[TagResponse], status_code=200
GET    /api/tags/:id/papers     # Papers avec ce tag
                                # response_model=PaperList, status_code=200
                                # responses={404: TagNotFound}
PATCH  /api/tags/:id            # Rename un tag
                                # response_model=TagResponse, status_code=200
                                # responses={404: TagNotFound}
POST   /api/tags/merge          # Merge deux tags
                                # response_model=TagResponse, status_code=200
DELETE /api/tags/:id            # Supprimer un tag
                                # status_code=204

# Search
POST   /api/search              # {query, mode: "exact"|"semantic", filters}
                                # response_model=SearchResult, status_code=200
GET    /api/search/similar/:id  # Papers similaires a un paper donne
                                # response_model=PaperList, status_code=200

# Chat (rate limited)
POST   /api/papers/:id/chat             # Envoyer un message (SSE response)
                                        # StreamingResponse, responses={404: PaperNotFound}
GET    /api/papers/:id/chat/sessions     # Historique des sessions
                                        # response_model=list[SessionResponse]
GET    /api/chat/sessions/:id/messages   # Messages d'une session
                                        # response_model=list[ChatMessageResponse]

# Graph
GET    /api/graph                # Noeuds + aretes pour Cytoscape
                                # response_model=GraphData, status_code=200
GET    /api/graph/paper/:id      # Sous-graphe autour d'un paper
                                # response_model=GraphData, status_code=200

# Cross-References
GET    /api/papers/:id/crossrefs # Cross-refs d'un paper
                                # response_model=list[CrossRefResponse]

# Research Intelligence
GET    /api/insights             # Tous les insights (filtrable par type, confidence)
                                # response_model=InsightList, status_code=200
GET    /api/insights/:id         # Detail d'un insight
                                # response_model=InsightResponse, status_code=200
                                # responses={404: InsightNotFound}
PATCH  /api/insights/:id/rate    # Rating thumbs up/down
                                # response_model=InsightResponse, status_code=200

# Health
GET    /api/health               # Health check
                                # status_code=200
```

### 4.3 Docker Compose

```yaml
# docker-compose.yml (developpement)
services:
  db:
    image: pgvector/pgvector:pg16
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: synapseai
      POSTGRES_USER: synapseai
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U synapseai"]
      interval: 5s
      retries: 5

  db-test:
    image: pgvector/pgvector:pg16
    ports:
      - "5434:5432"
    environment:
      POSTGRES_DB: synapseai_test
      POSTGRES_USER: synapseai
      POSTGRES_PASSWORD: synapseai_test
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U synapseai"]
      interval: 5s
      retries: 5

  api:
    build: ./api
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://synapseai:${DB_PASSWORD}@db:5432/synapseai
      UPLOAD_DIR: /data/uploads
      ENV: development
    volumes:
      - ./api:/app
      - uploads:/data/uploads
    depends_on:
      db:
        condition: service_healthy
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  web:
    build: ./web
    ports:
      - "5173:5173"
    volumes:
      - ./web:/app
      - /app/node_modules
    command: npm run dev -- --host 0.0.0.0

volumes:
  pgdata:
  uploads:
```

### 4.4 Error Handling Strategy

**Format de reponse erreur uniforme :**

```json
{
  "error": {
    "code": "PAPER_NOT_FOUND",
    "message": "Paper with id 550e8400-... not found"
  }
}
```

**Hierarchie d'exceptions :**

```python
# core/exceptions.py — base exceptions
class AppError(Exception):
    """Base pour toutes les exceptions domaine."""
    def __init__(self, code: str, message: str, status_code: int = 500):
        self.code = code
        self.message = message
        self.status_code = status_code

class NotFoundError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=404)

class ConflictError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=409)

class ValidationError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=422)

# papers/exceptions.py — domain exceptions
class PaperNotFoundError(NotFoundError):
    def __init__(self, paper_id: UUID):
        super().__init__("PAPER_NOT_FOUND", f"Paper {paper_id} not found")

class InvalidDOIError(ValidationError):
    def __init__(self, doi: str):
        super().__init__("INVALID_DOI", f"Invalid DOI format: {doi}")

class ExtractionError(AppError):
    def __init__(self, detail: str):
        super().__init__("EXTRACTION_FAILED", detail, status_code=422)
```

**Global exception handler dans main.py :**

```python
from app.core.exceptions import AppError
from app.core.schemas import ErrorResponse

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )
```

### 4.5 Dependencies Pattern

```python
# papers/dependencies.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.papers.models import Paper
from app.papers.exceptions import PaperNotFoundError

async def get_paper_or_404(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Paper:
    paper = await db.get(Paper, paper_id)
    if not paper:
        raise PaperNotFoundError(paper_id)
    return paper

# Usage dans router.py :
@router.get("/{paper_id}", response_model=PaperResponse)
async def get_paper(paper: Paper = Depends(get_paper_or_404)):
    return paper
```

### 4.6 API Documentation en Production

```python
# main.py
from app.config import settings

app = FastAPI(
    title="SynapseAI",
    openapi_url="/api/openapi.json" if settings.ENV != "production" else None,
    lifespan=lifespan,
)
```

En production, `openapi_url=None` desactive `/docs`, `/redoc`, et `/openapi.json`.

### 4.7 Rate Limiting

```python
# core/dependencies.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Usage dans router.py (endpoints sensibles) :
@router.post("/", response_model=PaperResponse, status_code=201)
@limiter.limit("10/minute")
async def create_paper(request: Request, ...):
    ...

@router.post("/{paper_id}/chat")
@limiter.limit("20/minute")
async def chat(request: Request, ...):
    ...
```

---

## 5. Database

### 5.1 Schema complet

> **IMPORTANT** : Tous les noms de tables sont au **singulier** dans l'implementation (ex: `paper` et non `papers`, `tag` et non `tags`).
> Les valeurs d'enum implementees different du SQL ci-dessous sur certains champs :
> - `cross_reference.relation_type` : `'supports', 'contradicts', 'extends', 'methodological', 'thematic'`
> - `insight.type` : `'trend', 'gap', 'hypothesis', 'methodology', 'contradiction', 'opportunity'`
> - `insight.confidence` : `'high', 'medium', 'low'`
> - `paper.status` : pas de `'downloading'`, mais inclut `'deleted'`
>
> Les enums definitifs sont dans `core/enums.py`. En cas de doute, le code fait autorite.

```sql
-- Extension vectorielle
CREATE EXTENSION IF NOT EXISTS vector;

-- Tags normalises (geres par l'IA)
CREATE TABLE tag (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('sub_domain', 'technique', 'pathology', 'topic')),
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (name, category)
);

-- Papers
CREATE TABLE papers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT,
    authors TEXT[],
    authors_short TEXT,
    publication_date DATE,
    journal TEXT,
    doi TEXT UNIQUE,
    url TEXT,
    source_type TEXT CHECK (source_type IN ('pdf', 'web')),
    status TEXT DEFAULT 'uploading' CHECK (status IN (
        'uploading', 'downloading', 'extracting', 'summarizing',
        'tagging', 'embedding', 'crossrefing', 'done', 'error'
    )),
    error_message TEXT,
    -- Contenu
    extracted_text TEXT,
    short_summary TEXT,
    detailed_summary TEXT,
    key_findings TEXT,
    keywords TEXT[],
    word_count INT,
    file_path TEXT,
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    processed_at TIMESTAMPTZ,
    -- Full-text search
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(title, '') || ' ' ||
            coalesce(short_summary, '') || ' ' ||
            coalesce(extracted_text, '')
        )
    ) STORED
);

-- Junction paper <-> tag
CREATE TABLE paper_tags (
    paper_id UUID REFERENCES papers(id) ON DELETE CASCADE,
    tag_id INT REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (paper_id, tag_id)
);

-- Embeddings (chunks pour RAG)
CREATE TABLE paper_embeddings (
    id SERIAL PRIMARY KEY,
    paper_id UUID REFERENCES papers(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(384),
    UNIQUE (paper_id, chunk_index)
);

-- Cross-references
CREATE TABLE cross_references (
    id SERIAL PRIMARY KEY,
    paper_a UUID REFERENCES papers(id) ON DELETE CASCADE,
    paper_b UUID REFERENCES papers(id) ON DELETE CASCADE,
    relation_type TEXT CHECK (relation_type IN (
        'concordance', 'contradiction', 'extends',
        'methodology_complement', 'similar_topic'
    )),
    strength TEXT CHECK (strength IN ('strong', 'moderate', 'weak')),
    description TEXT,
    detected_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (paper_a, paper_b)
);

-- Research Intelligence
CREATE TABLE insights (
    id SERIAL PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN (
        'gap', 'concordance', 'contradiction',
        'hypothesis', 'theory', 'direction'
    )),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    evidence TEXT,
    confidence TEXT CHECK (confidence IN ('high', 'moderate', 'speculative')),
    rating SMALLINT CHECK (rating IN (1, -1)),
    detected_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Junction insight <-> paper (integrite referentielle)
CREATE TABLE insight_papers (
    insight_id INT REFERENCES insights(id) ON DELETE CASCADE,
    paper_id UUID REFERENCES papers(id) ON DELETE CASCADE,
    PRIMARY KEY (insight_id, paper_id)
);

-- Chat
CREATE TABLE chat_sessions (
    id SERIAL PRIMARY KEY,
    paper_id UUID REFERENCES papers(id) ON DELETE CASCADE,
    scope TEXT DEFAULT 'paper' CHECK (scope IN ('paper', 'corpus')),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE chat_messages (
    id SERIAL PRIMARY KEY,
    session_id INT REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Processing events (pour SSE)
CREATE TABLE processing_events (
    id SERIAL PRIMARY KEY,
    paper_id UUID REFERENCES papers(id) ON DELETE CASCADE,
    step TEXT NOT NULL,
    detail TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Index
CREATE INDEX idx_papers_fts ON papers USING GIN (search_vector);
CREATE INDEX idx_papers_status ON papers (status);
CREATE INDEX idx_papers_created ON papers (created_at DESC);
CREATE INDEX idx_embeddings_vec ON paper_embeddings
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_crossref_a ON cross_references (paper_a);
CREATE INDEX idx_crossref_b ON cross_references (paper_b);
CREATE INDEX idx_insights_type ON insights (type);
CREATE INDEX idx_insight_papers_paper ON insight_papers (paper_id);
CREATE INDEX idx_chat_paper ON chat_sessions (paper_id);
CREATE INDEX idx_processing_paper ON processing_events (paper_id, created_at DESC);
CREATE INDEX idx_tags_category ON tags (category);
```

**Changements vs v1 :**
- `papers.updated_at` ajoute (mis a jour automatiquement a chaque modification)
- `insights.supporting_papers UUID[]` remplace par junction table `insight_papers`
- Index `idx_insight_papers_paper` ajoute sur la junction table
- Total : **10 tables** (9 + insight_papers)

### 5.2 Migrations

Outil : **Alembic** (standard avec SQLAlchemy).

Migration initiale = tout le schema ci-dessus. Les migrations suivantes ajouteront : auth tables, notifications, etc.

---

## 6. Backend Implementation

### 6.1 Conventions

**Pydantic :**
- Tous les schemas heritent de `AppBaseModel` (custom BaseModel avec config commune)
- Validators Field sur tous les schemas d'entree (min_length, max_length, pattern, etc.)
- Schemas separes : Create, Update, Response
- `ConfigDict(from_attributes=True)` sur tous les schemas de reponse

```python
# core/schemas.py
from pydantic import BaseModel, ConfigDict

class AppBaseModel(BaseModel):
    """Base model partagee pour tous les schemas."""
    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
    )

class ErrorResponse(AppBaseModel):
    error: ErrorDetail

class ErrorDetail(AppBaseModel):
    code: str
    message: str
```

```python
# papers/schemas.py
from pydantic import Field, HttpUrl, model_validator
from app.core.schemas import AppBaseModel

class PaperCreate(AppBaseModel):
    url: HttpUrl | None = None
    doi: str | None = Field(None, pattern=r"^10\.\d{4,}/\S+$")

    @model_validator(mode="after")
    def at_least_one_source(self):
        if not self.url and not self.doi:
            raise ValueError("url or doi is required")
        return self

class PaperResponse(AppBaseModel):
    id: uuid.UUID
    title: str | None
    authors: list[str] | None
    authors_short: str | None
    publication_date: date | None
    journal: str | None
    doi: str | None
    url: str | None
    source_type: str | None
    status: str
    short_summary: str | None
    key_findings: str | None
    keywords: list[str] | None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None

class PaperList(AppBaseModel):
    items: list[PaperResponse]
    total: int
```

**Dependencies :** chaque domaine definit ses `get_X_or_404` dans `dependencies.py`.

**Exceptions :** chaque domaine definit ses exceptions dans `exceptions.py`, heritant de `core/exceptions.py`.

**Router :** chaque route specifie `response_model`, `status_code`, et `responses` pour les erreurs.

### Phase 1 : Setup Projet (Fondations)

| # | Tache | Fichier(s) |
|---|-------|------------|
| 1 | Docker Compose : services `db` + `db-test` + `api`, volumes | `docker-compose.yml` |
| 2 | Dockerfile API : Python 3.12, pip install, uvicorn | `api/Dockerfile` |
| 3 | `.env.example` avec DATABASE_URL, UPLOAD_DIR, DB_PASSWORD, ENV | `.env.example` |
| 4 | `.gitignore` pour synapseai | `.gitignore` |
| 5 | `requirements.txt` | `api/requirements.txt` |
| 6 | `config.py` : Settings split (DatabaseConfig, UploadConfig, AppConfig) | `api/app/config.py` |
| 7 | `core/base.py` : DeclarativeBase avec naming convention | `api/app/core/base.py` |
| 8 | `core/database.py` : async engine + session + get_db (commit/rollback) | `api/app/core/database.py` |
| 9 | `core/schemas.py` : AppBaseModel + ErrorResponse | `api/app/core/schemas.py` |
| 10 | `core/exceptions.py` : AppError, NotFoundError, ConflictError | `api/app/core/exceptions.py` |
| 10b | `core/enums.py` : StrEnum pour champs contraints | `api/app/core/enums.py` |
| 11 | `main.py` : FastAPI app, lifespan, CORS explicite, 3 exception handlers, health | `api/app/main.py` |
| 12 | Health route : `GET /api/health` | `api/app/main.py` (inline) |

**Tests Phase 1 :**
| # | Tache | Fichier |
|---|-------|---------|
| 1 | `conftest.py` : fixtures async client + test DB session + cleanup | `api/tests/conftest.py` |
| 2 | Test health endpoint | `api/tests/core/test_health.py` |
| 3 | Test database connection | `api/tests/core/test_database.py` |

### Phase 2 : Models & Migration

| # | Tache | Fichier(s) |
|---|-------|------------|
| 1 | `papers/models.py` : Paper (tous champs, search_vector Computed, updated_at) | `api/app/papers/models.py` |
| 2 | `papers/models.py` : PaperTag (association table) | `api/app/papers/models.py` |
| 3 | `tags/models.py` : Tag | `api/app/tags/models.py` |
| 4 | `processing/models.py` : PaperEmbedding (Vector(384)), CrossReference, ProcessingEvent | `api/app/processing/models.py` |
| 5 | `insights/models.py` : Insight + InsightPaper (junction table) | `api/app/insights/models.py` |
| 6 | `chat/models.py` : ChatSession, ChatMessage | `api/app/chat/models.py` |
| 7 | `alembic.ini` + `alembic/env.py` (async setup) | `api/alembic/` |
| 8 | Migration initiale : extension vector + 10 tables + 11 index | `api/alembic/versions/001_initial_schema.py` |

### Phase 3 : Upload & Extraction

| # | Tache | Fichier(s) |
|---|-------|------------|
| 1 | `papers/exceptions.py` : PaperNotFoundError, InvalidDOIError, ExtractionError | `api/app/papers/exceptions.py` |
| 2 | `papers/dependencies.py` : get_paper_or_404, validate_upload (100MB, MIME check) | `api/app/papers/dependencies.py` |
| 3 | `papers/schemas.py` : PaperCreate (HttpUrl, DOI pattern, model_validator), PaperResponse, PaperList, PaperUpdate | `api/app/papers/schemas.py` |
| 4 | `utils/doi_resolver.py` : DOI -> URL (via doi.org redirect) | `api/app/utils/doi_resolver.py` |
| 5 | `utils/text_extraction.py` : pdfplumber (PDF -> markdown), trafilatura (HTML -> markdown) | `api/app/utils/text_extraction.py` |
| 6 | `papers/service.py` : create_paper, upload PDF, download URL/DOI, extraction texte | `api/app/papers/service.py` |
| 7 | `papers/router.py` : POST /api/papers, GET /api/papers/:id, GET /api/papers/:id/file, DELETE, PATCH | `api/app/papers/router.py` |

**Tests Phase 3 :**
| # | Tache | Fichier |
|---|-------|---------|
| 1 | Test upload PDF (multipart) | `api/tests/papers/test_router.py` |
| 2 | Test upload URL + DOI | `api/tests/papers/test_router.py` |
| 3 | Test validation (taille, MIME, DOI format) | `api/tests/papers/test_router.py` |
| 4 | Test get_paper_or_404 (existe + n'existe pas) | `api/tests/papers/test_router.py` |

### Phase 4 : Claude CLI Integration & Processing

| # | Tache | Fichier(s) |
|---|-------|------------|
| 1 | `processing/exceptions.py` : ClaudeError, EmbeddingError | `api/app/processing/exceptions.py` |
| 2 | `processing/claude_service.py` : call_claude (asyncio.create_subprocess_exec), stream_claude | `api/app/processing/claude_service.py` |
| 3 | Prompt : resume court + long + key findings + metadonnees | `api/app/processing/claude_service.py` |
| 4 | `processing/service.py` : pipeline complet (download -> extract -> summarize -> save) | `api/app/processing/service.py` |
| 5 | `processing/models.py` : ProcessingEvent write a chaque etape | `api/app/processing/models.py` |
| 6 | `processing/router.py` : GET /api/papers/:id/status (SSE stream) | `api/app/processing/router.py` |
| 7 | Background task : processing asynchrone (asyncio.create_task) | `api/app/processing/service.py` |

**Tests Phase 4 :**
| # | Tache | Fichier |
|---|-------|---------|
| 1 | Test call_claude (mock subprocess) | `api/tests/processing/test_claude_service.py` |
| 2 | Test stream_claude (mock subprocess) | `api/tests/processing/test_claude_service.py` |
| 3 | Test SSE status endpoint | `api/tests/processing/test_router.py` |

### Phase 5 : Tags & CRUD Papers

| # | Tache | Fichier(s) |
|---|-------|------------|
| 1 | `tags/exceptions.py` : TagNotFoundError, DuplicateTagError | `api/app/tags/exceptions.py` |
| 2 | `tags/dependencies.py` : get_tag_or_404 | `api/app/tags/dependencies.py` |
| 3 | `tags/schemas.py` : TagResponse, TagMergeRequest | `api/app/tags/schemas.py` |
| 4 | `tags/service.py` : get_all_tags, create_tag, rename_tag, merge_tags | `api/app/tags/service.py` |
| 5 | Prompt Claude : tagging avec liste de tags existants en contexte | `api/app/processing/claude_service.py` |
| 6 | `tags/router.py` : GET /api/tags, GET /api/tags/:id/papers, PATCH, DELETE, POST merge | `api/app/tags/router.py` |
| 7 | `papers/router.py` : GET /api/papers (filtres: tags, status, date, q) | `api/app/papers/router.py` |

**Tests Phase 5 :**
| # | Tache | Fichier |
|---|-------|---------|
| 1 | Test CRUD tags (create, rename, merge, delete) | `api/tests/tags/test_router.py` |
| 2 | Test filtres papers (par tag, status, date) | `api/tests/papers/test_router.py` |

### Phase 6 : Embeddings & Search

| # | Tache | Fichier(s) |
|---|-------|------------|
| 1 | `processing/embedding_service.py` : load sentence-transformers model (singleton) | `api/app/processing/embedding_service.py` |
| 2 | `utils/chunking.py` : split text en chunks (512 tokens, overlap 50) | `api/app/utils/chunking.py` |
| 3 | `processing/embedding_service.py` : encode chunks via run_in_threadpool -> store dans paper_embeddings | `api/app/processing/embedding_service.py` |
| 4 | Integration dans le pipeline : embedding apres summarizing | `api/app/processing/service.py` |
| 5 | `search/schemas.py` : SearchRequest, SearchResult | `api/app/search/schemas.py` |
| 6 | `search/service.py` : FTS (tsvector) + semantic (pgvector) | `api/app/search/service.py` |
| 7 | `search/router.py` : POST /api/search, GET /api/search/similar/:id | `api/app/search/router.py` |

Modele d'embedding configurable :
- Defaut : all-MiniLM-L6-v2 (384 dims, Apache 2.0, ~90MB, CPU-friendly)
- Upgrade possible : nomic-embed-text (768 dims, meilleur sur le scientifique)
- Upgrade possible : BGE-M3 (1024 dims, SOTA retrieval, multilingue, plus lent)
- Tous gratuits et open source, tournent en local
- Config : `EMBEDDING_MODEL_NAME` en variable d'env dans Settings

**Tests Phase 6 :**
| # | Tache | Fichier |
|---|-------|---------|
| 1 | Test embedding service (mock model) | `api/tests/processing/test_embedding_service.py` |
| 2 | Test FTS search | `api/tests/search/test_router.py` |
| 3 | Test semantic search | `api/tests/search/test_router.py` |

### Phase 7 : Chat & RAG

| # | Tache | Fichier(s) |
|---|-------|------------|
| 1 | `chat/exceptions.py` : SessionNotFoundError | `api/app/chat/exceptions.py` |
| 2 | `chat/dependencies.py` : get_session_or_404 | `api/app/chat/dependencies.py` |
| 3 | `chat/schemas.py` : ChatMessageCreate, ChatMessageResponse, SessionResponse | `api/app/chat/schemas.py` |
| 4 | `chat/service.py` : context retrieval (resumes + chunks pertinents) | `api/app/chat/service.py` |
| 5 | `chat/service.py` : mode "paper" (chunks du paper + cross-refs) | `api/app/chat/service.py` |
| 6 | `chat/service.py` : mode "corpus" (semantic search tous papers) | `api/app/chat/service.py` |
| 7 | `chat/service.py` : appel Claude CLI en streaming (stream_claude) | `api/app/chat/service.py` |
| 8 | `chat/router.py` : POST /api/papers/:id/chat (SSE, rate limited), GET sessions, GET messages | `api/app/chat/router.py` |

**Tests Phase 7 :**
| # | Tache | Fichier |
|---|-------|---------|
| 1 | Test chat endpoint (mock Claude CLI) | `api/tests/chat/test_router.py` |
| 2 | Test chat sessions persistence | `api/tests/chat/test_router.py` |

### Phase 8 : Cross-References & Graph

| # | Tache | Fichier(s) |
|---|-------|------------|
| 1 | `processing/crossref_service.py` : trouver papers similaires (pgvector top-K) | `api/app/processing/crossref_service.py` |
| 2 | `processing/crossref_service.py` : gate cosinus > 0.7 + Claude CLI analyse paires -> type + force | `api/app/processing/crossref_service.py` |
| 3 | Integration dans le pipeline post-embedding | `api/app/processing/service.py` |
| 4 | `graph/schemas.py` : GraphData, NodeResponse, EdgeResponse | `api/app/graph/schemas.py` |
| 5 | `graph/service.py` : aggregation noeuds + aretes format Cytoscape | `api/app/graph/service.py` |
| 6 | `graph/router.py` : GET /api/graph, GET /api/graph/paper/:id | `api/app/graph/router.py` |
| 7 | `papers/router.py` : GET /api/papers/:id/crossrefs | `api/app/papers/router.py` |

**Tests Phase 8 :**
| # | Tache | Fichier |
|---|-------|---------|
| 1 | Test crossref service (mock Claude CLI) | `api/tests/processing/test_crossref_service.py` |
| 2 | Test graph endpoints | `api/tests/graph/test_router.py` |

### Phase 9 : Research Intelligence

| # | Tache | Fichier(s) |
|---|-------|------------|
| 1 | `insights/exceptions.py` : InsightNotFoundError | `api/app/insights/exceptions.py` |
| 2 | `insights/dependencies.py` : get_insight_or_404 | `api/app/insights/dependencies.py` |
| 3 | `insights/schemas.py` : InsightResponse, InsightList | `api/app/insights/schemas.py` |
| 4 | `insights/service.py` : declenchement post-crossref | `api/app/insights/service.py` |
| 5 | Prompt Claude : analyse corpus -> gaps, concordances, contradictions | `api/app/processing/claude_service.py` |
| 6 | Prompt Claude : hypotheses et theories (quand seuils atteints) | `api/app/processing/claude_service.py` |
| 7 | `insights/service.py` : storage avec junction table insight_papers | `api/app/insights/service.py` |
| 8 | `insights/router.py` : GET /api/insights, GET /api/insights/:id, PATCH /api/insights/:id/rate | `api/app/insights/router.py` |

**Tests Phase 9 :**
| # | Tache | Fichier |
|---|-------|---------|
| 1 | Test insights endpoints | `api/tests/insights/test_router.py` |
| 2 | Test rating | `api/tests/insights/test_router.py` |

---

## 7. Frontend & Design

> **Spec complete frontend :** voir `.claude/SYNAPSEAI_FRONTEND_FEATURE.md`
>
> Le frontend (React 19, Vite, Tailwind, Shadcn/ui, TanStack Query v5) et le design (Pencil MCP)
> sont entierement specifies dans un fichier dedie, incluant :
> - Architecture Bulletproof React feature-based (7 features)
> - Patterns API (ky + TanStack Query), SSE, state management
> - Specs fonctionnelles de chaque page
> - Tests (Vitest + Testing Library + MSW + Playwright E2E)
> - Design system (Pencil MCP) + phases 10-22

---

## 8. Plan d'execution Backend

> **Plan frontend :** voir `.claude/SYNAPSEAI_FRONTEND_FEATURE.md` section 8 (Sprints 4-9)

### Sprint 1 — Fondations (Phase 1-2) ✓
- [x] Setup monorepo + Docker + DB + test DB
- [x] Core : base models, config (split), database (commit/rollback), exceptions, schemas, enums
- [x] SQLAlchemy models (10 tables, noms singuliers) + Alembic migration
- [x] Tests : health, database connection
- [x] Bulletproof FastAPI review (CORS, handlers, ruff, cleanup)
- [x] **Livrable** : `docker compose up` + `GET /api/health` + schema DB complet

### Sprint 2 — Upload & Processing (Phase 3-4)
- [ ] Upload + extraction de texte + validation (100MB, MIME)
- [ ] Integration Claude CLI (asyncio.create_subprocess_exec)
- [ ] Pipeline processing + SSE status
- [ ] Tests : upload, validation, Claude mock
- [ ] **Livrable** : endpoints upload + processing fonctionnels

### Sprint 3 — Tags, CRUD & Search backend (Phase 5-7)
- [ ] CRUD tags + tagging IA
- [ ] CRUD papers complet avec filtres
- [ ] Embeddings (run_in_threadpool) + semantic search
- [ ] Chat par paper (RAG) + rate limiting
- [ ] Tests : CRUD tags, filtres papers, search, chat
- [ ] **Livrable** : API complete pour papers, tags, search, chat

### Sprint 4 — Cross-refs, Insights & Polish (Phase 8-9)
- [ ] Cross-references automatiques (gate cosinus > 0.7)
- [ ] Graph endpoints
- [ ] Gaps, concordances, hypotheses, theories
- [ ] Insights endpoints + rating
- [ ] Tests : crossref, graph, insights
- [ ] **Livrable** : API backend 100% complete

### Sprint 5 — Deploy
- [ ] Docker production (openapi_url=None)
- [ ] Deploiement Hetzner/local
- [ ] Evaluer qualite embeddings, upgrade modele si necessaire
- [ ] **Livrable** : backend prod-ready

---

## 9. Notes Importantes

### Securite
- Pas d'auth en v1, mais preparer le middleware (placeholder)
- Valider les uploads : taille max (100MB), types acceptes (PDF), verification MIME type reel
- Validation upload via dependency `validate_upload` reutilisable
- Sanitizer le texte extrait avant insertion en DB
- Pas d'injection SQL possible (SQLAlchemy parameterized queries)
- CORS configure pour le frontend uniquement (`http://localhost:5173`)
- Rate limiting in-memory (slowapi) sur endpoints sensibles :
  - `POST /api/papers` : 10/minute
  - `POST /api/papers/:id/chat` : 20/minute
- En production : `HTTPSRedirectMiddleware` + `TrustedHostMiddleware`
- En production : `openapi_url=None` (desactive /docs, /redoc, /openapi.json)

### Performance
- sentence-transformers : charger le modele une seule fois (singleton au startup)
- Encoding embeddings : `run_in_threadpool()` pour ne pas bloquer l'event loop
- Claude CLI : `asyncio.create_subprocess_exec` (jamais subprocess.run en async)
- Embeddings : chunking avec overlap pour meilleure couverture RAG
- pgvector : index HNSW pour search < 50ms meme avec 10K+ papers
- Claude CLI : une seule instance a la fois, queue les requetes
- SSE : connexion legere, pas de WebSocket overhead
- Connection pooling : pool_size=20, max_overflow=10, pool_pre_ping=True
- `response_model` sur chaque endpoint pour la serialisation Rust Pydantic (~2x plus rapide)

### Testing
- pytest-asyncio + httpx AsyncClient des la Phase 1
- DB de test separee (service `db-test` dans Docker Compose, port 5434)
- Tests integration avec DB reelle PostgreSQL (pas SQLite, pas de mocks DB)
- Mock uniquement les services externes : Claude CLI, DOI resolver
- Structure tests miroir de la structure source
- Fixtures : async client, test DB session avec rollback apres chaque test

### Compatibilite
- Python 3.12+ (FastAPI + async)
- PostgreSQL 16 + pgvector 0.7+
- Docker Compose v2
- Claude CLI derniere version

### Evolutions futures (hors scope v1)
- Authentification multi-utilisateur (JWT + roles)
- Notifications in-app + email
- Import batch (CSV de DOIs)
- Export (PDF de synthese, bibliographie BibTeX)
- API publique pour integrations tierces
- Notion sync bidirectionnel (optionnel, pour ceux qui veulent garder Notion)
- Migration rate limiting vers Redis (si multi-worker)

### Dependencies (requirements.txt)
```
fastapi>=0.115
uvicorn[standard]>=0.34
sqlalchemy[asyncio]>=2.0
asyncpg>=0.30
alembic>=1.14
pydantic-settings>=2.7
pgvector>=0.3
slowapi>=0.1
python-multipart>=0.0.9
pdfplumber>=0.11
trafilatura>=1.12
sentence-transformers>=3.0
httpx>=0.27
pytest>=8.0
pytest-asyncio>=0.24
```
