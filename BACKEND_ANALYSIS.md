# Cereal-Killer Non-TUI Codebase Analysis

## 1. Architecture Overview

### 1.1 Module Hierarchy

The codebase is split into two top-level namespace packages:

| Namespace | Purpose | Key Modules |
|-----------|---------|-------------|
| `cereal_killer` | TUI-facing service layer (non-TUI backend) | `config.py`, `engine.py`, `knowledge_base.py`, `observer/`, `ui/workers/` |
| `mentor` | Core reasoning & coaching engine | `engine/brain.py`, `engine/session.py`, `engine/pedagogy.py`, `engine/search_orchestrator.py`, `kb/query.py`, `observer/stalker.py` |

### 1.2 Module Dependencies (Directed Graph)

```
main.py ──┬──> CerealKillerApp (UI facade)
           ├───> KnowledgeBase (Redis vector DB wrapper)
           └──> LLMEngine ──> mentor.engine.brain.Brain
                                    │
                                    ├── mentor.engine.session.ThinkingSessionStore
                                    ├── mentor.engine.pedagogy.PedagogyEngine
                                    ├── mentor.engine.search_orchestrator.tiered_search
                                    ├── mentor.kb.query.retrieve_reference_material
                                    └── mentor.engine.minifier.minify_terminal_output
```

**Dependency Chain:**

```
cereal_killer/main.py
  └── cereal_killer/engine.py
       └── mentor/engine/brain.py
            ├── mentor/engine/session.py (Redis session store)
            ├── mentor/engine/pedagogy.py (coaching state machine)
            ├── mentor/engine/search_orchestrator.py
            │    └── mentor/kb/query.py (RAG retrieval + embedding)
            ├── mentor/kb/query.py
            ├── mentor/engine/minifier.py
            └── cereal_killer/config.py (Settings)
```

### 1.3 Data Flow: Terminal → Observer → Stalker → Brain → Engine → UI

```
┌─────────────────────────────────────────────────────────────────┐
│                        TERMINAL (zsh/bash)                      │
│  - History file (.zsh_history / .bash_history)                │
│  - Clipboard (images via PIL/ImageGrab)                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │ watchfiles.awatch()
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     STALKER (mentor.observer.stalker)           │
│  - Parses history lines (zsh/fish/bash formats)                │
│  - Filters technical commands (nmap, gobuster, etc.)           │
│  - Detects box CD / host references                             │
│  - Detects feedback signals (404, permission denied)           │
│  - Emits HistoryEvent with context + feedback                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ observe_history_events()
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                 TERMINAL OBSERVER (ui/observers)                │
│  - Receives HistoryEvents from Stalker                         │
│  - Updates history_context, phase tracking                      │
│  - Triggers autocoach on technical commands                     │
│  - Auto-sets /box target on cd command                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ history_context + command
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      BRAIN (mentor.engine.brain)                │
│  - Assembles system prompt + user message                       │
│  - Calls tiered_search() for RAG reference material            │
│  - Manages thinking session buffer (Redis)                      │
│  - Manages pedagogy state (stuck detection)                    │
│  - Calls LLM via OpenAI client or LiteLLM                      │
│  - Parses <thought>...</thought> from LLM response             │
└──────────────────────────┬──────────────────────────────────────┘
                           │ LLMResponse (thought + answer)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  ENGINE (cereal_killer.engine)                  │
│  - Thin wrapper over Brain                                      │
│  - Wraps BrainResponse → LLMResponse                           │
│  - Exposes pedagogy properties (hint_level)                    │
│  - Manages context pruning thresholds                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ LLMResponse
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                 UI WORKERS (ui/workers/)                        │
│  - ChatWorkerManager: receives response, streams thought        │
│  - VisionWorkerManager: processes images                        │
│  - SearchWorkerManager: runs searches                           │
│  - WorkerLifecycleManager: cancellation safety                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              TEXTUAL UI (MainDashboard, screens)                │
│  - Chat display, thought stream, phase indicator                │
│  - Gibson tab (search + results)                                │
│  - Visual buffer (clipboard images)                             │
└─────────────────────────────────────────────────────────────────┘
```

### 1.4 Key Dependencies Between Modules

