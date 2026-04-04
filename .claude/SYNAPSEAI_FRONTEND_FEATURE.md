# SynapseAI Frontend - Spec Technique Complete

> **Projet** : SynapseAI — Plateforme de recherche scientifique augmentee par IA
> **Auteur** : Thibault Herve
> **Date** : 2026-04-04
> **Status** : Draft v1 (post bulletproof React Q&A)
> **Backend** : Voir `.claude/SYNAPSEAI_BACKEND_FEATURE.md` pour la spec backend complete (FastAPI + DB + Processing)
> **Architecture** : Bulletproof React (feature-based, unidirectional, integration-first testing)

---

## 1. Overview

### Objectif

Spec technique complete du frontend SynapseAI : une SPA React qui consomme l'API FastAPI pour permettre a un labo de recherche d'uploader des papers, les analyser par IA, chatter avec eux, visualiser le graphe de connaissances, et decouvrir des insights emergeants.

### Resume technique

| Composant | Technologie |
|-----------|-------------|
| Framework | React 19 + Vite 6 + TypeScript |
| Styling | Tailwind CSS v4 + Shadcn/ui |
| Data fetching | TanStack Query v5 |
| HTTP client | ky (3KB, interceptors built-in) |
| Routing | react-router v7 |
| App state | React Context (theme + sidebar) |
| Form state | react-hook-form + Zod (upload) |
| URL state | react-router searchParams (filtres) |
| PDF viewer | react-pdf (pdf.js) |
| Graph | Cytoscape.js + react-cytoscapejs |
| Markdown | react-markdown + remark-gfm |
| Toasts | Sonner |
| Testing | Vitest + Testing Library + MSW v2 |
| E2E | Playwright |
| Lint | ESLint + check-file + import/no-restricted-paths |
| Format | Prettier |
| Git hooks | Husky + lint-staged |
| Design | Tailwind v4 + Shadcn/ui + Pencil MCP (design-system.pen + design.pen) |

### Architecture

```
React SPA (Vite, react-router)
    | REST + SSE (ky + TanStack Query + EventSource)
FastAPI (Python)
    +-- PostgreSQL + pgvector
    +-- Claude CLI
    +-- sentence-transformers
```

---

## 2. Contexte

### Decisions du Q&A Bulletproof React

Ces decisions ont ete prises lors du Q&A avec l'utilisateur le 2026-04-04 :

| Decision | Choix | Justification |
|----------|-------|---------------|
| Structure | Feature-based (7 features) | Bulletproof-react : colocation, scalabilite |
| Upload | Feature separee de papers | Flow complexe (drag&drop, URL/DOI, SSE progress) |
| Tags | Feature + page Taxonomie | Page dediee pour gerer/merge/rename les tags |
| Processing | Dans features/papers/ | Trop petit pour une feature (1 hook + 1 composant) |
| Data fetching | TanStack Query v5 | Standard bulletproof-react pour REST + cache |
| API client | ky (3KB) | Interceptors, retry, JSON auto, zero boilerplate |
| App state | React Context seul | Suffisant pour theme + sidebar (low-velocity) |
| Filtres papers | URL state (searchParams) | Shareable URLs, back button, refresh conserve |
| Chat SSE | POST + SSE direct | Standard apps IA (ChatGPT, Claude.ai) |
| Auto-refetch | Invalidation TQ quand processing done | SSE step=done invalide le cache paper |
| Dashboard | Pas de page separee | Une seule PapersPage avec upload en haut |
| Panel detail | Summaries fixes + Chat drawer | Summaries toujours visibles, chat en overlay |
| Graph filtres | Toolbar en haut | Dropdowns horizontaux, max d'espace pour le graphe |
| Mobile | Deprioritise | Desktop-first, mobile dans une phase ulterieure |
| Dark mode | Suit l'OS | prefers-color-scheme + toggle override |
| Notifications | Toast Sonner + badge sidebar | Toast immediat + badge persistant |
| Tests frontend | Rigoureux par feature | Integration tests avec Testing Library + MSW |
| E2E | Playwright dans le scope | Happy paths des flows principaux |
| PDF viewer | react-pdf | pdf.js wrapper, le plus populaire |
| Graph lib | Cytoscape.js | Concu pour reseaux biologiques, mature, riche |
| Markdown | react-markdown + remark-gfm | Standard React, leger, suffisant |

---

## 3. Specifications Fonctionnelles Frontend

### 3.1 PapersPage (`/` et `/papers`)

Page principale. Combine upload + liste de papers + filtres.

**Layout :**
```
+--------------------------------------------------+
| SidebarNav (240px)  |  Content                   |
|                     |  +------------------------+ |
| [Papers] (active)   |  | UploadZone             | |
| [Tags]              |  | (drag&drop / URL/DOI)  | |
| [Search]            |  +------------------------+ |
| [Graph]             |  | Filters bar            | |
| [Insights]          |  | [Tags v] [Status v]    | |
|                     |  | [Date range] [Search]  | |
|                     |  +------------------------+ |
|                     |  | PaperCard              | |
|                     |  | PaperCard              | |
|                     |  | PaperCard              | |
|                     |  | ...                    | |
|                     |  +------------------------+ |
+--------------------------------------------------+
```

**UploadZone (en haut) :**
- Zone drag & drop pour PDF (visual feedback au dragover)
- OU input URL (paste, validation HttpUrl)
- OU input DOI (paste, validation regex `^10\.\d{4,}/\S+$`)
- Etats : idle, dragover, uploading (progress bar SSE), done, error
- Upload multipart pour PDF, JSON body pour URL/DOI
- Apres upload reussi : le paper apparait dans la liste avec status "processing"

**PaperCard :**
- Titre (tronque a 2 lignes max)
- Auteurs (format court `authors_short`)
- Date de publication
- TagBadges (colores par categorie : sub_domain=indigo, technique=violet, pathology=rose, topic=emerald)
- StatusBadge (uploading=neutral, processing=amber, done=green, error=red)
- Date d'ajout
- Click → navigate vers `/papers/:id`
- Hover → prefetch paper detail via `queryClient.prefetchQuery`

**Filtres (URL state via searchParams) :**
- Multi-select tags (groupe par categorie)
- Select status (all, processing, done, error)
- Date range picker
- Full-text search input (debounced 300ms)
- Toggle "Recherche intelligente" (semantic search via pgvector)
- Tous les filtres persistent dans l'URL : `?tags=1,2&status=done&q=myelin&mode=semantic`

**Tri :**
- Par date d'ajout (defaut, DESC)
- Par date de publication
- Par titre
- Par nombre de cross-refs

