# RAG Search Pipeline Architecture

## Overview

The **RAG (Retrieval-Augmented Generation) pipeline** is the knowledge retrieval system that powers Cereal Killer's contextual awareness. It provides the "Brain" engine with relevant, ranked snippets from a local knowledge base before each LLM completion request.

The pipeline operates as a four-stage funnel:

1. **Vector Search (RedisVL)** — Semantic matching via embedding similarity
2. **Lexical Search (Redis FT.SEARCH)** — Keyword/token matching with expansion
3. **Cross-Encoder Reranking** — Hybrid scoring combining lexical overlap and cross-encoder relevance
4. **Diverse Selection** — Source diversification to avoid redundant content

When vector search fails to find confident matches, the pipeline falls back to **SearXNG web search**.

### Files Involved

| File | Lines | Responsibility |
|---|---|---|
| `src/mentor/kb/query.py` | ~778 | Core retrieval, reranking, and snippet selection |
| `src/mentor/engine/search_orchestrator.py` | ~241 | Tiered search orchestration, budget management |
| `src/cereal_killer/knowledge_base.py` | ~234 | Index creation, dataset sync, Redis schema setup |

### Configuration

| Constant / Env Var | Default | Description |
|---|---|---|
| `EMBEDDING_DIMS` | `768` | Sentence-transformers / nomic-embed-text embedding dimension |
| `RAG_NOT_EMPTY_SIMILARITY_THRESHOLD` | `0.40` | Minimum cosine similarity to include results |
| `_RERANK_CANDIDATES` | `10` | Number of candidates kept before final selection |
| `_CONTEXT_CACHE_TURNS` | `3` | How many turns back to check cached results |
| `_CONTEXT_CACHE_TTL_SECS` | `21600` (6 hours) | Cache entry expiration |
| `RAG_RERANKER` | `"on"` | Toggle cross-encoder reranking |
| `RAG_RERANKER_MODEL` | `"BAAI/bge-reranker-base"` | HuggingFace cross-encoder model ID |

---

## Table of Contents

