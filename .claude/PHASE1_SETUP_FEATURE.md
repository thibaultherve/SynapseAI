# Phase 1 : Setup Projet SynapseAI

> **Projet** : SynapseAI — Plateforme de recherche scientifique augmentee par IA
> **Phase** : 1 — Setup Projet (Fondations)
> **Spec source** : `.claude/SYNAPSEAI_BACKEND_FEATURE.md`
> **Date** : 2026-04-04
> **Status** : v4 — Post bulletproof FastAPI review

---

## 1. Overview

### Objectif

Mettre en place le socle technique complet de SynapseAI : structure monorepo domain-based, backend FastAPI fonctionnel, base de donnees PostgreSQL + pgvector avec le schema complet, infrastructure de test, et Docker Compose pour le dev.

### Livrable

A la fin de cette phase :
- `docker compose up` demarre PostgreSQL + pgvector + DB test + API FastAPI
- `GET /api/health` repond `{"status": "ok", "database": "connected"}`
- Le schema DB complet (10 tables) est applique via Alembic
- Les models SQLAlchemy et schemas Pydantic de base sont prets
- L'infrastructure de test est en place (pytest-asyncio + httpx AsyncClient)
- Les premiers tests passent (health, database)

### Stack technique

| Composant | Technologie |
|-----------|-------------|
| Backend | FastAPI (Python 3.12+) |
| ORM | SQLAlchemy 2.0 (async, mapped_column) |
| Migrations | Alembic |
| Schemas | Pydantic v2 |
| Database | PostgreSQL 16 + pgvector 0.7+ |
| Infra dev | Docker Compose |
| Tests | pytest-asyncio + httpx AsyncClient |

---

## 2. Contexte

### Systeme actuel (NeuroAI v1)

Le systeme v1 a ete deplace dans le dossier `v1/` a la racine du workspace. Il contient :
- `v1/scripts/` : Python scripts (poll_papers, process_paper, acquire_paper, verify_sync, etc.)
- `v1/data/` : JSON plats (index.json, state.json, analysis.json) + papers par UUID
- `.claude/skills/` : Claude Code skills pour orchestrer le processing
- Notion comme UI et source de verite

### Transition

Phase 1 cree la structure `synapseai/` a la racine du workspace, **a cote** du dossier `v1/`. Aucune modification du systeme v1. Les deux cohabitent pendant la transition.

---

## 3. Architecture Technique

### 3.1 Structure des fichiers a creer (organisation par domaine)

```
synapseai/
+-- api/
|   +-- app/
|   |   +-- __init__.py
|   |   +-- main.py                   # FastAPI app, lifespan, CORS, global exception handler
|   |   +-- config.py                 # Settings (pydantic-settings, env vars)
|   |   |
|   |   +-- core/                     # Infrastructure partagee
|   |   |   +-- __init__.py
|   |   |   +-- database.py           # AsyncEngine + async_sessionmaker + get_db (commit/rollback)
|   |   |   +-- base.py               # DeclarativeBase avec naming convention
|   |   |   +-- schemas.py            # AppBaseModel + ErrorResponse
|   |   |   +-- exceptions.py         # AppError, NotFoundError, ConflictError
|   |   |   +-- enums.py              # StrEnum pour tous les champs contraints (PaperStatus, SourceType, etc.)
|   |   |
|   |   +-- papers/
|   |   |   +-- __init__.py
|   |   |   +-- models.py             # Paper, PaperTag
|   |   |
|   |   +-- tags/
|   |   |   +-- __init__.py
|   |   |   +-- models.py             # Tag
|   |   |
|   |   +-- processing/
|   |   |   +-- __init__.py
|   |   |   +-- models.py             # PaperEmbedding, CrossReference, ProcessingEvent
|   |   |
|   |   +-- insights/
|   |   |   +-- __init__.py
|   |   |   +-- models.py             # Insight, InsightPaper
|   |   |
|   |   +-- chat/
|   |       +-- __init__.py
|   |       +-- models.py             # ChatSession, ChatMessage
|   |
|   +-- alembic/
|   |   +-- env.py
|   |   +-- script.py.mako
|   |   +-- versions/
|   |       +-- 001_initial_schema.py
|   +-- alembic.ini                   # + file_template configure
|   +-- requirements.txt
|   +-- pyproject.toml                # pytest-asyncio + ruff config
|   +-- Dockerfile
|   +-- tests/
|       +-- conftest.py               # Fixtures: async client, test DB
|       +-- core/
|           +-- test_health.py
|           +-- test_database.py
|
+-- docker-compose.yml
+-- .env.example
+-- .gitignore
```