**Pagination :**
- Infinite scroll OU pagination classique (offset/limit dans l'URL)
- `usePapers()` utilise `useInfiniteQuery` ou `useQuery` selon le choix

**Etats speciaux :**
- Empty state : aucun paper → message + CTA "Uploadez votre premier paper"
- Loading : skeleton cards (3-6 placeholders animes)
- Error : message inline avec retry button
- Filtres sans resultats : message "Aucun paper ne correspond aux filtres"

### 3.2 PaperDetailPage (`/papers/:id`)

Page dediee par paper. Layout 60/40 : viewer a gauche, panel droit avec summaries fixes + chat en drawer.

**Layout :**
```
+--------------------------------------------------+
| SidebarNav | Viewer (60%)    | SummaryPanel (40%) |
|            | +--------------+| +----------------+ |
|            | | PDF Viewer   || | Short summary  | |
|            | | (react-pdf)  || | Detailed       | |
|            | | OU           || | (collapsible)  | |
|            | | Markdown     || | Key findings   | |
|            | | Viewer       || | (numbered)     | |
|            | |              || |                | |
|            | +--------------+| +----------------+ |
|            | Metadata bar    | [Open Chat] btn    |
+--------------------------------------------------+
```

**Viewer (panneau gauche, ~60%) :**
- Si source PDF → react-pdf viewer avec :
  - Navigation par pages (prev/next + input numero)
  - Zoom (fit-width, fit-page, zoom in/out)
  - Toolbar en haut du viewer
- Si source web → markdown rendering (extracted_text via react-markdown)
- Lien vers URL originale toujours visible
- Metadata bar en haut : titre, auteurs, date, journal, DOI, tags (cliquables → filtrent la PapersPage)

**SummaryPanel (panneau droit, ~40%) :**
- Toujours visible (pas de toggle)
- 3 sections :
  - Short summary (4-10 phrases) — toujours expand
  - Detailed summary (800-1200 mots) — collapsible, collapse par defaut
  - Key findings (3-7 items numerotes) — toujours expand
- Rendu markdown (react-markdown)
- Si paper en processing → skeleton + ProcessingStatus

**Chat Drawer :**
- Bouton "Open Chat" en bas du SummaryPanel
- Ouvre un drawer overlay a droite (au-dessus du SummaryPanel)
- Le drawer peut etre ferme (retour aux summaries)
- Contenu du drawer :
  - Header : toggle scope "Ce paper" / "Tout le corpus"
  - Zone messages : ChatMessage[] (scrollable, auto-scroll)
  - Input en bas : textarea + bouton envoyer
- ChatMessage : bulle user (droite) ou assistant (gauche)
  - Assistant : rendu markdown (react-markdown)
  - Streaming : les mots apparaissent progressivement (SSE)
- Sessions : dropdown pour changer de session ou en creer une nouvelle
- Historique persiste (chat_sessions + chat_messages en DB)

**ProcessingStatus (si paper en cours) :**
- Barre de progression avec label de l'etape courante
- Etapes : downloading → extracting → summarizing → tagging → embedding → crossrefing → done
- SSE via `useProcessingEvents(paperId)` → auto-invalidation du paper quand done
- Si error : message d'erreur + possibilite de retry

**Etats :**
- Loading : skeleton viewer + skeleton summaries
- Paper not found : 404 page
- Processing : skeleton summaries + progress bar, viewer si PDF deja uploade

### 3.3 TagsPage (`/tags`)

Page Taxonomie pour gerer les tags.

**Layout :**
```
+--------------------------------------------------+
| SidebarNav | Tags par categorie                   |
|            | +----------------------------------+ |
|            | | sub_domain          | technique   | |
|            | | [Molecular] (12)    | [scRNA] (8) | |
|            | | [Cellular] (9)      | [ATAC] (5)  | |
|            | | ...                 | ...         | |
|            | +----------------------------------+ |
|            | | pathology           | topic       | |
|            | | [MS] (15)           | [oligo] (7) | |
|            | | [Alzheimer] (6)     | [epigen] (4)| |
|            | +----------------------------------+ |
+--------------------------------------------------+
```

**Fonctionnalites :**
- Tags groupes par categorie (4 groupes : sub_domain, technique, pathology, topic)
- Chaque tag affiche le nombre de papers associes
- Click sur un tag → navigate vers `/papers?tags={id}` (filtre)
- Actions par tag :
  - Rename (inline edit ou dialog)
  - Delete (confirmation dialog, CASCADE les paper_tags)
- Action merge : selectionner 2 tags → bouton merge → dialog de confirmation
- Pas de creation manuelle (les tags sont crees par Claude pendant le processing)

### 3.4 SearchPage (`/search`)

**Layout :**
```
+--------------------------------------------------+
| SidebarNav | SearchBar                             |
|            | [                    ] [Exact|Intel]  |
|            | +----------------------------------+ |
|            | | SearchResult                      | |
|            | | Titre du paper                    | |
|            | | "...passage pertinent surligne..."| |
|            | | Tags  | Score pertinence          | |
|            | +----------------------------------+ |
|            | | SearchResult                      | |
|            | | ...                               | |
+--------------------------------------------------+
```

**SearchBar :**
- Input texte (debounced 300ms)
- Toggle mode : "Exact" (FTS PostgreSQL) / "Intelligent" (semantic pgvector)
- La query + le mode sont dans l'URL : `?q=myelin&mode=semantic`

**SearchResult :**
- Titre du paper (click → navigate)
- Passage le plus pertinent mis en surbrillance (highlight du match)
- Tags du paper
- Score de pertinence (si semantic)

**Etats :**
- Initial : empty state "Tapez une recherche"
- Loading : skeleton results
- No results : message "Aucun resultat pour X"
- Results : liste triee par pertinence

### 3.5 GraphPage (`/graph`)

**Layout :**
```
+--------------------------------------------------+
| SidebarNav | Toolbar                               |
|            | [Tags v] [Type v] [Force v] [Date]   |
|            | +----------------------------------+ |
|            | |                                  | |
|            | |       Cytoscape.js Graph          | |
|            | |       (full height)               | |
|            | |                                  | |
|            | |                                  | |
|            | +----------------------------------+ |
+--------------------------------------------------+
```

**Graph (Cytoscape.js) :**
- Noeuds = papers
  - Taille = nombre de connexions
  - Couleur = sub_domain principal (meme couleurs que TagBadge)
  - Label = titre tronque
- Aretes = cross-references
  - Epaisseur = force (strong > moderate > weak)
  - Couleur = type (concordance=green, contradiction=red, extends=blue, methodology=purple, similar=gray)
  - Style : solid (strong), dashed (moderate), dotted (weak)
- Layout : force-directed (cose layout de Cytoscape)

**Interactions :**
- Zoom / pan (scroll + drag)
- Click sur noeud → navigate vers `/papers/:id`
- Hover sur noeud → tooltip (titre complet + resume court + nombre de refs)
- Hover sur arete → tooltip (type + force + description)

**Toolbar filtres (dropdowns en haut) :**
- Tags : multi-select, montre/cache les papers par tag
- Type de relation : checkboxes (concordance, contradiction, extends, etc.)
- Force minimum : slider ou select (all, moderate+, strong only)
- Date : range slider temporel (afficher l'evolution du graphe)
- Reset filtres button

**Etats :**
- Loading : spinner centre
- Empty : aucune cross-reference → message "Pas encore de connexions"
- Few nodes (<5) : message indicatif "Le graphe s'enrichit avec plus de papers"

### 3.6 InsightsPage (`/insights`)

**Layout :**
```
+--------------------------------------------------+
| SidebarNav | Filters                               |
|            | [Type v] [Confidence v] [Tags v]     |
|            | +----------------------------------+ |
|            | | gap (3)                           | |
|            | | InsightCard  InsightCard           | |
|            | +----------------------------------+ |
|            | | concordance (5)                   | |
|            | | InsightCard  InsightCard ...       | |
|            | +----------------------------------+ |
|            | | hypothesis (2)                    | |
|            | | InsightCard  InsightCard           | |
|            | +----------------------------------+ |
+--------------------------------------------------+
```

**InsightCard :**
- Icone par type (gap=circle-dashed, concordance=git-merge, contradiction=git-compare, hypothesis=lightbulb, theory=atom, direction=compass)
- Titre
- Contenu (tronque, expand on click)
- Confidence badge (high=green, moderate=amber, speculative=purple)
- Papers de soutien (liens cliquables → `/papers/:id`)
- Rating : thumbs up / thumbs down (optimistic update via TQ mutation)
- Timestamp "Detecte le..."

**Filtres :**
- Par type (multi-select)
- Par confidence (multi-select)
- Par tags des papers associes

**Groupement :** par type d'insight (sections collapsibles)

**Etats :**
- Empty : "Pas encore d'insights. Continuez a uploader des papers !"
- Loading : skeleton cards

---

## 4. Architecture Technique

### 4.1 Structure du projet (Bulletproof React feature-based)

```
synapseai/web/
+-- src/
|   +-- app/                           # Application layer
|   |   +-- routes/                    # Route components (lazy loaded)
|   |   |   +-- papers.tsx             # PapersPage
|   |   |   +-- paper-detail.tsx       # PaperDetailPage (lazy: react-pdf)
|   |   |   +-- tags.tsx               # TagsPage
|   |   |   +-- search.tsx             # SearchPage
|   |   |   +-- graph.tsx              # GraphPage (lazy: cytoscape)
|   |   |   +-- insights.tsx           # InsightsPage
|   |   +-- app.tsx                    # Root component
|   |   +-- provider.tsx               # QueryClientProvider + RouterProvider + ThemeProvider
|   |   +-- router.tsx                 # Route config + lazy() + Suspense + ErrorBoundaries
|   |
|   +-- components/                    # SHARED components (used by 2+ features)
|   |   +-- ui/                        # Shadcn/ui primitives (Button, Input, Dialog, Tabs, Sheet, etc.)
|   |   +-- layouts/
|   |   |   +-- app-layout.tsx         # Sidebar + content wrapper + Sonner Toaster
|   |   |   +-- sidebar-nav.tsx        # Nav items + processing badge counter
|   |   +-- errors/
|   |   |   +-- error-boundary.tsx     # Reusable ErrorBoundary wrapper
|   |   |   +-- error-fallback.tsx     # Fallback UI (retry button)
|   |   |   +-- not-found.tsx          # 404 page
|   |   +-- feedback/
|   |       +-- loading-skeleton.tsx   # Configurable skeleton (card, list, page)
|   |       +-- empty-state.tsx        # Icon + message + optional CTA
|   |
|   +-- features/                      # Feature modules (7 features)
|   |   +-- papers/
|   |   |   +-- api/
|   |   |   |   +-- get-papers.ts      # PapersParams + getPapers() + usePapers()
|   |   |   |   +-- get-paper.ts       # getPaper(id) + usePaper(id)
|   |   |   |   +-- get-paper-file.ts  # getPaperFileUrl(id)
|   |   |   |   +-- get-crossrefs.ts   # getCrossrefs(id) + useCrossrefs(id)
|   |   |   |   +-- update-paper.ts    # useUpdatePaper() + invalidation
|   |   |   |   +-- delete-paper.ts    # useDeletePaper() + invalidation
|   |   |   +-- components/
|   |   |   |   +-- paper-card.tsx
|   |   |   |   +-- paper-list.tsx
|   |   |   |   +-- paper-metadata.tsx
|   |   |   |   +-- paper-filters.tsx
|   |   |   |   +-- pdf-viewer.tsx          # react-pdf wrapper
|   |   |   |   +-- markdown-viewer.tsx     # react-markdown wrapper
|   |   |   |   +-- summary-panel.tsx
|   |   |   |   +-- processing-status.tsx
|   |   |   +-- hooks/
|   |   |   |   +-- use-paper-filters.ts    # URL state (searchParams)
|   |   |   |   +-- use-processing-events.ts # SSE EventSource + TQ invalidation
|   |   |   +-- types/
|   |   |       +-- paper.ts
|   |   |
|   |   +-- upload/
|   |   |   +-- api/
|   |   |   |   +-- upload-paper.ts         # useUploadPaper() multipart + URL/DOI
|   |   |   +-- components/
|   |   |   |   +-- upload-zone.tsx          # Drag&drop + URL/DOI input
|   |   |   |   +-- upload-progress.tsx      # Progress feedback
|   |   |   +-- types/
|   |   |       +-- upload.ts
|   |   |
|   |   +-- tags/
|   |   |   +-- api/
|   |   |   |   +-- get-tags.ts
|   |   |   |   +-- get-tag-papers.ts
|   |   |   |   +-- rename-tag.ts
|   |   |   |   +-- merge-tags.ts
|   |   |   |   +-- delete-tag.ts
|   |   |   +-- components/
|   |   |   |   +-- tag-badge.tsx
|   |   |   |   +-- tag-filter.tsx
|   |   |   |   +-- tag-list.tsx
|   |   |   |   +-- tag-merge-dialog.tsx
|   |   |   +-- types/
|   |   |       +-- tag.ts
|   |   |
|   |   +-- chat/
|   |   |   +-- api/
|   |   |   |   +-- send-message.ts         # POST + SSE stream handler
|   |   |   |   +-- get-sessions.ts         # useChatSessions(paperId)
|   |   |   |   +-- get-messages.ts         # useChatMessages(sessionId)
|   |   |   +-- components/
|   |   |   |   +-- chat-drawer.tsx
|   |   |   |   +-- chat-message.tsx
|   |   |   |   +-- chat-input.tsx
|   |   |   |   +-- scope-toggle.tsx
|   |   |   |   +-- session-selector.tsx
|   |   |   +-- hooks/
|   |   |   |   +-- use-chat-stream.ts      # fetch + ReadableStream SSE parsing
|   |   |   +-- types/
|   |   |       +-- chat.ts
|   |   |
|   |   +-- search/
|   |   |   +-- api/
|   |   |   |   +-- search-papers.ts        # useSearchPapers(query, mode)
|   |   |   |   +-- get-similar.ts          # useSimilarPapers(paperId)
|   |   |   +-- components/
|   |   |   |   +-- search-bar.tsx
|   |   |   |   +-- search-results.tsx
|   |   |   |   +-- search-mode-toggle.tsx
|   |   |   |   +-- search-highlight.tsx    # Surlignage du passage pertinent
|   |   |   +-- types/
|   |   |       +-- search.ts
|   |   |
|   |   +-- graph/
|   |   |   +-- api/
|   |   |   |   +-- get-graph.ts            # useGraph(filters)
|   |   |   |   +-- get-paper-graph.ts      # usePaperGraph(paperId)
|   |   |   +-- components/
|   |   |   |   +-- knowledge-graph.tsx     # Cytoscape.js wrapper
|   |   |   |   +-- graph-toolbar.tsx       # Filtres horizontaux
|   |   |   |   +-- graph-tooltip.tsx       # Tooltip hover noeud/arete
|   |   |   +-- hooks/
|   |   |   |   +-- use-graph-filters.ts    # State local des filtres graph
|   |   |   +-- types/
|   |   |       +-- graph.ts
|   |   |
|   |   +-- insights/
|   |       +-- api/
|   |       |   +-- get-insights.ts         # useInsights(filters)
|   |       |   +-- get-insight.ts          # useInsight(id)
|   |       |   +-- rate-insight.ts         # useRateInsight() optimistic update
|   |       +-- components/
|   |       |   +-- insight-card.tsx
|   |       |   +-- insight-list.tsx
|   |       |   +-- insight-filters.tsx
|   |       +-- types/
|   |           +-- insight.ts
|   |
|   +-- hooks/                             # SHARED hooks (used by 2+ features)
|   |   +-- use-debounce.ts
|   |   +-- use-media-query.ts
|   |
|   +-- lib/                               # Configured libraries
|   |   +-- api-client.ts                  # ky instance + error interceptor + toast
|   |   +-- query-client.ts               # TanStack QueryClient config
|   |   +-- utils.ts                       # cn() helper (clsx + tailwind-merge)
|   |
|   +-- config/
|   |   +-- env.ts                         # API_BASE_URL, etc.
|   |
|   +-- contexts/
|   |   +-- theme-context.tsx              # ThemeProvider (light/dark/system + toggle)
|   |
|   +-- types/                             # SHARED types
|   |   +-- api.ts                         # ApiError, PaginatedResponse<T>
|   |   +-- common.ts                      # UUID, Timestamp, StatusType aliases
|   |
|   +-- testing/                           # Test infrastructure
|   |   +-- mocks/
|   |   |   +-- handlers/
|   |   |   |   +-- papers.ts
|   |   |   |   +-- tags.ts
|   |   |   |   +-- chat.ts
|   |   |   |   +-- search.ts
|   |   |   |   +-- graph.ts
|   |   |   |   +-- insights.ts
|   |   |   +-- factories.ts              # createPaper(), createTag(), createInsight()...
|   |   |   +-- server.ts                 # MSW setupServer
|   |   +-- test-utils.tsx                 # renderWithProviders()
|   |
|   +-- styles/
|       +-- globals.css                    # Tailwind v4 imports + CSS variables (light/dark)
|
+-- public/
+-- index.html
+-- vite.config.ts
+-- tsconfig.json
+-- tsconfig.app.json
+-- tailwind.config.ts
+-- postcss.config.js
+-- eslint.config.js                       # Flat config + import restrictions
+-- prettier.config.js
+-- playwright.config.ts
+-- vitest.config.ts
+-- components.json                        # Shadcn/ui config
+-- package.json
+-- Dockerfile
```

### 4.2 Flux unidirectionnel (Bulletproof React)

```
shared (components/, hooks/, lib/, types/, contexts/)
         |
         v
features/ (papers/, chat/, tags/, search/, graph/, insights/, upload/)
         |
         v
app/ (routes/, router.tsx, provider.tsx)
```

**Regles d'import :**
- `shared` ne peut PAS importer de `features/` ou `app/`
- `features/X` ne peut PAS importer de `features/Y` ou `app/`
- `app/routes/` compose les features ensemble (c'est le seul endroit ou les features se rencontrent)

**Enforcement ESLint :**

```javascript
// eslint.config.js
'import/no-restricted-paths': ['error', {
  zones: [
    // shared cannot import features or app
    { target: ['./src/components', './src/hooks', './src/lib', './src/types', './src/contexts'],
      from: ['./src/features', './src/app'] },
    // features cannot import app
    { target: './src/features', from: './src/app' },
    // cross-feature imports blocked
    { target: './src/features/papers', from: './src/features', except: ['./papers'] },
    { target: './src/features/upload', from: './src/features', except: ['./upload'] },
    { target: './src/features/tags', from: './src/features', except: ['./tags'] },
    { target: './src/features/chat', from: './src/features', except: ['./chat'] },
    { target: './src/features/search', from: './src/features', except: ['./search'] },
    { target: './src/features/graph', from: './src/features', except: ['./graph'] },
    { target: './src/features/insights', from: './src/features', except: ['./insights'] },
  ]
}]
```

### 4.3 Pas de barrel files

**Interdit :** pas de `index.ts` re-exports dans les features.

**Imports directs :**
```typescript
// BON
import { usePapers } from '@/features/papers/api/get-papers'
import { PaperCard } from '@/features/papers/components/paper-card'

// INTERDIT
import { usePapers, PaperCard } from '@/features/papers'
```

### 4.4 Naming conventions

- Fichiers et dossiers : **kebab-case** (`paper-card.tsx`, `use-debounce.ts`)
- Composants React : **PascalCase** (`PaperCard`, `UploadZone`)
- Hooks : **camelCase** avec prefix `use` (`usePapers`, `useChatStream`)
- Types : **PascalCase** (`Paper`, `PaperFilters`, `ChatMessage`)
- Constants : **UPPER_SNAKE_CASE** (`API_BASE_URL`)

Enforcement via ESLint `check-file/filename-naming-convention`.

### 4.5 Absolute imports

```json
// tsconfig.json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
  }
}
```

Tous les imports utilisent `@/` : `import { Button } from '@/components/ui/button'`

---

## 5. Implementation Patterns

### 5.1 API Client (ky)

```typescript
// lib/api-client.ts
import ky from 'ky'
import { toast } from 'sonner'
import { env } from '@/config/env'

export const apiClient = ky.create({
  prefixUrl: env.API_BASE_URL,
  hooks: {
    afterResponse: [
      async (_request, _options, response) => {
        if (!response.ok) {
          const body = await response.json() as { error: { code: string; message: string } }
          toast.error(body.error.message)
          throw new ApiError(body.error.code, body.error.message, response.status)
        }
      },
    ],
  },
})

export class ApiError extends Error {
  constructor(
    public code: string,
    public override message: string,
    public status: number,
  ) {
    super(message)
  }
}
```

### 5.2 TanStack Query — pattern par endpoint

**Query (lecture) :**
```typescript
// features/papers/api/get-papers.ts
import { apiClient } from '@/lib/api-client'
import { useQuery } from '@tanstack/react-query'
import type { Paper, PaperFilters } from '@/features/papers/types/paper'

type PapersResponse = { items: Paper[]; total: number }

const getPapers = (filters: PaperFilters): Promise<PapersResponse> =>
  apiClient.get('api/papers', { searchParams: filters }).json()

export const papersQueryKey = (filters: PaperFilters) => ['papers', filters] as const

export const usePapers = (filters: PaperFilters) =>
  useQuery({
    queryKey: papersQueryKey(filters),
    queryFn: () => getPapers(filters),
  })
```

**Mutation (ecriture) :**
```typescript
// features/papers/api/delete-paper.ts
import { apiClient } from '@/lib/api-client'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

const deletePaper = (id: string): Promise<void> =>
  apiClient.delete(`api/papers/${id}`).then(() => undefined)

export const useDeletePaper = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deletePaper,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['papers'] })
      toast.success('Paper supprime')
    },
  })
}
```

**Regles TanStack Query :**
- Un fichier par endpoint (types + fetcher + hook)
- Exporter les queryKeys pour l'invalidation croisee
- Jamais appeler le fetcher directement dans un composant — toujours via le hook
- Mutations invalident les queries liees via `queryClient.invalidateQueries`
- Prefetch via `queryClient.prefetchQuery` au hover des PaperCards

### 5.3 SSE — Processing Events

```typescript
// features/papers/hooks/use-processing-events.ts
import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { env } from '@/config/env'

export const useProcessingEvents = (paperId: string, enabled: boolean) => {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!enabled) return

    const source = new EventSource(
      `${env.API_BASE_URL}/api/papers/${paperId}/status`
    )

    source.onmessage = (event) => {
      const data = JSON.parse(event.data)
      // Update local state for progress bar
      // When done, invalidate paper cache
      if (data.step === 'done') {
        queryClient.invalidateQueries({ queryKey: ['paper', paperId] })
        queryClient.invalidateQueries({ queryKey: ['papers'] })
        toast.success('Paper traite avec succes')
        source.close()
      }
      if (data.step === 'error') {
        toast.error('Erreur de traitement')
        source.close()
      }
    }

    return () => source.close()
  }, [paperId, enabled, queryClient])
}
```

### 5.4 SSE — Chat Streaming

```typescript
// features/chat/hooks/use-chat-stream.ts
import { useCallback, useState } from 'react'
import { env } from '@/config/env'

export const useChatStream = (paperId: string) => {
  const [streamingText, setStreamingText] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)

  const sendMessage = useCallback(async (
    content: string,
    scope: 'paper' | 'corpus',
    sessionId?: number,
  ) => {
    setIsStreaming(true)
    setStreamingText('')

    const response = await fetch(
      `${env.API_BASE_URL}/api/papers/${paperId}/chat`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, scope, session_id: sessionId }),
      },
    )

    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let accumulated = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const chunk = decoder.decode(value, { stream: true })
      // Parse SSE format: "data: {...}\n\n"
      const lines = chunk.split('\n')
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = JSON.parse(line.slice(6))
          if (data.text) {
            accumulated += data.text
            setStreamingText(accumulated)
          }
        }
      }
    }

    setIsStreaming(false)
    return accumulated
  }, [paperId])

  return { sendMessage, streamingText, isStreaming }
}
```

### 5.5 React Context — Theme

```typescript
// contexts/theme-context.tsx
import { createContext, useContext, useEffect, useState } from 'react'

type Theme = 'light' | 'dark' | 'system'

const ThemeContext = createContext<{
  theme: Theme
  setTheme: (theme: Theme) => void
  resolved: 'light' | 'dark'
} | null>(null)

export const ThemeProvider = ({ children }: { children: React.ReactNode }) => {
  const [theme, setTheme] = useState<Theme>(() =>
    (localStorage.getItem('theme') as Theme) || 'system'
  )

  const resolved = theme === 'system'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    : theme

  useEffect(() => {
    document.documentElement.classList.toggle('dark', resolved === 'dark')
    localStorage.setItem('theme', theme)
  }, [theme, resolved])

  return (
    <ThemeContext.Provider value={{ theme, setTheme, resolved }}>
      {children}
    </ThemeContext.Provider>
  )
}

export const useTheme = () => {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}
```

### 5.6 URL State — Paper Filters

```typescript
// features/papers/hooks/use-paper-filters.ts
import { useSearchParams } from 'react-router-dom'
import { useMemo } from 'react'
import type { PaperFilters } from '@/features/papers/types/paper'

export const usePaperFilters = () => {
  const [searchParams, setSearchParams] = useSearchParams()

  const filters: PaperFilters = useMemo(() => ({
    tags: searchParams.get('tags')?.split(',').map(Number) ?? [],
    status: searchParams.get('status') ?? undefined,
    q: searchParams.get('q') ?? undefined,
    mode: (searchParams.get('mode') as 'exact' | 'semantic') ?? 'exact',
    sort: searchParams.get('sort') ?? 'created_at',
    order: (searchParams.get('order') as 'asc' | 'desc') ?? 'desc',
  }), [searchParams])

  const setFilter = (key: string, value: string | undefined) => {
    setSearchParams(prev => {
      if (value) prev.set(key, value)
      else prev.delete(key)
      return prev
    })
  }

  return { filters, setFilter, setSearchParams }
}
```

### 5.7 Error Boundaries — placement

```typescript
// app/router.tsx
import { lazy, Suspense } from 'react'
import { createBrowserRouter } from 'react-router-dom'
import { ErrorBoundary } from '@/components/errors/error-boundary'
import { LoadingSkeleton } from '@/components/feedback/loading-skeleton'
import { AppLayout } from '@/components/layouts/app-layout'

const PapersPage = lazy(() => import('./routes/papers'))
const PaperDetailPage = lazy(() => import('./routes/paper-detail'))
const TagsPage = lazy(() => import('./routes/tags'))
const SearchPage = lazy(() => import('./routes/search'))
const GraphPage = lazy(() => import('./routes/graph'))
const InsightsPage = lazy(() => import('./routes/insights'))

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    errorElement: <ErrorBoundary />,  // Global fallback
    children: [
      {
        path: '/',
        element: (
          <ErrorBoundary>
            <Suspense fallback={<LoadingSkeleton variant="page" />}>
              <PapersPage />
            </Suspense>
          </ErrorBoundary>
        ),
      },
      {
        path: '/papers/:id',
        element: (
          <ErrorBoundary>  {/* Isole react-pdf crash */}
            <Suspense fallback={<LoadingSkeleton variant="page" />}>
              <PaperDetailPage />
            </Suspense>
          </ErrorBoundary>
        ),
      },
      {
        path: '/tags',
        element: (
          <ErrorBoundary>
            <Suspense fallback={<LoadingSkeleton variant="page" />}>
              <TagsPage />
            </Suspense>
          </ErrorBoundary>
        ),
      },
      // ... search, graph, insights with same pattern
    ],
  },
])
```

### 5.8 Prefetch on Hover

```typescript
// features/papers/components/paper-card.tsx
import { useQueryClient } from '@tanstack/react-query'
import { getPaper } from '@/features/papers/api/get-paper'

export const PaperCard = ({ paper }: { paper: Paper }) => {
  const queryClient = useQueryClient()

  return (
    <Link
      to={`/papers/${paper.id}`}
      onMouseEnter={() => {
        queryClient.prefetchQuery({
          queryKey: ['paper', paper.id],
          queryFn: () => getPaper(paper.id),
          staleTime: 30_000,
        })
      }}
    >
      {/* card content */}
    </Link>
  )
}
```

### 5.9 3rd Party Wrappers

Les libs tierces lourdes sont wrappees dans des composants feature-specific :

| Lib tierce | Wrapper | Feature | Raison |
|------------|---------|---------|--------|
| react-pdf | `pdf-viewer.tsx` | papers | Config workers, gestion pages, zoom |
| Cytoscape.js | `knowledge-graph.tsx` | graph | Adapter events React, cleanup, styling |
| react-markdown | `markdown-viewer.tsx` | papers | Config remark plugins, composants custom |

Shadcn/ui n'est PAS wrappe (c'est deja du code local dans `components/ui/`).

### 5.10 Composition dans les routes

Les routes composent les features. Exemple `paper-detail.tsx` :

```typescript
// app/routes/paper-detail.tsx
// Compose: features/papers + features/chat + features/tags
import { usePaper } from '@/features/papers/api/get-paper'
import { useCrossrefs } from '@/features/papers/api/get-crossrefs'
import { PdfViewer } from '@/features/papers/components/pdf-viewer'
import { MarkdownViewer } from '@/features/papers/components/markdown-viewer'
import { SummaryPanel } from '@/features/papers/components/summary-panel'
import { ProcessingStatus } from '@/features/papers/components/processing-status'
import { ChatDrawer } from '@/features/chat/components/chat-drawer'
import { TagBadge } from '@/features/tags/components/tag-badge'

export default function PaperDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { data: paper, isLoading, error } = usePaper(id!)
  // ... compose the features together
}
```

C'est le **seul** endroit ou `features/papers` et `features/chat` sont importes ensemble.

---

## 6. Design (Pencil MCP)

### Principes

Le design est structure en **deux fichiers `.pen`** :

1. **`synapseai/design-system.pen`** — Library de composants reutilisables (= design system)
2. **`synapseai/design.pen`** — Pages/frames qui importent les composants depuis la library

Regles strictes :

1. **Composants d'abord** : chaque element d'UI est un composant reutilisable (`reusable: true`) dans `design-system.pen`, nomme exactement comme le futur composant React (ex: `PaperCard`, `TagBadge`, `UploadZone`)
2. **Frames = assemblage pur** : les frames de pages dans `design.pen` n'utilisent **que** des instances de composants reutilisables (`type: "ref"`). Aucun element one-off dans les frames.
3. **Light + Dark obligatoire** : chaque composant et chaque page doit fonctionner dans les deux themes.
4. **Desktop-first** : frames desktop (1440px) seulement. Mobile deprioritise.

### Theme System

**Theme axes** (definis au document root de chaque `.pen`) :

```json
{
  "themes": {
    "mode": ["light", "dark"]
  }
}
```

**Variables bimodales** — chaque variable de couleur a une valeur light ET dark :

```json
{
  "color.background": {
    "type": "color",
    "value": [
      { "value": "#FFFFFF", "theme": { "mode": "light" } },
      { "value": "#0A0A0A", "theme": { "mode": "dark" } }
    ]
  }
}
```

**Correspondance Pencil -> Tailwind CSS** — les variables Pencil sont exportees vers `globals.css` :

| Variable Pencil | CSS Variable (globals.css) | Usage Tailwind |
|-----------------|---------------------------|----------------|
| `$color.background` | `--background` | `bg-[var(--background)]` |
| `$color.foreground` | `--foreground` | `text-[var(--foreground)]` |
| `$color.primary` | `--primary` | `bg-[var(--primary)]` |
| `$color.muted` | `--muted` | `text-[var(--muted)]` |
| `$color.border` | `--border` | `border-[var(--border)]` |
| `$color.card` | `--card` | `bg-[var(--card)]` |
| `$color.destructive` | `--destructive` | `bg-[var(--destructive)]` |
| `$color.success` | `--color-success` | `bg-[var(--color-success)]` |
| `$color.warning` | `--color-warning` | `bg-[var(--color-warning)]` |
| `$color.tag.subdomain` | `--tag-subdomain` | `bg-[var(--tag-subdomain)]` |
| `$color.tag.technique` | `--tag-technique` | `bg-[var(--tag-technique)]` |
| `$color.tag.pathology` | `--tag-pathology` | `bg-[var(--tag-pathology)]` |
| `$color.tag.topic` | `--tag-topic` | `bg-[var(--tag-topic)]` |

Le CSS utilise Tailwind v4 (`@import "tailwindcss"`) avec les variables dans `:root` (light) et `.dark` (dark).

### Style de base

- **Style Pencil** : Editorial Scientific
- **Palette dark** : Deep Space Neon (`surface.primary: #0A0A0A`, `accent.primary: #A855F7`)
- **Palette light** : inverse coherent — fond blanc, meme accent violet
- **Typographie** : headings=Inter, body=Inter, captions=Geist Mono
- **Roundness** : Basic Roundness (none=0, md=6, full=9999)
- **Elevation** : Soft Lift
- **Icons** : Lucide (coherent avec Shadcn/ui)

### Phase 10 : Design System — Library (`design-system.pen`)

**10a — Setup Library + Variables**

- [ ] Creer `synapseai/design-system.pen`
- [ ] Definir le theme axis `mode: ["light", "dark"]`
- [ ] Configurer les variables de couleur bimodales (light + dark) :
  - Couleurs de surface : background, foreground, card, muted, border
  - Couleurs d'accent : primary, secondary, destructive
  - Couleurs semantiques : success, warning, error, info
  - Couleurs de tags : tag.subdomain (indigo), tag.technique (violet), tag.pathology (rose), tag.topic (emerald)
  - Couleurs de status : status.idle (neutral), status.processing (amber), status.done (success), status.error (destructive)
- [ ] Configurer les variables de typographie : font-primary (Inter), font-secondary (Inter), font-mono (Geist Mono)
- [ ] Configurer les variables de spacing, border-radius, shadows

**10b — Composants atomiques reutilisables**

Chaque composant cree avec `reusable: true`, utilisant les variables `$color.*` pour supporter light/dark automatiquement.

- [ ] `Button` : variantes Primary, Secondary, Outline, Ghost, Destructive
- [ ] `Input` : champ texte standard + variante search
- [ ] `TagBadge` : badge colore par categorie (4 variantes : sub_domain, technique, pathology, topic)
- [ ] `StatusBadge` : badge par status (idle, processing, done, error)
- [ ] `SearchBar` : input + toggle "Exact" / "Intelligent" (deux etats)
- [ ] `ProcessingStatus` : barre de progression avec label d'etape
- [ ] `MetricCard` : carte stat (label + valeur numerique)
- [ ] `EmptyState` : placeholder pour les etats vides (icone + message + CTA optionnel)
- [ ] `SkeletonCard` : placeholder de chargement (forme animee)

**10c — Composants composites reutilisables**

- [ ] `PaperCard` : carte paper (titre, auteurs_short, date, TagBadge[], StatusBadge)
- [ ] `UploadZone` : zone drag & drop (etats: idle, hover/dragover, uploading, done, error)
- [ ] `SummaryPanel` : sections Short / Detailed / Findings avec contenu collapsible
- [ ] `ChatMessage` : bulle message (variantes user / assistant, avec markdown placeholder)
- [ ] `ChatDrawer` : drawer complet (header toggle scope, zone messages, input en bas)
- [ ] `InsightCard` : carte insight (icone type, titre, confidence badge, papers liens, rating)
- [ ] `SidebarNav` : sidebar navigation desktop avec icones Lucide + labels (Papers, Tags, Search, Graph, Insights)
- [ ] `PdfViewer` : placeholder viewer PDF (toolbar + zone contenu)
- [ ] `KnowledgeGraph` : placeholder graphe (zone avec noeuds/aretes schematises)

### Phase 11 : Frames Pages (`design.pen`)

Chaque page a **une frame desktop (1440px)**. Chaque frame est dupliquee en light et dark via `theme: { mode: "light" }` / `theme: { mode: "dark" }`.

Les frames utilisent **exclusivement** des `type: "ref"` vers les composants de `design-system.pen`.

**Pages desktop (1440px) :**

- [ ] **PapersPage** : SidebarNav + UploadZone + Filters bar + grille PaperCard
- [ ] **PaperDetailPage** : SidebarNav + layout 60/40 (PdfViewer | SummaryPanel) + ChatDrawer (overlay)
- [ ] **TagsPage** : SidebarNav + Tags groupes par categorie (4 colonnes)
- [ ] **SearchPage** : SidebarNav + SearchBar + resultats PaperCard avec extraits
- [ ] **GraphPage** : SidebarNav + Toolbar filtres + KnowledgeGraph plein ecran
- [ ] **InsightsPage** : SidebarNav + filtres + grille InsightCard groupees par type

**Themes :**

- [ ] Dupliquer chaque frame en variante light (`theme: { mode: "light" }`) et dark (`theme: { mode: "dark" }`)
- [ ] Verifier le contraste et la lisibilite dans les deux modes

**Etats speciaux :**

- [ ] Empty states : pas de papers, pas de resultats, pas d'insights (utilise composant `EmptyState`)
- [ ] Loading states : `SkeletonCard` en remplacement des PaperCard/InsightCard pendant le chargement
- [ ] Error states : messages inline avec couleur `$color.destructive`
- [ ] Processing state sur PaperDetailPage : skeleton summaries + ProcessingStatus

---

## 7. Frontend Implementation

### Phase 12 : Setup React + Tooling

- [ ] Scaffold `synapseai/web/` avec Vite + React + TypeScript
- [ ] Configurer `tsconfig.json` : strict mode, paths `@/*`
- [ ] Configurer `vite.config.ts` : alias `@/` → `./src/`
- [ ] Installer et configurer Tailwind CSS v4 + `globals.css` (variables light/dark)
- [ ] Installer et configurer Shadcn/ui (`components.json`, `components/ui/`)
- [ ] Installer Sonner (toasts)
- [ ] Installer et configurer ESLint flat config :
  - `check-file/filename-naming-convention` (kebab-case)
  - `check-file/folder-naming-convention` (kebab-case)
  - `import/no-restricted-paths` (unidirectional + cross-feature)
- [ ] Installer et configurer Prettier
- [ ] Installer et configurer Husky + lint-staged (pre-commit: lint + format + typecheck)
- [ ] Configurer `vitest.config.ts` avec setup MSW
- [ ] Configurer `playwright.config.ts`
- [ ] Creer `src/config/env.ts` (API_BASE_URL)
- [ ] Creer `src/lib/utils.ts` (cn helper)
- [ ] Creer `Dockerfile` (Node 20, npm install, build, serve)
- [ ] Ajouter service `web` dans `synapseai/docker-compose.yml`

### Phase 13 : Shared Infrastructure

- [ ] Creer `src/types/api.ts` : `ApiError`, `PaginatedResponse<T>`
- [ ] Creer `src/types/common.ts` : `UUID`, `Timestamp`, `StatusType`
- [ ] Creer `src/lib/api-client.ts` : instance ky + error interceptor + toast
- [ ] Creer `src/lib/query-client.ts` : QueryClient config (staleTime, retry, gcTime)
- [ ] Creer `src/contexts/theme-context.tsx` : ThemeProvider (system/light/dark + localStorage)
- [ ] Creer `src/hooks/use-debounce.ts`
- [ ] Creer `src/hooks/use-media-query.ts`
- [ ] Creer `src/components/errors/error-boundary.tsx` : wrapper reutilisable
- [ ] Creer `src/components/errors/error-fallback.tsx` : UI retry
- [ ] Creer `src/components/errors/not-found.tsx`
- [ ] Creer `src/components/feedback/loading-skeleton.tsx` : variantes card, list, page
- [ ] Creer `src/components/feedback/empty-state.tsx` : icon + message + CTA
- [ ] Creer `src/app/provider.tsx` : QueryClientProvider + ThemeProvider
- [ ] Creer `src/app/router.tsx` : routes + lazy imports + Suspense + ErrorBoundaries
- [ ] Creer `src/app/app.tsx` : RouterProvider
- [ ] Creer `src/main.tsx` : entry point (render App)

**Tests Phase 13 :**
- [ ] Test api-client : interceptor erreur, toast on error
- [ ] Test theme-context : toggle, persistence localStorage, system detection
- [ ] Test error-boundary : render fallback on error, retry

### Phase 14 : Feature Papers (types + API + composants)

- [ ] Creer `src/features/papers/types/paper.ts` : Paper, PaperStatus, PaperFilters, PaperSort
- [ ] Creer `src/features/papers/api/get-papers.ts` : types + fetcher + usePapers(filters)
- [ ] Creer `src/features/papers/api/get-paper.ts` : fetcher + usePaper(id)
- [ ] Creer `src/features/papers/api/get-paper-file.ts` : getPaperFileUrl(id)
- [ ] Creer `src/features/papers/api/get-crossrefs.ts` : fetcher + useCrossrefs(id)
- [ ] Creer `src/features/papers/api/update-paper.ts` : useUpdatePaper() + invalidation
- [ ] Creer `src/features/papers/api/delete-paper.ts` : useDeletePaper() + invalidation
- [ ] Creer `src/features/papers/hooks/use-paper-filters.ts` : URL state (searchParams)
- [ ] Creer `src/features/papers/hooks/use-processing-events.ts` : SSE EventSource + TQ invalidation
- [ ] Creer `src/features/papers/components/paper-card.tsx` : carte + prefetch on hover
- [ ] Creer `src/features/papers/components/paper-list.tsx` : composition PaperCard
- [ ] Creer `src/features/papers/components/paper-metadata.tsx` : titre, auteurs, tags, dates
- [ ] Creer `src/features/papers/components/paper-filters.tsx` : tag filter + status + date + search
- [ ] Creer `src/features/papers/components/pdf-viewer.tsx` : react-pdf wrapper (zoom, pages, toolbar)
- [ ] Creer `src/features/papers/components/markdown-viewer.tsx` : react-markdown + remark-gfm
- [ ] Creer `src/features/papers/components/summary-panel.tsx` : short/detailed/findings sections
- [ ] Creer `src/features/papers/components/processing-status.tsx` : progress bar + step label

**Tests Phase 14 :**
- [ ] MSW handlers papers : GET /api/papers, GET /api/papers/:id, DELETE, PATCH
- [ ] Factory : createPaper(), createPaperList()
- [ ] Test paper-list : renders papers, loading skeleton, empty state
- [ ] Test paper-filters : URL state persistence, filter application
- [ ] Test paper-card : renders metadata, navigates on click
- [ ] Test summary-panel : renders summaries, collapsible sections
- [ ] Test processing-status : progress bar updates via SSE mock

### Phase 15 : Feature Upload

- [ ] Creer `src/features/upload/types/upload.ts` : UploadMode ('pdf' | 'url' | 'doi'), UploadState
- [ ] Creer `src/features/upload/api/upload-paper.ts` : useUploadPaper() multipart + JSON
- [ ] Creer `src/features/upload/components/upload-zone.tsx` : drag&drop + URL/DOI input + etats
- [ ] Creer `src/features/upload/components/upload-progress.tsx` : feedback pendant upload

**Tests Phase 15 :**
- [ ] MSW handler : POST /api/papers (multipart + JSON)
- [ ] Test upload-zone : drag&drop interaction, URL input, DOI validation
- [ ] Test upload flow : upload → processing status → paper apparait dans la liste

### Phase 16 : Feature Tags

- [ ] Creer `src/features/tags/types/tag.ts` : Tag, TagCategory, TagMergeRequest
- [ ] Creer `src/features/tags/api/get-tags.ts` : useTags()
- [ ] Creer `src/features/tags/api/get-tag-papers.ts` : useTagPapers(tagId)
- [ ] Creer `src/features/tags/api/rename-tag.ts` : useRenameTag() + invalidation
- [ ] Creer `src/features/tags/api/merge-tags.ts` : useMergeTags() + invalidation
- [ ] Creer `src/features/tags/api/delete-tag.ts` : useDeleteTag() + invalidation
- [ ] Creer `src/features/tags/components/tag-badge.tsx` : badge colore par categorie
- [ ] Creer `src/features/tags/components/tag-filter.tsx` : multi-select par categorie
- [ ] Creer `src/features/tags/components/tag-list.tsx` : liste groupee par categorie + actions
- [ ] Creer `src/features/tags/components/tag-merge-dialog.tsx` : dialog merge 2 tags

**Tests Phase 16 :**
- [ ] MSW handlers tags : GET, PATCH, DELETE, POST merge
- [ ] Factory : createTag(), createTagList()
- [ ] Test tag-list : renders grouped by category, click navigates
- [ ] Test tag-badge : correct color per category
- [ ] Test rename/merge/delete flows

### Phase 17 : Feature Chat

- [ ] Creer `src/features/chat/types/chat.ts` : ChatSession, ChatMessage, ChatScope
- [ ] Creer `src/features/chat/api/get-sessions.ts` : useChatSessions(paperId)
- [ ] Creer `src/features/chat/api/get-messages.ts` : useChatMessages(sessionId)
- [ ] Creer `src/features/chat/api/send-message.ts` : POST body definition
- [ ] Creer `src/features/chat/hooks/use-chat-stream.ts` : fetch + ReadableStream SSE
- [ ] Creer `src/features/chat/components/chat-drawer.tsx` : Sheet/drawer overlay
- [ ] Creer `src/features/chat/components/chat-message.tsx` : bulle user/assistant + markdown
- [ ] Creer `src/features/chat/components/chat-input.tsx` : textarea + send button + Enter shortcut
- [ ] Creer `src/features/chat/components/scope-toggle.tsx` : "Ce paper" / "Tout le corpus"
- [ ] Creer `src/features/chat/components/session-selector.tsx` : dropdown sessions

**Tests Phase 17 :**
- [ ] MSW handlers chat : POST chat, GET sessions, GET messages
- [ ] Test chat-drawer : opens/closes, sends message
- [ ] Test chat-message : renders markdown, user/assistant variants
- [ ] Test streaming : progressive text display (mock ReadableStream)
- [ ] Test scope-toggle : switches between paper and corpus

### Phase 18 : Feature Search

- [ ] Creer `src/features/search/types/search.ts` : SearchMode, SearchRequest, SearchResult
- [ ] Creer `src/features/search/api/search-papers.ts` : useSearchPapers(query, mode)
- [ ] Creer `src/features/search/api/get-similar.ts` : useSimilarPapers(paperId)
- [ ] Creer `src/features/search/components/search-bar.tsx` : input + mode toggle
- [ ] Creer `src/features/search/components/search-results.tsx` : liste resultats + scores
- [ ] Creer `src/features/search/components/search-mode-toggle.tsx` : "Exact" / "Intelligent"
- [ ] Creer `src/features/search/components/search-highlight.tsx` : surlignage passage pertinent

**Tests Phase 18 :**
- [ ] MSW handlers search : POST /api/search, GET /api/search/similar/:id
- [ ] Test search flow : type query → debounce → results displayed
- [ ] Test mode toggle : switch between exact/semantic
- [ ] Test search-highlight : correct highlighting

### Phase 19 : Feature Graph

- [ ] Creer `src/features/graph/types/graph.ts` : GraphData, GraphNode, GraphEdge, GraphFilters
- [ ] Creer `src/features/graph/api/get-graph.ts` : useGraph(filters)
- [ ] Creer `src/features/graph/api/get-paper-graph.ts` : usePaperGraph(paperId)
- [ ] Creer `src/features/graph/components/knowledge-graph.tsx` : Cytoscape.js wrapper
  - Config : cose layout, node sizing, edge styling
  - Events : click → navigate, hover → tooltip
  - Cleanup : destroy cy instance on unmount
- [ ] Creer `src/features/graph/components/graph-toolbar.tsx` : dropdowns filtres
  - Tags multi-select
  - Relation type checkboxes
  - Force minimum select
  - Date range
  - Reset button
- [ ] Creer `src/features/graph/components/graph-tooltip.tsx` : tooltip hover
- [ ] Creer `src/features/graph/hooks/use-graph-filters.ts` : state local filtres

**Tests Phase 19 :**
- [ ] MSW handlers graph : GET /api/graph, GET /api/graph/paper/:id
- [ ] Factory : createGraphData()
- [ ] Test graph-toolbar : filter interactions
- [ ] Test knowledge-graph : renders without crash, click navigates
- [ ] Test empty graph : shows empty message

### Phase 20 : Feature Insights

- [ ] Creer `src/features/insights/types/insight.ts` : Insight, InsightType, InsightConfidence
- [ ] Creer `src/features/insights/api/get-insights.ts` : useInsights(filters)
- [ ] Creer `src/features/insights/api/get-insight.ts` : useInsight(id)
- [ ] Creer `src/features/insights/api/rate-insight.ts` : useRateInsight() optimistic update
- [ ] Creer `src/features/insights/components/insight-card.tsx` : carte + rating + papers links
- [ ] Creer `src/features/insights/components/insight-list.tsx` : groupees par type
- [ ] Creer `src/features/insights/components/insight-filters.tsx` : type + confidence + tags

**Tests Phase 20 :**
- [ ] MSW handlers insights : GET /api/insights, GET /api/insights/:id, PATCH rate
- [ ] Factory : createInsight()
- [ ] Test insight-card : renders, rating click, paper links
- [ ] Test insight-list : grouped by type, filters applied
- [ ] Test optimistic rating : UI updates immediately, rollback on error

### Phase 21 : Route Pages (composition)

- [ ] Creer `src/app/routes/papers.tsx` : compose upload + papers + tags (filtres)
- [ ] Creer `src/app/routes/paper-detail.tsx` : compose papers (viewer/summaries) + chat (drawer) + tags
- [ ] Creer `src/app/routes/tags.tsx` : compose tags (taxonomie)
- [ ] Creer `src/app/routes/search.tsx` : compose search + papers (resultats) + tags
- [ ] Creer `src/app/routes/graph.tsx` : compose graph + tags (filtres toolbar)
- [ ] Creer `src/app/routes/insights.tsx` : compose insights + tags
- [ ] Creer `src/components/layouts/app-layout.tsx` : sidebar + Outlet + Sonner Toaster
- [ ] Creer `src/components/layouts/sidebar-nav.tsx` : nav items + processing badge

**Tests Phase 21 :**
- [ ] Test PapersPage integration : upload → paper appears → filter works
- [ ] Test PaperDetailPage integration : viewer + summaries + chat drawer
- [ ] Test navigation : sidebar links work, back button works

### Phase 22 : E2E Tests (Playwright)

- [ ] Setup Playwright config (base URL, browser config)
- [ ] E2E : Upload a PDF → processing completes → paper visible in list
- [ ] E2E : Navigate to paper detail → view summaries → open chat → send message
- [ ] E2E : Search for a paper → click result → lands on detail
- [ ] E2E : Navigate to graph → see nodes → click node → lands on detail
- [ ] E2E : Navigate to insights → see cards → rate an insight
- [ ] E2E : Tags page → rename a tag → verify update

---

## 8. Execution Plan

### Sprint 4 — Design (Phase 10-11)
- [ ] Design system library (`design-system.pen`)
- [ ] Composants reutilisables Pencil
- [ ] Frames pages desktop, light + dark
- [ ] **Livrable** : design complet pret pour implementation frontend

### Sprint 5 — Frontend Setup + Shared (Phase 12-13)
- [ ] Scaffold Vite + React + TypeScript + Tailwind + Shadcn/ui
- [ ] ESLint (import restrictions, naming), Prettier, Husky
- [ ] API client (ky), QueryClient, ThemeProvider, ErrorBoundaries
- [ ] Vitest + Playwright config + MSW setup
- [ ] **Livrable** : `npm run dev` affiche un shell avec sidebar et theme toggle

### Sprint 6 — Papers + Upload (Phase 14-15)
- [ ] Feature papers : types, API hooks, composants, viewer PDF, summaries
- [ ] Feature upload : drag&drop, URL/DOI, SSE progress
- [ ] Route PapersPage + PaperDetailPage
- [ ] Tests integration papers + upload
- [ ] **Livrable** : on peut uploader un PDF, voir la liste, ouvrir le detail, voir les summaries

### Sprint 7 — Tags + Chat + Search (Phase 16-17-18)
- [ ] Feature tags : CRUD, badges, page taxonomie
- [ ] Feature chat : drawer, streaming SSE, scope toggle, sessions
- [ ] Feature search : barre, exact/semantic, highlight
- [ ] Tests integration tags + chat + search
- [ ] **Livrable** : on peut filtrer par tags, chatter avec un paper, chercher dans le corpus
- [ ] **MVP Frontend atteint**

### Sprint 8 — Graph + Insights (Phase 19-20)
- [ ] Feature graph : Cytoscape.js wrapper, toolbar filtres, interactions
- [ ] Feature insights : cards, groupement, rating optimistic, filtres
- [ ] Tests integration graph + insights
- [ ] **Livrable** : graphe interactif + dashboard insights avec rating

### Sprint 9 — Routes, Layout, E2E (Phase 21-22)
- [ ] Routes pages finales (composition des features)
- [ ] Layout (sidebar, theme, notifications badge)
- [ ] E2E Playwright (6 happy paths)
- [ ] **Livrable** : frontend complet, teste, pret pour production

---

## 9. Notes Importantes

### Project Standards (Bulletproof React)
- Absolute imports `@/*` partout
- Kebab-case pour tous les fichiers et dossiers
- Pas de barrel files (imports directs)
- Flux unidirectionnel enforced par ESLint
- Cross-feature imports interdits (composition dans `app/routes/` uniquement)

### State Management
- **Server cache** : TanStack Query v5 (TOUT ce qui vient du backend)
- **URL state** : react-router searchParams (filtres, search query, mode)
- **App state** : React Context (theme, sidebar)
- **Form state** : react-hook-form + Zod (upload form uniquement)
- **Component state** : useState local (chat input, drawer open, etc.)

### Error Handling
- **API** : interceptor ky → toast.error automatique
- **Render** : ErrorBoundary par route (isole les crashs react-pdf, Cytoscape)
- **TanStack Query** : error states par hook (`isError`, `error`)

### Performance
- **Code splitting** : lazy() sur PaperDetailPage (react-pdf ~500KB), GraphPage (cytoscape ~280KB), InsightsPage
- **Prefetch** : hover PaperCard → prefetch paper detail
- **SSE** : EventSource leger pour processing, fetch+ReadableStream pour chat
- **Children pattern** : AppLayout + Outlet (pas de re-render du layout)
- **Zero-runtime styling** : Tailwind CSS (build-time, pas de CSS-in-JS runtime)
- **TanStack Query** : staleTime + gcTime configurees pour eviter refetch inutiles

### Testing
- **Integration-first** : Vitest + Testing Library + MSW v2
- **MSW handlers** : un fichier par feature, miroir des endpoints backend
- **Factories** : fonctions pour generer des donnees de test realistes
- **renderWithProviders** : wrapper avec QueryClient + Router + Theme pour chaque test
- **E2E** : Playwright, 6 happy paths couvrant les flows principaux
- **Pre-commit** : Husky + lint-staged (lint + format + typecheck)

### Securite
- Pas d'auth en v1 (pas de token storage)
- Sanitization markdown (react-markdown est safe par defaut, pas de dangerouslySetInnerHTML)
- CORS configure cote backend (`http://localhost:5173`)
- Pas de localStorage sensible (juste le theme)

### Compatibilite
- React 19 + TypeScript 5.5+
- Node 20+ (Vite 6)
- Navigateurs modernes (Chrome/Firefox/Safari/Edge derniers 2 versions)

### Dependencies (package.json)
```json
{
  "dependencies": {
    "react": "^19.0",
    "react-dom": "^19.0",
    "react-router-dom": "^7.0",
    "@tanstack/react-query": "^5.0",
    "ky": "^1.7",
    "react-hook-form": "^7.54",
    "@hookform/resolvers": "^3.9",
    "zod": "^3.23",
    "sonner": "^1.7",
    "react-pdf": "^9.0",
    "react-cytoscapejs": "^2.0",
    "cytoscape": "^3.30",
    "cytoscape-cose-bilkent": "^4.1",
    "react-markdown": "^9.0",
    "remark-gfm": "^4.0",
    "clsx": "^2.1",
    "tailwind-merge": "^2.6",
    "lucide-react": "^0.460"
  },
  "devDependencies": {
    "vite": "^6.0",
    "@vitejs/plugin-react": "^4.3",
    "typescript": "^5.5",
    "tailwindcss": "^4.0",
    "@tailwindcss/vite": "^4.0",
    "vitest": "^2.1",
    "@testing-library/react": "^16.1",
    "@testing-library/jest-dom": "^6.6",
    "@testing-library/user-event": "^14.5",
    "msw": "^2.6",
    "@playwright/test": "^1.49",
    "eslint": "^9.0",
    "@eslint/js": "^9.0",
    "typescript-eslint": "^8.0",
    "eslint-plugin-import": "^2.31",
    "eslint-plugin-check-file": "^2.8",
    "prettier": "^3.4",
    "husky": "^9.1",
    "lint-staged": "^15.2"
  }
}
```

### Evolutions futures (hors scope)
- Mobile responsive (Phase ulterieure)
- Storybook (catalogue composants)
- i18n (internationalisation)
- Auth frontend (JWT + protected routes)
- Notifications push (WebSocket)
- Export PDF synthese
- Import batch DOI (CSV)