- [Query Pipeline (`query.py`)](#query-pipeline-querypy)
  - [Tier 1: Vector Search (RedisVL)](#tier-1-vector-search-redisvl)
  - [Tier 2: Lexical Search](#tier-2-lexical-search)
  - [Tier 3: Cross-Encoder Reranking](#tier-3-cross-encoder-reranking)
  - [Tier 4: Diverse Selection](#tier-4-diverse-selection)
- [Search Orchestrator (`search_orchestrator.py`)](#search-orchestrator-search_orchestropy)
  - [`tiered_search()` Pipeline](#tiered_search-pipeline)
  - [_best_vector_score()](#best_vector_score)
  - [_snippet_token_cost()](#snippet_token_cost)
  - [_snippet_priority()](#snippet_priority)
  - [_trim_snippets_to_budget()](#trim_snippets_to_budget)
- [Knowledge Base Indexing (`knowledge_base.py`)](#knowledge-base-indexing-knowledge_py)
- [Data Flow Diagram](#data-flow-diagram)

---

## Query Pipeline (`query.py`)

The core retrieval logic lives in `src/mentor/kb/query.py`. It implements a four-stage pipeline that progressively filters and ranks candidate snippets.

### Tier 1: Vector Search (RedisVL)

The first tier performs semantic similarity search using sentence-transformers embeddings (default: nomic-embed-text).

#### `retrieve_reference_material()`

Entry point for the retrieval pipeline. Orchestrates parallel vector and lexical searches:

```python
retrieve_reference_material(
    query: str,
    query_type: str = "default",
    phase: str = "default",
    phase_bucket: str = ""
)
```

**Execution steps:**

1. Calls `_query_single_index()` concurrently for each RedisVL index
2. Calls `_query_single_index_lexical()` in parallel for keyword matches
3. Merges results, deduplicating by `(source, content[:200])`
4. Preserves lexical hits even when they don't meet the similarity threshold
5. Returns a combined result set passed to reranking

#### `_query_single_index()`

Performs the vector similarity search against a single RedisVL index:

1. Embeds the query using a sentence-transformers embedding model (configured via `EMBEDDING_MODEL` env var, default: nomic-embed-text)
2. Runs `VectorQuery` against the RedisVL index
3. Converts Redis cosine distance to similarity: `1 - distance`
4. Filters results below `RAG_NOT_EMPTY_SIMILARITY_THRESHOLD` (0.40)
5. Returns structured snippet objects with source, content, and score

#### Parallel Lexical Query

`_query_single_index_lexical()` runs concurrently with the vector query and handles keyword-based retrieval. Results are merged post-hoc to ensure lexical matches aren't lost when similarity scores are borderline.

---

### Tier 2: Lexical Search

The lexical search tier handles keyword matching with token expansion.

#### Tokenization & Expansion

1. Splits the query into tokens of 2+ characters
2. Expands tokens by:
   - Splitting on separator characters
   - Adding wildcard variants (`term*`)
3. Runs `FT.SEARCH` commands against Redis

#### GTFOBins Special Query

For tool lookups (e.g., `find`, `setuid`), a special exact-title query targets GTFOBins entries. This provides high-precision results for specific command lookups.

---

### Tier 3: Cross-Encoder Reranking

The reranking stage combines multiple signals into a unified relevance score.

#### `_rerank_snippets()`

Merges and reranks candidates from the vector and lexical tiers:

**Composite score components:**

| Component | Description |
|---|---|
| Lexical rerank score | Token overlap between query and snippet |
| Cross-encoder score | Semantic reranking (when `sentence_transformers` available) |
| Phase bonus | Boost for matching `phase_bucket` context |
| Source bonus | GTFOBins boost for `find`/`setuid` queries |
| Cache penalty | Deduplication penalty for results seen in recent turns |

**Recon-heavy filtering:**
In later search phases, snippets marked as "recon-heavy" receive additional scoring penalties to avoid returning large blocks of reconnaissance data when more targeted content is available.

---

### Tier 4: Diverse Selection

The final selection stage ensures the output isn't dominated by a single source.

#### `_select_diverse_snippets()`

```python
_select_diverse_snippets(snippets: list, budget: int) -> list
```

**Selection strategy:**

1. **First pass** — Prefers unique sources (one snippet per source)
2. **Second pass** — Falls back to rank order when more results are needed
3. Enforces the token budget constraint throughout

This prevents any single document from dominating the retrieved context window.

---

## Search Orchestrator (`search_orchestrator.py`)

The orchestrator manages the end-to-end search flow, integrating the query pipeline with token budgeting and web search fallback.

### `tiered_search()` Pipeline

The top-level orchestrator method:

```python
async def tiered_search(
    query: str,
    query_type: str = "default",
    phase: str = "default",
    phase_bucket: str = "",
    ...
) -> dict
```

**Execution steps:**

1. Calls `retrieve_reference_material()` → gets vector + lexical snippets
2. Trims snippets to token budget (`_REFERENCE_TOKEN_BUDGET = 1500`)
3. Calculates the best vector score across results
4. If `best_score < threshold` → triggers SearXNG web search
5. Combines all results into a `reference_block` for the LLM prompt

The orchestrator acts as the bridge between the knowledge base and the Brain engine.

### `_best_vector_score()`

Converts Redis cosine distance to a similarity score:

```
similarity = 1 - cosine_distance
```

This normalized score (0–1 range) is used to determine whether vector search produced confident results or whether web search fallback is needed.

### `_snippet_token_cost()`

Approximates the token cost of a snippet:

```
token_cost ≈ len(text) // 4
```

This rough heuristic is used to stay within the token budget without requiring a full tokenizer.

### `_snippet_priority()`

Ranks individual snippets by a multi-factor priority score:

| Factor | Effect |
|---|---|
| Target match | Priority boost (priority 2 vs baseline 0) |
| Generic detection | Priority reduction for common/low-value terms |
| Inverse token cost | Fewer tokens = higher priority |

### `_trim_snippets_to_budget()`

Sorts snippets by priority and includes them until the token budget is exhausted:

```
while total_token_cost <= _REFERENCE_TOKEN_BUDGET:
    add next highest-priority snippet
```

This ensures the context window stays within limits while maximizing information density.

---

## Knowledge Base Indexing (`knowledge_base.py`)

The knowledge base module manages the Redis-backed index lifecycle.

### Responsibilities

- **Index creation** — Sets up RedisVL schema and index definitions
- **Dataset sync** — Loads knowledge base documents into Redis
- **Schema management** — Defines vector, text, and metadata fields

### Key Concepts

| Concept | Detail |
|---|---|
| Deterministic embeddings | Hash-based 64-dim vectors for consistent indexing |
| Multi-index support | Separate indexes for different knowledge domains (HackTricks, GTFOBins, etc.) |
| Schema fields | Vector field (distance metric), text field, source metadata, phase tags |

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         RAG Search Pipeline                              │
│                                                                          │
│  User Query                                                              │
│    │                                                                     │
│    ▼                                                                     │
│  ┌──────────────────────┐                                                 │
│  │  search_orchestrator │                                                 │
│  │  tiered_search()     │                                                 │
│  │                      │                                                 │
│  │  1. Call retrieve_   │  ┌──────────────────────────────┐               │
│  │     reference_       │  │  query.py                     │               │
│  │     material()       │  │                                 │               │
│  │                      │  │  Tier 1: Vector Search         │               │
│  │  2. Trim to budget   │  │  - Embed query (hash-based)    │               │
│  │  3. Check score      │  │  - RedisVL query               │               │
│  │  4. Fallback check   │  │  - Threshold: 0.40              │               │
│  │                      │  │                                 │               │
│  │                      │  │  Tier 2: Lexical Search         │               │
│  │                      │  │  - Tokenize & expand             │               │
│  │                      │  │  - FT.SEARCH with wildcards      │               │
│  │                      │  │                                 │               │
│  │                      │  │  Tier 3: Cross-Encoder          │               │
│  │                      │  │  - Hybrid scoring                │               │
│  │                      │  │  - Phase/source bonuses          │               │
│  │                      │  │                                 │               │
│  │                      │  │  Tier 4: Diverse Selection       │               │
│  │                      │  │  - Source diversity               │               │
│  │                      │  │  - Rank-based fallback            │               │
│  │                      │  └──────────────────────────────┘               │
│  │                      │                                                 │
│  │  5. Web fallback if  │  ┌──────────────────────────────┐               │
│  │     needed           │  │  SearXNG Web Search           │               │
│  │                      │  └──────────────────────────────┘               │
│  └──────────────────────┘                                                 │
│    │                                                                     │
│    ▼                                                                     │
│  reference_block → Brain Engine Prompt Assembly                          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Component Relationships

```
Brain Engine (brain.py)
  │
  └─ ask()
       │
       ├─ tiered_search() [search_orchestrator.py]
       │    │
       │    ├─ retrieve_reference_material() [query.py]
       │    │    │
       │    │    ├─ _query_single_index() ── RedisVL
       │    │    ├─ _query_single_index_lexical() ── Redis FT.SEARCH
       │    │    ├─ _rerank_snippets() ── Hybrid scoring
       │    │    └─ _select_diverse_snippets() ── Source diversity
       │    │
       │    ├─ _best_vector_score() ── Cosine → similarity
       │    ├─ _snippet_token_cost() ── Text length → tokens
       │    ├─ _snippet_priority() ── Multi-factor ranking
       │    └─ _trim_snippets_to_budget() ── Budget enforcement
       │         │
       │         └─ (fallback) SearXNG web search
       │
       └─ reference_block → Prompt assembly
```

---

*Document generated from `src/mentor/kb/query.py`, `src/mentor/engine/search_orchestrator.py`, `src/cereal_killer/knowledge_base.py`*
