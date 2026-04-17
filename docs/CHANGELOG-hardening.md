# Hardening Changelog — Sprint 5

Source spec: [`.claude/API_BULLETPROOF_FIX_FEATURE.md`](../.claude/API_BULLETPROOF_FIX_FEATURE.md)
Sprint window: 2026-04-17 → 2026-04-28
Scope: 40 findings from a 5-audit parallel review of the FastAPI backend (bulletproof check + Backend Architect + Security Engineer).

All findings are `DONE` unless marked otherwise. Each entry cites the commit that landed it; phases 1-2 ship as a single commit, later phases are one-per-commit.

## Commit map

| Phase | Title | Commit |
|---|---|---|
| 1 + 2 | Observability, config, middlewares, single-worker guard | `79b35a3` feat(hardening): Add observability & config hardening (Wave 1) |
| 3 | SSRF + DNS pinning | `21410eb` fix(ssrf): Add DNS-rebinding-resistant SSRF hardening |
| 4 | Claude prompt hardening | `41f916b` fix(processing): Add Claude prompt hardening defenses |
| 5 | UUID session migration | `d9b670b` fix(chat)!: use UUID for session IDs |
| 6 | Concurrency + commit convention | `cc6e39d` refactor(lifecycle): enforce explicit commits and graceful shutdown |
| 7 | File serving + container hardening | `48c4c66` fix(papers,container): harden file serving and container security |
| 8 | Insights / tags / SSE UX | `fb38104` fix(api): Phase 8 hardening — insights/tags/SSE UX |
| 9 | SSE constants, PDF timeout, async chunking, alembic lock | `668e676` refactor(processing,utils): move SSE constants to config, add per-page PDF timeout, async chunking |
| 10 | Startup checks + Retry-After | `4cd44fc` fix(api): add Retry-After on 429, startup checks |
| 11 | Docs & finalization | _this change_ |

## Findings

### CRITICAL

| # | Title | Status | Phase |
|---|---|---|---|
| F1 | Rate limiter collapse behind proxy | DONE | 2 |
| F2 | GZip compresses SSE | DONE | 2 |
| F3 | SSRF via DNS rebinding + DOI redirect chain | DONE | 3 |
| F4 | Prompt injection via `extracted_text` | DONE | 4 |
| F5 | `stream_claude` stdin.drain without timeout | DONE | 4 |
| F6 | `_paper_events` memory leak | DONE | 6 |
| F7 | IDOR chat session (int auto-increment PK) | DONE | 5 |
| F8 | Dockerfile root user | DONE | 7 |
| F31 | DNS rebinding (IP pinning after resolve) | DONE | 3 |
| F32 | Log redaction (PDF / chat / Claude I/O never logged clear) | DONE | 1 |

### HIGH

| # | Title | Status | Phase |
|---|---|---|---|
| F9 | No central logging config | DONE | 1 |
| F10 | No correlation ID / request ID | DONE | 1 |
| F11 | Process-local locks/counters | DONE | 2 (guard) |
| F12 | `SequenceMatcher` blocks the event loop | DONE | 8 |
| F13 | Inconsistent commit ownership | DONE | 6 |
| F14 | `drain_tasks` shutdown race | DONE | 6 |
| F15 | `docs_url` / `redoc_url` not gated in prod | DONE | 2 |
| F16 | Path traversal race on file endpoint | DONE | 7 |
| F17 | `InsightPaper` missing `ON DELETE CASCADE` | DONE | 8 |
| F33 | Claude output schema validation | DONE | 4 |
| F34 | File serving by UUID only (drop filesystem paths) | DONE | 7 |
| F35 | Extended IP blocklist (IPv6 ULA, IPv4-mapped, CGNAT, metadata) | DONE | 3 |

### MEDIUM

| # | Title | Status | Phase |
|---|---|---|---|
| F18 | 9 `BaseSettings` parse `.env` 9× | DONE | 2 |
| F19 | Tag merge non-transactional | DONE | 8 |
| F20 | SSE without `id:` / Last-Event-ID | DONE | 8 |
| F21 | Startup healthcheck single-connection | DONE | 10 |
| F22 | Embedding unload race | DONE | 6 |
| F23 | No per-page PDF timeout | DONE | 9 |
| F24 | `chunk_text` blocking in async | DONE | 9 |
| F25 | CORS validator for `"*"` | DONE | 2 |
| F36 | Container hardening (read_only, cap_drop, no-new-privileges) | DONE | 7 |
| F37 | Alembic migration lock (`pg_advisory_lock`) | DONE | 9 |
| F38 | SSE disconnect check in generator loop | DONE | 8 |
| F39 | Subprocess zombie reap in finally | DONE | 4 |

### LOW

| # | Title | Status | Phase |
|---|---|---|---|
| F26 | No docker-compose resource limits / healthcheck | DONE | 7 |
| F27 | Lifespan without try/except | DONE | 2 |
| F28 | `/insights/refresh` 409 TOCTOU | DONE | 8 |
| F29 | Magic numbers SSE processing | DONE | 9 |
| F30 | Test confirming `onupdate=func.now()` | DONE | 10 |
| F40 | 429 `Retry-After` header verification | DONE | 10 |

## Deferred to v2

- Auth + per-user scoping (IDOR beyond UUID unguessability)
- Redis, horizontal scaling, distributed tracing
- Persistent audit log
- `pg_trgm` bascule for insight dedup when N > 500 (index provisioned in `add_insight_title_normalized`)

## Non-fixes (faux-positifs clarified during review)

| Claim | Verdict |
|---|---|
| `update_paper` missing commit | Was covered by implicit `get_db.commit()`; explicit post-Phase-6. |
| `except Exception` catches `CancelledError` | **False.** `CancelledError` extends `BaseException` since Python 3.8. |
| Graph N+1 on `p.tags` | **False.** `lazy="selectin"` loads in batch. |
| `_acquire_slot` TOCTOU mono-worker | **False.** No `await` between check and increment. |
| `onupdate=func.now()` broken in async | **False.** Confirmed by spot-check test (Phase 10.5). |
| Ego CTE cyclic explosion | **False.** `UNION` dedup. |
| Graph ETag not representative | **False.** Timestamps + counts cover mutations. |

## Rollback notes

Phase 5 (UUID migration) is the only non-trivially reversible change:

- **Step 1** (`add_uuid_cols_chat`) — reversible (drop added columns).
- **Step 2** (`swap_pk_chat_session`) — **irreversible in practice**; int IDs are gone. Backup before applying.
- **Step 3** (`drop_int_chat_artifacts`) — reversible (recreate sequence + index).

All other phases: `git revert` suffices.