### 3.2 Aucun fichier existant modifie

Phase 1 est 100% additive. Seuls des fichiers dans `synapseai/` sont crees.

---

## 4. Database

### 4.1 Schema complet (10 tables)

Le schema est defini dans `SYNAPSEAI_FEATURE.md` section 5.1. Alembic migration initiale applique :

**Extension :** `CREATE EXTENSION IF NOT EXISTS vector;`

**Tables :**

> **Note** : Tous les noms de tables sont au **singulier** (ex: `paper`, `tag`, `insight`) suite au bulletproof FastAPI review.

1. **tag** — Tags normalises (geres par l'IA)
   - `id SERIAL PRIMARY KEY`
   - `name TEXT NOT NULL`
   - `category TEXT NOT NULL CHECK (category IN ('sub_domain', 'technique', 'pathology', 'topic'))`
   - `description TEXT`
   - `created_at TIMESTAMPTZ DEFAULT now()`
   - `UNIQUE (name, category)`

2. **paper** — Papers scientifiques
   - `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
   - `title TEXT`, `authors TEXT[]`, `authors_short TEXT`
   - `publication_date DATE`, `journal TEXT`, `doi TEXT UNIQUE`, `url TEXT`
   - `source_type TEXT CHECK ('pdf', 'web')`
   - `status TEXT DEFAULT 'uploading' CHECK (9 valeurs)`
   - `error_message TEXT`
   - `extracted_text TEXT`, `short_summary TEXT`, `detailed_summary TEXT`
   - `key_findings TEXT`, `keywords TEXT[]`, `word_count INT`, `file_path TEXT`
   - `created_at TIMESTAMPTZ`, `updated_at TIMESTAMPTZ`, `processed_at TIMESTAMPTZ`
   - `search_vector TSVECTOR GENERATED ALWAYS AS (...) STORED`

3. **paper_tag** — Junction paper <-> tag
   - `paper_id UUID FK paper(id) ON DELETE CASCADE`
   - `tag_id INT FK tag(id) ON DELETE CASCADE`
   - PK composite (paper_id, tag_id)

4. **paper_embeddings** — Chunks pour RAG
   - `id SERIAL PRIMARY KEY`
   - `paper_id UUID FK`, `chunk_index INT`
   - `chunk_text TEXT`, `embedding vector(384)`
   - UNIQUE (paper_id, chunk_index)

5. **cross_references** — Relations entre papers
   - `id SERIAL PRIMARY KEY`
   - `paper_a UUID FK`, `paper_b UUID FK`
   - `relation_type TEXT CHECK (5 valeurs)`
   - `strength TEXT CHECK ('strong', 'moderate', 'weak')`
   - `description TEXT`, `detected_at TIMESTAMPTZ`
   - UNIQUE (paper_a, paper_b)
   - **CHECK (paper_a < paper_b)** — empeche les doublons (A,B)/(B,A), normaliser l'ordre a l'insertion

6. **insights** — Research Intelligence
   - `id SERIAL PRIMARY KEY`
   - `type TEXT CHECK (6 valeurs)`, `title TEXT`, `content TEXT`
   - `evidence TEXT`, `confidence TEXT CHECK (3 valeurs)`
   - `rating SMALLINT CHECK (rating IN (1, -1)) NULLABLE` — NULL = pas encore vote
   - `detected_at TIMESTAMPTZ`, `updated_at TIMESTAMPTZ`

7. **insight_papers** — Junction insight <-> paper (integrite referentielle)
   - `insight_id INT FK insights(id) ON DELETE CASCADE`
   - `paper_id UUID FK papers(id) ON DELETE CASCADE`
   - PK composite (insight_id, paper_id)

8. **chat_sessions** — Sessions de chat
   - `id SERIAL PRIMARY KEY`
   - `paper_id UUID FK **NULLABLE**`, `scope TEXT DEFAULT 'paper' CHECK ('paper', 'corpus')`
   - **CHECK ((scope='paper' AND paper_id IS NOT NULL) OR (scope='corpus'))** — corpus n'a pas de paper_id
   - `created_at TIMESTAMPTZ`

9. **chat_messages** — Messages de chat
   - `id SERIAL PRIMARY KEY`
   - `session_id INT FK`, `role TEXT CHECK ('user', 'assistant')`
   - `content TEXT`, `created_at TIMESTAMPTZ`

10. **processing_events** — Events pour SSE
    - `id SERIAL PRIMARY KEY`
    - `paper_id UUID FK`, `step TEXT`, `detail TEXT`
    - `created_at TIMESTAMPTZ`

### 4.2 Index

```sql
CREATE INDEX idx_papers_fts ON papers USING GIN (search_vector);
CREATE INDEX idx_papers_status ON papers (status);
CREATE INDEX idx_papers_created ON papers (created_at DESC);
CREATE INDEX idx_embeddings_vec ON paper_embeddings USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_crossref_a ON cross_references (paper_a);
CREATE INDEX idx_crossref_b ON cross_references (paper_b);
CREATE INDEX idx_insights_type ON insights (type);
CREATE INDEX idx_insight_papers_paper ON insight_papers (paper_id);
CREATE INDEX idx_chat_paper ON chat_sessions (paper_id);
CREATE INDEX idx_processing_paper ON processing_events (paper_id, created_at DESC);
CREATE INDEX idx_tags_category ON tags (category);
```

---

## 5. Backend Implementation

### Phase 1.1 — Infra Docker + Config

| # | Tache | Fichier |
|---|-------|---------|
| 1 | Docker Compose : services `db` + `db-test` + `api`, volumes | `synapseai/docker-compose.yml` |
| 2 | Dockerfile API : Python 3.12, pip install, workdir /app | `synapseai/api/Dockerfile` |
| 3 | `.env.example` avec DATABASE_URL, UPLOAD_DIR, DB_PASSWORD, ENV | `synapseai/.env.example` |
| 4 | `.gitignore` pour synapseai (venv, __pycache__, .env, uploads) | `synapseai/.gitignore` |
| 5 | `requirements.txt` | `synapseai/api/requirements.txt` |

### Phase 1.2 — Core Infrastructure

| # | Tache | Fichier |
|---|-------|---------|
| 1 | `config.py` : Settings global (DATABASE_URL, UPLOAD_DIR, ENV, UPLOAD_MAX_SIZE) | `synapseai/api/app/config.py` |
| 2 | `core/base.py` : DeclarativeBase avec naming convention MetaData | `synapseai/api/app/core/base.py` |
| 3 | `core/database.py` : async engine, async_sessionmaker, get_db dependency | `synapseai/api/app/core/database.py` |
| 4 | `core/schemas.py` : AppBaseModel (custom BaseModel), ErrorResponse, ErrorDetail | `synapseai/api/app/core/schemas.py` |
| 5 | `core/exceptions.py` : AppError, NotFoundError, ConflictError, ValidationError | `synapseai/api/app/core/exceptions.py` |

### Phase 1.3 — FastAPI App + Health

| # | Tache | Fichier |
|---|-------|---------|
| 1 | `main.py` : FastAPI app avec lifespan (test DB connection), CORS, global exception handler, health route | `synapseai/api/app/main.py` |
| 2 | Health inline : `GET /api/health` -> teste connexion DB, retourne status | `synapseai/api/app/main.py` |

### Phase 1.4 — SQLAlchemy Models (10 tables)

| # | Tache | Fichier |
|---|-------|---------|
| 1 | `papers/models.py` : Paper (search_vector Computed, updated_at) + PaperTag | `synapseai/api/app/papers/models.py` |
| 2 | `tags/models.py` : Tag | `synapseai/api/app/tags/models.py` |
| 3 | `processing/models.py` : PaperEmbedding (Vector(384)), CrossReference, ProcessingEvent | `synapseai/api/app/processing/models.py` |
| 4 | `insights/models.py` : Insight + InsightPaper (junction) | `synapseai/api/app/insights/models.py` |
| 5 | `chat/models.py` : ChatSession, ChatMessage | `synapseai/api/app/chat/models.py` |

### Phase 1.5 — Alembic

| # | Tache | Fichier |
|---|-------|---------|
| 1 | `alembic.ini` : config (sqlalchemy.url depuis env) | `synapseai/api/alembic.ini` |
| 2 | `alembic/env.py` : async migration setup, import tous les models | `synapseai/api/alembic/env.py` |
| 3 | `001_initial_schema.py` : CREATE EXTENSION vector + 10 tables + 11 index | `synapseai/api/alembic/versions/` |

### Phase 1.6 — Tests

| # | Tache | Fichier |
|---|-------|---------|
| 1 | `conftest.py` : fixtures async client (httpx AsyncClient), test DB session (PostgreSQL reel sur port 5433), rollback apres chaque test | `synapseai/api/tests/conftest.py` |
| 2 | Test health endpoint (`GET /api/health` retourne status ok + database connected) | `synapseai/api/tests/core/test_health.py` |
| 3 | Test database connection (session fonctionne, SELECT 1) | `synapseai/api/tests/core/test_database.py` |

---

## 6. Details d'implementation

### 6.1 config.py

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://synapseai:synapseai@db:5432/synapseai"
    UPLOAD_DIR: str = "/data/uploads"
    ENV: str = "development"
    UPLOAD_MAX_SIZE: int = 100 * 1024 * 1024  # 100MB

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()
```

### 6.2 core/database.py

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
    pool_timeout=30,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with async_session() as session:
        yield session
```

### 6.3 core/schemas.py

```python
from pydantic import BaseModel, ConfigDict

class AppBaseModel(BaseModel):
    """Base model partagee — tous les schemas en heritent."""
    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
    )

