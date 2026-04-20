# HackTricks Deep Read Integration Guide

## Overview

The HackTricks integration system provides **full-text ingestion and vectorization** of the HackTricks knowledge base directly into Redis, enabling Zero Cool to perform instant RAG (Retrieval-Augmented Generation) queries against the entire HackTricks library without relying on external APIs.

## Architecture

### 1. Content Extraction (`hacktricks_ingest.py`)

**Deep Read Process:**
- Discovers all `.md` files in the HackTricks `/src` directory
- Extracts structured metadata:
  - `title`: H1 heading or filename
  - `raw_content`: Full cleaned Markdown (HTML comments removed)
  - `headers`: All H2/H3 subheadings for metadata filtering
  - `url`: Generated book.hacktricks.xyz citation link

**Extraction Pattern:**
```python
doc = extract_document(file_path)
# Returns: HackTricksDocument with title, headers, url, raw_content
```

### 2. Recursive Semantic Chunking

**Section-Based Strategy:**
- **NOT** simple character count chunking
- Splits files at every H2/H3 header
- Each chunk includes **parent header context** for scope awareness
  - Example breadcrumb: `"Generic Hacking > Reverse Shells > Port Forwarding"`

**Metadata Tags:**
Automatically extracted from breadcrumb:
- `category`: exploitation | recon | privilege-escalation | networking | general
- `service`: ssh | ftp | http | smb | nfs | dns | ldap | snmp | mysql | postgres | etc.

**Chunk Example Structure:**
```python
HackTricksChunk(
    breadcrumb="Network Services > SSH > SSH Tunneling",
    content_text="<markdown content here>",
    source_file="src/network-services/ssh.md",
    title="SSH",
    url="https://book.hacktricks.xyz/network-services/ssh",
    tags={"category": "networking", "service": "ssh"}
)
```

### 3. Redis Schema Expansion

**Index Name:** `hacktricks`

**Fields:**
```python
{
    "breadcrumb": "TEXT",           # "Linux > Privilege Escalation > SUID"
    "content_text": "TEXT",         # Full Markdown content
    "content_vector": "FLOAT32",    # 1536-dim embedding (OpenAI)
    "source_file": "TAG",           # "src/linux/privilege-escalation.md"
    "title": "TEXT",                # "Privilege Escalation"
    "url": "TEXT",                  # Citation link
    "tags": "TAG"                   # "category:privilege-escalation,service:ssh"
}
```

**Key Design:**
- Full `content_text` stored in Redis (not external links)
- LLM gets immediate access to actual methodology, not summaries
- Zero-millisecond retrieval from sub-millisecond memory

### 4. Batch Ingestion Pipeline

**Batch Processing (default: 50 chunks/batch):**

```python
stats = await ingest_hacktricks_batch(
    chunks=all_chunks,
    embed_fn=embed,           # Async embedding function
    redis_client=client,      # Redis connection
    index_name="hacktricks",
    batch_size=50             # Process 50 chunks per batch
)
# Returns: {"total_chunks": X, "ingested": Y, "failed": Z, "batches": N}
```

**Why Batching:**
- HackTricks has ~1,000s of files → massive ingestion load
- Prevents Redis connection timeouts
- Allows progress monitoring
- Graceful error handling per batch

### 5. Integration with Brain's RAG