| Dependency | Direction | Why |
|------------|-----------|-----|
| `cereal_killer.config` | ALL modules | Single source of truth for settings |
| `cereal_killer.engine` | `cereal_killer.ui` | UI workers call engine methods |
| `mentor.engine.brain` | `cereal_killer.engine` | Engine wraps Brain |
| `mentor.engine.search_orchestrator` | `mentor.engine.brain` | Brain calls tiered_search for every chat |
| `mentor.kb.query` | Multiple | Shared RAG retrieval + embedding logic |
| `cereal_killer.observer` | `mentor.observer.stalker` | Re-exports + adds clipboard integration |
| `mentor.engine.pedagogy` | `mentor.engine.brain` | Brain uses PedagogyEngine for hint depth |

---

## 2. Strengths

### 2.1 What Works Well Architecturally

**1. Clear Separation of Concerns**
- `mentor` package is the pure reasoning engine — no UI dependencies
- `cereal_killer` wraps the mentor engine with TUI-specific concerns (workers, lifecycle, clipboard)
- `brain.py` is self-contained: it doesn't import from `cereal_killer` except for `Settings`
- This means the `mentor` package could be extracted to a standalone library

**2. Tiered Search Pipeline**
- The `tiered_search()` orchestrator (search_orchestrator.py) is well-designed:
  1. **Tier 1**: Local Redis vector DB (IppSec + HackTricks + GTFOBins + LOLBAS)
  2. **Tier 2**: SearXNG live web search (when vector similarity is low)
- The `reference_token_budget` management prevents context window overflow
- Source-aware caching (`_load_recent_snippet_fingerprints`) avoids repeating retrievals

**3. Pedagogy State Machine**
- `PedagogyEngine` is a clean, well-documented state machine with three levels:
  - `VAGUE` (0-10 min) → Socratic questions only
  - `CONCEPTUAL` (10-20 min) → Named vulnerability class
  - `DIRECT` (20+ min) → Concrete technical pointer
- Web search is gated behind `DIRECT` level — prevents premature web reliance

**4. Worker Lifecycle Management**
- `WorkerLifecycleManager` provides cancellation safety
- `@work` decorator groups workers to prevent concurrent LLM calls
- `_with_worker_cancellation()` prevents race conditions when multiple workers share state

**5. Session Persistence via Redis**
- `ThinkingSessionStore` persists reasoning chains across sessions
- Mental state is saved/loaded with recon summaries
- User learnings vault is a novel feature for long-term skill building

**6. Claude-style `<thought>...</thought>` Parsing**
- The `parse_brain_output()` function handles multiple response formats:
  - XML-style `<thought>` tags
  - `thought` / `Response:` plain text fallback
  - Quoted response stripping
- This makes the system resilient to different LLM output formats

### 2.2 Technical Debt That's Been Handled Well

**1. Embedding Cache**
- `_embed_cache` with LRU eviction prevents redundant model computations
- Falls back to hash-based embedding when the model is unavailable
- 1000-entry cache is a reasonable size for typical sessions

**2. Cross-Encoder Reranking**
- `CrossEncoder` is loaded lazily and cached globally
- If loading fails, subsequent calls retry (not cached-failed)
- Source-aware bonus scoring (GTFoBins for tool lookups, etc.)

**3. Feedback Loop Guards**
- `_FEEDBACK_COOLDOWN_SECS` prevents rapid-fire brain triggers
- `_triggered_line_hashes` deduplication prevents infinite loops
- Bounded dedup set (200 entries) prevents memory leak

**4. Context Pruning**
- `prune_threshold()` and `prune_target()` calculate from model's max context length
- Transcript condensation triggers after N turns
- Token counting is a lightweight approximation (`len(text) // 4`)

**5. Multi-Provider LLM Support**
- Falls back gracefully between OpenAI client and LiteLLM
- Vision calls use direct httpx client (independent of LLM provider)
- Provider-specific metrics extraction (`_extract_completion_metrics`)

---

## 3. Areas for Growth (Technical)

### 3.1 Performance Bottlenecks