class ErrorDetail(AppBaseModel):
    code: str
    message: str

class ErrorResponse(AppBaseModel):
    error: ErrorDetail
```

### 6.4 core/exceptions.py

```python
class AppError(Exception):
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
```

### 6.5 main.py

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.config import settings
from app.core.database import engine, get_db
from app.core.exceptions import AppError

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Test DB connection on startup
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    yield

app = FastAPI(
    title="SynapseAI",
    openapi_url="/api/openapi.json" if settings.ENV != "production" else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )

@app.get("/api/health")
async def health(db=Depends(get_db)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
```

### 6.6 core/base.py

```python
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=convention)
```

### 6.7 Model Paper (extrait)

Points d'attention :
- `id` : `Mapped[uuid.UUID]` avec `server_default=text("gen_random_uuid()")` — UUID genere cote PostgreSQL uniquement (PG16 built-in, pas besoin de pgcrypto)
- `authors` : `ARRAY(Text)` pour PostgreSQL
- `keywords` : `ARRAY(Text)`
- `search_vector` : colonne `Computed` avec `to_tsvector('english', coalesce(title,'') || ' ' || coalesce(short_summary,'') || ' ' || coalesce(extracted_text,''))` — **espaces entre chaque coalesce** pour eviter la fusion de mots
- `status` : string avec `CheckConstraint` pour les 9 valeurs
- `updated_at` : `TIMESTAMPTZ` avec `server_default=func.now()`, `onupdate=func.now()`