**System Instruction (added to Brain's system_prompt):**

```
When users ask about methodology, techniques, or tools mentioned in HackTricks
(e.g., reverse shells, port forwarding, privilege escalation vectors):

1. Query HackTricks RAG with: query_hacktricks_from_redis(user_query)
2. Receive top 3 chunks with breadcrumb context
3. Include content_text in your response with citations
4. If user is in [EXPLOITATION] phase, prioritize exploitation/evasion content
5. For [POST-EXPLOITATION], prioritize privilege escalation content
6. Always cite: "HackTricks > [Breadcrumb] — [URL]"
```

**Example Integration Point:**
```python
# In Brain.ask() method
hacktricks_snippets = query_hacktricks_from_redis(
    redis_client,
    user_query,
    embed_fn,
    limit=3
)

# Add to system prompt
ht_block = format_hacktricks_snippets(hacktricks_snippets)
system_prompt += ht_block
```

## Usage

### One-Time Setup: `/sync-hacktricks`

**Command:**
```bash
/sync-hacktricks                    # Uses ~/.cache/hacktricks
/sync-hacktricks /path/to/ht       # Custom directory
```

**Full Pipeline (Automated):**

1. **Clone/Update:** Fetches latest HackTricks repo (via git clone --depth=1)
2. **Parse:** Discovers all .md files, extracts documents
3. **Chunk:** Splits by headers, adds breadcrumb context
4. **Embed:** Vectorizes all chunks (async batches)
5. **Store:** Ingests to Redis with 30-day TTL
6. **Report:** Shows ingestion stats

**Expected Output:**
```
[*] HackTricks Deep Read Synchronization
[*] Target directory: /home/user/.cache/hacktricks
[+] Step 1: Fetching HackTricks repository...
    ✓ Clone complete
[+] Step 2: Discovering and extracting markdown files...
    Found 1,245 markdown files
[+] Step 3: Parsing and chunking documents...
    ✓ Created 8,432 semantic chunks
[+] Step 4: Vectorizing and ingesting to Redis...
    Using Redis: redis://localhost:6379
    Batch 1: 50 ingested, 0 failed
    Batch 2: 50 ingested, 0 failed
    ...
    ✓ Ingestion complete:
      - Total chunks: 8,432
      - Ingested: 8,432
      - Failed: 0
      - Batches: 169
[✓] HackTricks synchronization complete!
[*] Zero Cool now has the entire HackTricks library in his sub-millisecond memory.
```

### Retrieval in Responses

**User Query:** "How do I create a reverse shell?"

**Zero Cool's Process:**
1. Query HackTricks: `"reverse shell techniques"`
2. Get 3 top chunks with full methodology
3. Include in response with context and citation

**Example Response:**
```
Based on HackTricks methodology:

[HackTricks #1] Reverse Shells
Location: Generic Hacking > Reverse Shells > Common Techniques
Reference: https://book.hacktricks.xyz/generic-hacking/reverse-shells

<Full methodology content here>

This is a common pattern in HTB boxes. You'll want to try multiple payload types.
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    User Command                             │
│                  /sync-hacktricks                           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │  sync_command.py                       │
        │  ├─ Git Clone/Pull HackTricks          │
        │  ├─ Discover .md files                 │
        │  └─ Orchestrate pipeline               │
        └────────────┬──────────────────────────┘
                     │
        ┌────────────▼──────────────────────────┐
        │  hacktricks_ingest.py                  │
        │  ├─ extract_document()                 │
        │  │   └─ Parse title, headers, url     │
        │  ├─ chunk_document()                   │
        │  │   └─ Split by H2/H3, add context   │
        │  └─ ingest_hacktricks_batch()         │
        │      └─ Embed & store in Redis        │
        └────────────┬──────────────────────────┘
                     │
        ┌────────────▼──────────────────────────┐
        │         Redis Index                   │
        │    ┌─────────────────────────────┐    │
        │    │ hacktricks:HASH             │    │
        │    │ ├─ breadcrumb (TEXT)        │    │
        │    │ ├─ content_text (TEXT)      │    │
        │    │ ├─ content_vector (VEC)     │    │
        │    │ ├─ source_file (TAG)        │    │
        │    │ ├─ title (TEXT)             │    │
        │    │ ├─ url (TEXT)               │    │
        │    │ └─ tags (TAG)               │    │
        │    └─────────────────────────────┘    │
        └─────────────────────────────────────┘
                     │
        ┌────────────▼──────────────────────────┐
        │  hacktricks_retrieval.py               │
        │  ├─ query_hacktricks_from_redis()     │
        │  │   └─ Vector similarity search      │
        │  └─ format_hacktricks_snippets()      │
        │      └─ Format for LLM context        │
        └────────────┬──────────────────────────┘
                     │
        ┌────────────▼──────────────────────────┐
        │         Brain.ask()                   │
        │  ├─ Include HackTricks in system      │
        │  │   prompt as RAG context            │
        │  └─ LLM generates response with       │
        │      citations                        │
        └─────────────────────────────────────┘
```

## Performance Characteristics

**Ingestion (First Time):**
- ~1,000-1,200 markdown files
- ~8,000-10,000 semantic chunks
- 169-200 batches (50 chunks/batch)
- **Time:** 3-5 minutes (depends on embedding model, internet speed)
- **Storage:** ~500MB-1GB in Redis (TTL: 30 days)

**Retrieval:**
- **Query time:** <100ms (vector similarity in Redis)
- **Response latency:** Sub-millisecond for cached chunks
- **Throughput:** Supports concurrent queries

**Memory Footprint:**
- Embeddings: 1,536 floats × 8k chunks = ~48MB
- Text content: ~200KB avg × 8k chunks = ~1.6GB
- **Total:** ~1.7GB Redis memory (with compression)

## Troubleshooting

**Problem: "git clone" fails**
- Solution: Install git (`apt install git` or `brew install git`)
- Or: Manually clone: `git clone https://github.com/carlospolop/hacktricks.git ~/.cache/hacktricks`

**Problem: Redis connection timeout**
- Solution: Increase batch size if network is slow, or reduce it if Redis is memory-constrained
- Check Redis: `redis-cli ping` should return `PONG`

**Problem: Out of memory during ingestion**
- Solution: Reduce `batch_size` (e.g., 25 instead of 50)
- Or: Temporarily reduce TTL to free up space

**Problem: Embeddings are slow**
- Solution: Switch to a faster model or use GPU acceleration if available
- Check embedding function configuration in settings

## Future Enhancements

- Incremental sync (only update changed files instead of full re-ingest)
- Hierarchical chunking (maintain parent-child relationships)
- Custom tagging via user-provided taxonomy
- Real-time HackTricks subscriptions (watch for updates)
- Category-specific retrieval (ask for "only Linux privesc" techniques)

## Security Notes

- All content is stored in Redis — ensure Redis instance is properly secured
- No sensitive data is logged (embeddings are vectors, not text)
- HackTricks content is public; no confidentiality concerns
- 30-day TTL ensures stale data is automatically cleaned up

---

**Summary:** The HackTricks Deep Read integration gives Zero Cool sub-millisecond access to the entire HackTricks methodology library, enabling instant, citation-aware guidance without external API calls. The system is designed for scale (10,000+ chunks), fault-tolerance (batch ingestion), and performance (vector similarity on Redis).