| Bottleneck | Location | Impact |
|------------|----------|--------|
| **Embedding model loaded per query** | `mentor/kb/query.py:_embed_with_cache()` | Each unique query requires a forward pass unless cached. No batch encoding. |
| **Cross-encoder reranking loads top-k candidates every time** | `mentor/kb/query.py:_rerank_snippets()` | For every chat, top-k snippets go through a cross-encoder inference |
| **History file re-parsed on every change** | `mentor/observer/stalker.py:observe_history()` | Full file re-read and re-parse on each watch event |
| **No connection pooling for Redis** | `mentor/kb/query.py` | Each query creates a new Redis client connection |
| **SearXNG search is sequential** | `mentor/engine/search_orchestrator.py:tiered_search()` | Tier 2 web search waits for Tier 1 to complete |

### 3.2 Missing Features (Technical)

**1. Streaming LLM Responses**
- Currently, responses are fully buffered then displayed
- No partial thought streaming to the UI during LLM generation
- Would benefit from server-sent events or WebSocket support

**2. Structured Tool Execution**
- `ingest_logic.py` has basic document ingestion
- No structured command execution pipeline (run nmap → parse output → feed to brain)
- Command execution happens at the UI layer only

**3. Concurrent History Parsing**
- Multiple history files are watched concurrently, but results converge on a single `history_context`
- No deduplication or conflict resolution for concurrent modifications

**4. Batch Embedding**
- Each query embeds individually; no batching for batch queries
- Could process multiple queries in parallel during idle periods

**5. Vector Index Incremental Updates**
- `sync_ippsec_dataset()` always does full replace
- No incremental sync capability for new walkthroughs

### 3.3 Code Organization Opportunities

**1. Duplicate `MainDashboard` Resolution Pattern**
The pattern:
```python
def _dashboard(self):
    active = self._app.screen
    if isinstance(active, MainDashboard):
        return active
    for s in getattr(self._app, "screen_stack", []):
        if isinstance(s, MainDashboard):
            return s
    return None
```
Is repeated in **7+ files** (app.py, workers, observers, command_handler, etc.).
- **Suggestion**: Centralize in `cereal_killer.ui.base` or add to `ContextManager`

**2. Worker Manager Duplication**
Each `*WorkerManager` class follows the same pattern:
```python
class *WorkerManager:
    def __init__(self, app: Any):
        self._app = app
    
    async def _run_xxx_worker(self): ...
    async def _xxx_body(self): ...
```
- **Suggestion**: Extract a base `AsyncWorkerManager` class with shared run/cancel logic

**3. `Any` Type Overuse**
Most `*Manager` classes use `app: Any` which loses type safety:
- `command_handler.py` imports `Any` from typing but never uses it as `Any` (uses it for app)
- `worker_lifecycle.py` uses `Any` for task types
- **Suggestion**: Use `TypeVar` or Protocol-based typing for the app interface

**4. Global State in `mentor.kb.query`**
- `_EMBED_MODEL`, `_CROSS_ENCODER`, `_embed_cache`, `_CROSS_ENCODER_LOAD_ATTEMPTED` are module-level globals
- Not thread-safe (no locking on cache writes)
- **Suggestion**: Wrap in a `RAGContext` class with thread-local storage

### 3.4 Caching Opportunities

| Cache Target | Current | Opportunity |
|--------------|---------|-------------|
| **Embedding model** | Global singleton `_EMBED_MODEL` | Good; no improvement needed |
| **Embedding cache** | In-process dict `_embed_cache` | Add TTL expiration; currently infinite |
| **RAG results** | `_recent_snippet_fingerprints` (Redis list) | Good; implements context cache already |
| **LLM responses** | No cache | Missing — could cache by query hash + system prompt hash |
| **System prompt pins** | `_pinned_system_prompt_by_machine` (Brain instance dict) | Good; clears on prompt addendum change |
| **Cross-encoder** | Global singleton `_CROSS_ENCODER` | Good; lazy loaded, cached |

### 3.5 Concurrency Improvements

**1. Race Condition in `_embed_cache`**
```python
if len(_embed_cache) >= _EMBED_CACHE_SIZE:
    oldest_key = next(iter(_embed_cache))
    del _embed_cache[oldest_key]
_embed_cache[key] = embedding
```
Two concurrent calls could both evict the same oldest key.
- **Fix**: Use `collections.deque` with maxlen or add asyncio.Lock

**2. Concurrent Redis Writes in `append_thought`**
Multiple concurrent appends to the same Redis list are safe (RPUSH is atomic), but the `_invalidate_cache()` call after each append could race.