### 6.7b Model CrossReference — contrainte d'ordre

```python
class CrossReference(Base):
    __tablename__ = "cross_references"
    __table_args__ = (
        UniqueConstraint("paper_a", "paper_b"),
        CheckConstraint("paper_a < paper_b", name="ordered_pair"),
    )
```

A l'insertion, toujours normaliser : `paper_a, paper_b = sorted([id1, id2])`.

### 6.7c Model ChatSession — paper_id nullable

```python
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'paper' AND paper_id IS NOT NULL) OR (scope = 'corpus')",
            name="scope_paper_check",
        ),
    )
    paper_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=True
    )
```

### 6.7d Model Insight — rating nullable

```python
class Insight(Base):
    rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # CHECK (rating IN (1, -1)) via CheckConstraint — NULL = pas encore vote
```

### 6.8 Model PaperEmbedding (pgvector)

```python
from pgvector.sqlalchemy import Vector

class PaperEmbedding(Base):
    embedding: Mapped[Any] = mapped_column(Vector(384))
```

### 6.9 Model InsightPaper (junction table)

```python
class InsightPaper(Base):
    __tablename__ = "insight_papers"

    insight_id: Mapped[int] = mapped_column(ForeignKey("insights.id", ondelete="CASCADE"), primary_key=True)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True)
```