**3. No Rate Limiting on LLM Calls**
Multiple rapid user inputs could trigger concurrent LLM calls (if workers aren't exclusive).
- **Current mitigation**: `@work(exclusive=True, group="llm")`
- **Gap**: Vision and search workers use separate groups and could run concurrently with chat workers

---

## 4. Areas for Growth (Features)

### 4.1 Missing Coaching Functionality

| Feature | Current State | Opportunity |
|---------|---------------|-------------|
| **Adaptive difficulty** | Static `pathetic_meter` | Add phase-aware difficulty that adjusts based on user's command history success rate |
| **Personalized hints** | Global pedagogy engine | Track per-user weak areas (e.g., "weak at SMB", "strong at Linux privesc") |
| **Hint tree navigation** | Linear VAGUE→CONCEPTUAL→DIRECT | Branching hints based on user's specific situation |
| **Command suggestion** | Only `suggest_tool_upgrade()` | Proactive suggestions: "You ran nmap; consider gobuster next" |
| **Post-session review** | No structured review | Generate a "what you missed" report after session ends |
| **Progressive disclosure** | All hints available at DIRECT level | Reveal hints progressively based on hints already tried |

### 4.2 Integration Opportunities

**1. Live Terminal Proxy**
- Currently observes history files (passive)
- Could proxy stdin/stdout for real-time command interception
- Would enable immediate brain responses (not just on history write)

**2. Multi-Box Session Tracking**
- Currently one `history_context` list for the entire session
- Could maintain per-box context windows
- Would enable switching between boxes without losing progress

**3. Knowledge Base Web Crawler**
- `kb/web_crawler.py` exists but is minimal
- Could integrate with HackTricks, GTFOBins, and custom sites for continuous KB growth

**4. GitHub Actions Integration**
- No CI/CD pipeline for knowledge base updates
- Could auto-sync when new HackTricks articles are published

### 4.3 Extensibility Hooks

| Hook | Current | Enhancement |
|------|---------|-------------|
| **Custom tools** | `TECHNICAL_TOOLS` hardcoded set | Configurable tool list from settings |
| **Custom prompts** | Single `OLDC_ZERO_COOL_PROMPT` | Per-box or per-user system prompt overrides |
| **Custom RAG sources** | Hardcoded index names in `_resolve_index_priority` | Dynamic index registration from settings |
| **Custom feedback detectors** | `detect_feedback_signal` fixed set | Configurable regex patterns |
| **Plugin callbacks** | `on_web_search_state_change` only | Full callback lifecycle (before/after LLM, before/after search, etc.) |

### 4.4 Plugin Potential

The architecture naturally supports these plugins:

1. **Custom Brain Extensions** — Inject additional system prompt blocks or modify responses
2. **Custom RAG Sources** — Add new Redis indices or external knowledge sources
3. **Custom Observers** — Monitor additional event sources (network traffic, etc.)
4. **Custom Pedagogy Levels** — Add new hint depth levels or transition conditions
5. **Custom Workers** — Add new async worker types for specialized tasks

---

## 5. Specific Code Improvements

### 5.1 High-Impact Changes (Do Next)

| Change | Files | Impact |
|--------|-------|--------|
| **Add response caching** | `brain.py`, `engine.py` | Reduce LLM latency for repeated queries |
| **Batch embedding** | `mentor/kb/query.py` | Faster RAG retrieval for multi-query searches |
| **Centralize dashboard resolution** | All `*Manager` classes | Remove ~300 lines of duplicated code |
| **Add streaming support** | `brain.py`, workers | Better perceived latency, interactive thought display |
| **Fix embedding cache thread safety** | `mentor/kb/query.py` | Prevent duplicate evictions in concurrent scenarios |

### 5.2 Medium-Impact Changes

| Change | Files | Impact |
|--------|-------|--------|
| **Add command execution pipeline** | New module | Run commands, capture output, feed to brain |
| **Add per-box context** | `brain.py`, `context_manager.py` | Better multi-box support |
| **Add Redis connection pooling** | `mentor/kb/query.py` | Reduce Redis connection overhead |
| **Add LRU-TTL to embedding cache** | `mentor/kb/query.py` | Prevent cache staleness |
| **Refactor `_prompt_by_machine` cache** | `brain.py` | Use TTL-based eviction instead of manual clear |

### 5.3 Low-Impact Improvements

| Change | Files | Impact |
|--------|-------|--------|
| **Add type annotations to `Any` params** | All worker managers | Better IDE support, fewer runtime errors |
| **Add logging to error paths** | Various | Better debugging visibility |
| **Extract constants to config** | `stalker.py`, `brain.py` | Configurable thresholds |
| **Add unit tests for parsing** | `mentor/engine/brain.py` | `parse_brain_output` has complex edge cases |
| **Consistent naming** | Throughout | `self._app` vs `self.app` inconsistency |

---

## 6. Scalability

### 6.1 Scaling to More Users

| Concern | Current | Scaling Behavior |
|---------|---------|-----------------|
| **Embedding model** | Singleton (in-process) | O(n) memory per additional model instance; good |
| **Redis connections** | One client per Brain instance | Could exhaust connection pool with many users |
| **Cross-encoder** | Singleton (in-process, CPU-bound) | Bottleneck; only one reranking at a time |
| **In-memory cache** | Dict per Brain instance | Linear memory growth; needs TTL |
| **LLM API rate limits** | Concurrent calls possible | Could hit rate limits with many concurrent users |

### 6.2 Scaling to More Data

| Data Type | Current | Scaling |
|-----------|---------|---------|
| **Knowledge base size** | Single Redis index | Redis handles millions of documents; linear search time |
| **Session history** | Rolling window with prune | O(1) bounded growth; good |
| **Thinking buffer** | Redis list with trim | O(1) bounded growth; good |
| **Learning vault** | Redis lists per machine | Grows linearly; needs periodic pruning |
| **Trace logs** | Append-only file | Unbounded growth; needs rotation |

### 6.3 Memory/Performance Concerns

**Critical: Cross-Encoder Memory**
The cross-encoder model (BAAI/bge-reranker-base) loads ~400MB into memory. With concurrent reranking:
```
- One cross-encoder instance: ~400MB
- Multiple concurrent reranks: still ~400MB (shared)
- But each rerank is sequential (not batched)
```

**Critical: Embedding Cache Growth**
The `_embed_cache` dict has no eviction based on recency or TTL — only when exceeding `_EMBED_CACHE_SIZE`. With enough unique queries, it grows unbounded.

**Moderate: Redis Client Per Instance**
Each `Brain` instance creates its own Redis client:
```python
self._redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
```
With many concurrent brains (e.g., multiple users/boxes), this could exhaust Redis connections.

### 6.4 Database Scaling Considerations

**RedisVL Index Structure:**
```
ippsec_idx:{doc_id} → Hash {
    machine: "BoxName",
    title: "Walkthrough Title",
    url: "https://...",
    content: "...",
    embedding: [float; 768 dimensions]
}
```

**Scaling considerations:**
1. **Flat index** (not HNSW): Linear search time O(n). At 100K+ documents, consider migrating to HNSW or IVF.
2. **Cosine distance**: Computationally expensive for large datasets. Consider IVF-PQ for 1M+ docs.
3. **No replication**: Single Redis instance; add replicas for read scaling.
4. **No partitioning**: All indexes in a single Redis namespace.

---

## 7. Recommendations

### 7.1 Priority-Ordered Action Items

#### Phase 1: Quick Wins (Weeks 1-2)

1. **Centralize `_dashboard()` pattern** → Extract to `ContextManager` or base class
   - **Effort**: 2 hours
   - **Impact**: Eliminates ~200 lines of duplication

2. **Add TTL to `_embed_cache`**
   ```python
   # In _embed_cache management
   if len(_embed_cache) >= _EMBED_CACHE_SIZE:
       # Evict oldest entry that hasn't been accessed in >1 hour
       ...
   ```
   - **Effort**: 1 hour
   - **Impact**: Prevents stale cache growth

3. **Add thread safety to embedding cache**
   ```python
   _embed_lock = asyncio.Lock()
   ```
   - **Effort**: 1 hour
   - **Impact**: Prevents race conditions

#### Phase 2: Medium Impact (Weeks 3-4)

4. **Add LLM response caching**
   ```python
   # Key = hash(user_prompt + system_prompt_hash)
   # TTL = 5 minutes (or configurable)
   _response_cache = LRUCache(maxsize=100, ttl=300)
   ```
   - **Effort**: 4 hours
   - **Impact**: Significant latency reduction for repeated queries

5. **Batch embedding for RAG queries**
   - Group multiple queries during idle periods
   - Process embeddings in batch for the sentence-transformers model
   - **Effort**: 6 hours
   - **Impact**: 2-5x faster RAG retrieval

6. **Add Redis connection pooling**
   ```python
   from redis.asyncio import ConnectionPool
   _pool = ConnectionPool.from_url(settings.redis_url, max_connections=50)
   ```
   - **Effort**: 3 hours
   - **Impact**: Better connection reuse for multi-user scenarios

#### Phase 3: Structural Improvements (Months 2-3)

7. **Streaming LLM responses**
   - Add Server-Sent Events or WebSocket support
   - Stream partial thoughts to the UI as they're generated
   - **Effort**: 2 days
   - **Impact**: Dramatically improves user experience

8. **Command execution pipeline**
   - New module that runs commands, captures output, parses structured data
   - Feeds parsed results to the brain for analysis
   - **Effort**: 3-5 days
   - **Impact**: Transforms from passive observer to active coach

9. **Per-box context windows**
   - Track separate `history_context` per active box
   - Automatic context switching on `cd <box>`
   - **Effort**: 2 days
   - **Impact**: Better multi-box workflow

### 7.2 Quick Wins vs. Major Refactors

| Type | Items | Effort | Payoff |
|------|-------|--------|--------|
| **Quick Wins** | Centralize dashboard, add TTL, add locks | 4 hours | High (defects fixed) |
| **Quick Wins** | Add logging to error paths | 2 hours | Medium (debuggability) |
| **Quick Wins** | Extract constants from hardcoded sets | 3 hours | Low (maintainability) |
| **Major Refactors** | Streaming LLM | 2 days | High (UX transformation) |
| **Major Refactors** | Command execution pipeline | 3-5 days | High (active coaching) |
| **Major Refactors** | Per-box context | 2 days | Medium (multi-box workflow) |

### 7.3 Strategic vs. Tactical Improvements

**Strategic (Invest Now):**
1. **Response caching** — Reduces LLM costs and latency long-term
2. **Streaming support** — Modernizes the UX paradigm
3. **Batch embedding** — Foundation for faster RAG at scale

**Tactical (Fix Soon):**
1. **Thread safety on cache** — Prevents subtle bugs
2. **Centralize dashboard** — Reduces code duplication immediately
3. **TTL on embedding cache** — Prevents memory leak

**Nice-to-Have:**
1. **Command execution pipeline** — Great feature but not urgent
2. **Cross-encoder batching** — Incremental improvement
3. **Plugin system** — Nice to have but low priority

---

## Appendix: Key File Index

| File | Purpose | Lines |
|------|---------|-------|
| `cereal_killer/main.py` | Application entry point | ~70 |
| `cereal_killer/engine.py` | LLM engine wrapper | ~207 |
| `cereal_killer/config.py` | Settings management | ~103 |
| `cereal_killer/knowledge_base.py` | Redis KB wrapper | ~235 |
| `cereal_killer/context_manager.py` | Transcript management | ~68 |
| `cereal_killer/observer/vision_watcher.py` | Clipboard image monitoring | ~149 |
| `cereal_killer/observer/__init__.py` | Observer re-exports | ~52 |
| `mentor/engine/brain.py` | Core reasoning engine | ~1067 |
| `mentor/engine/session.py` | Redis session storage | ~292 |
| `mentor/engine/pedagogy.py` | Coaching state machine | ~143 |
| `mentor/engine/search_orchestrator.py` | Tiered search pipeline | ~287 |
| `mentor/engine/minifier.py` | Terminal output compression | ~40 |
| `mentor/engine/commands.py` | Slash command dispatch | ~100 |
| `mentor/engine/methodology.py` | Command methodology audit | ~60 |
| `mentor/kb/query.py` | RAG retrieval + embedding | ~1073 |
| `mentor/kb/hacktricks_ingest.py` | HackTricks data ingest | ~varies |
| `mentor/kb/hacktricks_retrieval.py` | HackTricks retrieval | ~varies |
| `mentor/observer/stalker.py` | History file watcher | ~643 |
| `mentor/tools/web_search.py` | SearXNG integration | ~varies |