### 6.10 Tests conftest.py

```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.main import app
from app.core.database import get_db

TEST_DATABASE_URL = "postgresql+asyncpg://synapseai:synapseai_test@localhost:5433/synapseai_test"

test_engine = create_async_engine(TEST_DATABASE_URL)
test_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

async def override_get_db():
    async with test_session() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
```

### 6.11 Docker Compose

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    ports: ["5432:5432"]
    environment:
      POSTGRES_DB: synapseai
      POSTGRES_USER: synapseai
      POSTGRES_PASSWORD: ${DB_PASSWORD:-synapseai_dev}
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U synapseai"]
      interval: 5s
      retries: 5

  db-test:
    image: pgvector/pgvector:pg16
    ports: ["5433:5432"]
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
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql+asyncpg://synapseai:${DB_PASSWORD:-synapseai_dev}@db:5432/synapseai
      UPLOAD_DIR: /data/uploads
      ENV: development
    volumes:
      - ./api:/app
      - uploads:/data/uploads
    depends_on:
      db: { condition: service_healthy }
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

volumes:
  pgdata:
  uploads:
```

### 6.12 Alembic env.py (async)

Points d'attention :
- Utiliser `run_async` avec `asyncpg`
- Importer tous les models de chaque domaine pour que l'autogenerate les detecte
- `target_metadata = Base.metadata`
- `sqlalchemy.url` lu depuis l'env var DATABASE_URL
- **`include_object` filter** : exclure les colonnes `Computed` et le type `Vector` des diffs autogenerate pour eviter le bruit de migration a chaque run. Exemple :
  ```python
  def include_object(object, name, type_, reflected, compare_to):
      if type_ == "column" and hasattr(object, "computed"):
          return False
      return True
  ```
- Apres autogenerate, **toujours verifier** la migration generee pour les colonnes `Computed` et `Vector(384)` — ajuster manuellement si necessaire

### 6.13 Migration initiale

La migration doit :
1. `op.execute("CREATE EXTENSION IF NOT EXISTS vector")`
2. Creer les 10 tables dans l'ordre (respecter les FK)
3. Creer les 11 index

---

## 7. Execution Plan

### Phase 1.1 — Infra Docker + Config
- [x] Creer `synapseai/docker-compose.yml` (services db + db-test + api, volumes)
- [x] Creer `synapseai/api/Dockerfile` (Python 3.12, pip install, workdir /app)
- [x] Creer `synapseai/.env.example` (DATABASE_URL, UPLOAD_DIR, DB_PASSWORD, ENV)
- [x] Creer `synapseai/.gitignore`
- [x] Creer `synapseai/api/requirements.txt`

### Phase 1.2 — Core Infrastructure
- [x] Creer `synapseai/api/app/__init__.py`
- [x] Creer `synapseai/api/app/config.py` (pydantic-settings)
- [x] Creer `synapseai/api/app/core/__init__.py`
- [x] Creer `synapseai/api/app/core/base.py` (DeclarativeBase + naming convention)
- [x] Creer `synapseai/api/app/core/database.py` (async engine + session + get_db)
- [x] Creer `synapseai/api/app/core/schemas.py` (AppBaseModel + ErrorResponse)
- [x] Creer `synapseai/api/app/core/exceptions.py` (AppError hierarchy)

### Phase 1.3 — FastAPI App + Health
- [x] Creer `synapseai/api/app/main.py` (lifespan, CORS, global exception handler, health route, openapi_url conditionnel)

### Phase 1.4 — SQLAlchemy Models (10 tables)
- [x] Creer `synapseai/api/app/papers/__init__.py` + `models.py` (Paper + PaperTag)
- [x] Creer `synapseai/api/app/tags/__init__.py` + `models.py` (Tag)
- [x] Creer `synapseai/api/app/processing/__init__.py` + `models.py` (PaperEmbedding + CrossReference + ProcessingEvent)
- [x] Creer `synapseai/api/app/insights/__init__.py` + `models.py` (Insight + InsightPaper)
- [x] Creer `synapseai/api/app/chat/__init__.py` + `models.py` (ChatSession + ChatMessage)

### Phase 1.5 — Alembic + Migration
- [x] Creer `synapseai/api/alembic.ini`
- [x] Creer `synapseai/api/alembic/env.py` (async setup, import all domain models, include_object filter)
- [x] Creer `synapseai/api/alembic/script.py.mako`
- [x] Migration initiale ecrite manuellement (Computed + Vector necessitent controle manuel)
- [x] La migration doit : CREATE EXTENSION vector + 10 tables + CHECK constraints + 11 index

### Phase 1.6 — Tests
- [x] Creer `synapseai/api/tests/__init__.py`
- [x] Creer `synapseai/api/tests/conftest.py` (async client + test DB sur port 5433)
- [x] Creer `synapseai/api/tests/core/__init__.py`
- [x] Creer `synapseai/api/tests/core/test_health.py`
- [x] Creer `synapseai/api/tests/core/test_database.py`

### Pre-requis : Git
- [ ] Commit le move v1/ + specs .claude/ (clean slate)
- [x] Creer branche `dev` et switch dessus

### Validation finale
- [x] `docker compose up -d` demarre sans erreur
- [x] `alembic upgrade head` applique la migration
- [x] `curl localhost:8000/api/health` retourne `{"status":"ok","database":"connected"}`
- [x] Les 10 tables existent dans PostgreSQL avec les bons index + CHECK constraints
- [x] `pytest` passe (health + database tests)

---

## 8. Notes

### Securite
- Pas d'auth en v1 (placeholder middleware prevu pour plus tard)
- CORS restreint a `http://localhost:5173`
- SQLAlchemy parameterized queries (pas d'injection SQL)
- En production : `openapi_url=None`

### Performance
- async engine (asyncpg) des le depart
- HNSW index sur les embeddings (m=16, ef_construction=64) pour search < 50ms
- Connection pooling : pool_size=20, max_overflow=10, pool_pre_ping=True, pool_timeout=30
- `expire_on_commit=False` : performances async, mais stale reads possibles apres commit (tradeoff documente)

### Testing
- DB test separee sur port 5433 (service `db-test` dans Docker Compose)
- httpx AsyncClient + ASGITransport (pas TestClient sync)
- DB reelle PostgreSQL (pas SQLite, pas de mocks)
- Rollback apres chaque test

### Compatibilite
- Python 3.12+
- PostgreSQL 16 + pgvector 0.7+
- Docker Compose v2

### Dependencies (requirements.txt)
```
fastapi>=0.115
uvicorn[standard]>=0.34
sqlalchemy[asyncio]>=2.0
asyncpg>=0.30
alembic>=1.14
pydantic-settings>=2.7
pgvector>=0.3
python-multipart>=0.0.9
pytest>=8.0
pytest-asyncio>=0.24
httpx>=0.27
```

> **Note** : `slowapi` est reporte a Phase 3 (rate limiting). `python-multipart` est necessaire pour le file upload en Phase 3 mais inclus d'office (FastAPI le requiert pour les formulaires).

### Decisions Q&A integrees
| Decision | Choix |
|----------|-------|
| Structure | Sous-dossier `synapseai/` |
| Python | 3.12 |
| Migration | Auto-generate + ajustement manuel |
| Test DB | Service `db-test` Docker (port 5433) |
| FTS search_vector | `Computed()` dans le model |
| Authors | `ARRAY(Text)` natif PostgreSQL |
| .gitignore | Dans `synapseai/` |
| Lifespan | Verifie connexion DB au startup |
| UUID | `gen_random_uuid()` cote PostgreSQL (PG16 built-in) |
| Rate limiting | Reporte a Phase 3+ |
| insights.rating | Nullable (NULL = pas encore vote) |
| chat_sessions.paper_id | Nullable + CHECK contrainte scope |
| cross_references | CHECK (paper_a < paper_b) — normaliser l'ordre |
| HNSW index | Params explicites : m=16, ef_construction=64 |
| pool_timeout | 30s explicite |
| Alembic | include_object filter pour Computed/Vector |

---

## 9. Post-Review Deviations (Bulletproof FastAPI Review)

> Le code a ete audite via bulletproof-fastapi-review apres l'implementation initiale.
> Les changements ci-dessous sont appliques dans le code mais les snippets des sections 6.x ci-dessus refletent encore la spec originale.
> **En cas de doute, le code fait autorite.**

### 9.1 Noms de tables singuliers

Toutes les tables renommees au singulier (best practice SQLAlchemy) :

| Spec originale | Implementation |
|----------------|----------------|
| `papers` | `paper` |
| `tags` | `tag` |
| `paper_tags` | `paper_tag` |
| `paper_embeddings` | `paper_embedding` |
| `cross_references` | `cross_reference` |
| `insights` | `insight` |
| `insight_papers` | `insight_paper` |
| `chat_sessions` | `chat_session` |
| `chat_messages` | `chat_message` |
| `processing_events` | `processing_event` |

Toutes les FK, contraintes, et index mis a jour en consequence dans les models et la migration.

### 9.2 config.py — Settings decouple par domaine

La classe `Settings` monolithique est scindee en 3 :

| Classe | Variables | Instance |
|--------|-----------|----------|
| `DatabaseConfig` | `DATABASE_URL` | `db_settings` |
| `UploadConfig` | `UPLOAD_DIR`, `UPLOAD_MAX_SIZE` | `upload_settings` |
| `AppConfig` | `ENV` | `settings` |

`database.py` importe `db_settings` (et non plus `settings`).

### 9.3 database.py — get_db avec commit/rollback

```python
async def get_db():
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

### 9.4 main.py — CORS explicite + handlers supplementaires

**CORS** : `allow_methods` et `allow_headers` sont des listes explicites (pas `["*"]`) car `allow_credentials=True` interdit les wildcards.

```python
allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
allow_headers=["Authorization", "Content-Type"],
```

**Exception handlers ajoutes** :
- `@app.exception_handler(RequestValidationError)` — format coherent avec `AppError`
- `@app.exception_handler(Exception)` — handler global 500, log + reponse sanitisee

### 9.5 Nouveau fichier : core/enums.py

StrEnum pour tous les champs contraints (a utiliser dans les schemas Pydantic futurs) :
`PaperStatus`, `SourceType`, `TagCategory`, `RelationType`, `ReferenceStrength`, `InsightType`, `InsightConfidence`, `ChatScope`, `ChatRole`

### 9.6 Tooling

- `alembic.ini` : `file_template = %%(year)d-%%(month).2d-%%(day).2d_%%(slug)s`
- `pyproject.toml` : section `[tool.ruff]` (target py312, rules E/W/F/I/UP/B/SIM/ASYNC)

### 9.7 conftest.py — cleanup + rollback

- `dependency_overrides` scope au fixture (pas module-level), avec `clear()` en teardown
- `override_get_db` fait `rollback()` dans `finally` pour isolation inter-tests

### 9.8 Port db-test

Le port du service `db-test` est **5434** (pas 5433 comme indique dans les sections 6.10 et 8).
