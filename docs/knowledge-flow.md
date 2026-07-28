# PDF → vector DB (pgvector) — knowledge ingestion flow

How reservoir PDFs become searchable semantic memory, fully local (no cloud vector
service). Code: [pipeline/knowledge_ingest.py](../src/vrr_agent_open/pipeline/knowledge_ingest.py)
+ pure text ops in [core/knowledge.py](../src/vrr_agent_open/core/knowledge.py).

```mermaid
flowchart TD
    U["👤 drop PDF in ./knowledge_uploads/"] --> REG["1. register_new()<br/>sha1(file) → knowledge_registry"]
    REG --> KR[("vrr_agent.knowledge_registry<br/>status='pending_review'")]
    KR --> REV["👤 2. human review<br/>UPDATE status='approved'<br/>(VRR-relevant only — NOT automated)"]
    REV -->|approved| PARSE["3. ingest_approved()<br/>pypdf parse → core.knowledge.chunk_text"]
    PARSE --> PII["redact_pii()<br/>email·phone·SSN·card·creds → [REDACTED]"]
    PII --> EMB["embed() — local model<br/>Ollama nomic-embed-text (768-dim)"]
    EMB --> INS[("INSERT vrr_agent.reservoir_knowledge<br/>text + embedding vector(768)")]
    INS --> IDX["pgvector index (cosine)"]
    Q["agent question"] --> SRCH["4. search()<br/>embedding <=> query (cosine)"]
    IDX --> SRCH --> TOOL["SEARCH_KNOWLEDGE tool → cited chunks"]
```

## The four steps

| Step | Function | What happens |
|---|---|---|
| 1. Register | `register_new()` | Every new PDF in `./knowledge_uploads/` gets a `knowledge_registry` row, `status='pending_review'`. Nothing else touches it. |
| 2. Review | *(human SQL)* | A reviewer confirms it's VRR-relevant and sets `status='approved'`. **Deliberately manual** — relevance is not automated (guardrail). |
| 3. Ingest | `ingest_approved()` | Parse (pypdf) → **chunk** (paragraph-aware + overlap) → **PII detect & REDACT** (PII never reaches the DB) → **embed** with a local model → `INSERT` into `reservoir_knowledge` with the `vector(768)` embedding. |
| 4. Search | `search()` | The agent's `SEARCH_KNOWLEDGE` tool runs `embedding <=> query` (pgvector cosine `<=>`), returns the top-k cited chunks. |

## Why pgvector (vs a separate vector service)

- **Same database** as the VRR data — one store, one backup, one connection; no
  extra service, no cost. The embedding lives in `vrr_agent.reservoir_knowledge`
  next to the governed tables.
- **Cosine search** is `ORDER BY embedding <=> query LIMIT k` — plain SQL.
- Add an ANN index for scale: `CREATE INDEX ON vrr_agent.reservoir_knowledge
  USING hnsw (embedding vector_cosine_ops);`

## Loading other formats, and choosing a chunker

Ingest loads through [document_loaders.py](../src/vrr_agent_open/pipeline/document_loaders.py)
and chunks through [text_splitters.py](../src/vrr_agent_open/pipeline/text_splitters.py).
Both run standalone, so you can see what a change does before it reaches the index:

```bash
make loaders                      # ./knowledge_uploads → List[Document] + metadata summary
make loaders from=./docs          # any folder: mixed .pdf/.txt/.html/.docx/.csv in one pass
make loaders from=https://…       # a URL (WebBaseLoader)
make chunks                       # score chunking strategies by retrieval, not by eye
make floor                        # measure the similarity floor that separates
                                  #   "the docs answer this" from "they don't"
```

| Format | Loader | Granularity |
|---|---|---|
| `.pdf` | `PyPDFLoader` (`PyMuPDFLoader` via `backend="pymupdf"`) | one Document per page |
| `.txt` `.md` | `TextLoader` | one Document per file |
| `.html` | `BSHTMLLoader` | one Document, markup stripped |
| `.docx` | `Docx2txtLoader` | one Document per file |
| `.csv` | `CSVLoader` | **one Document per row** |
| folder | per-suffix dispatch | mixed formats in one pass, bad files skipped |
| URL(s) | `WebBaseLoader` | one Document per URL |
| complex | `UnstructuredFileLoader` | optional extra: scans, tables, slides |

`file_name`/`page`/`file_type` are normalised across every loader (and pages made
1-based), because a chunk that cannot be cited is one the RAG answer has to drop or fake.

**PyPDF vs PyMuPDF**: same Document shape, so nothing downstream changes. `pypdf` is pure
Python and is the default; `pymupdf` is a C library — faster and better on multi-column
layouts, so it is the one for a bulk backfill (`pip install -e ".[fastpdf]"`).

### Chunking is judged by retrieval, never by eye

Each chunk is embedded in isolation, so context lost at a boundary is unrecoverable at
query time. Measured on the same procedure text (`make chunks`):

| Strategy | chunks | ends on a sentence | recall@2 | MRR |
|---|---|---|---|---|
| fixed (200 chars) | 4 | 25% | **0.33** | 0.56 |
| **recursive (400/60)** — default | 3 | 100% | **1.00** | 1.00 |
| semantic (cosine 0.75) | 9 | 100% | **0.67** | 0.78 |

Fixed-size splitting cuts `"…the response factor rho … It starts │ at 0.85…"`, and the
question "what does rho start at?" then ranks the chunk holding `0.85` third — outside
the top-k that reaches the prompt. Semantic chunking is *not* automatically best: on
short, dense procedure text it over-splits (83-char chunks carry too little context).

**The test, in short** — `retrieval_check()`: label questions with a phrase the retrieved
chunk MUST contain, embed each strategy's chunks, score recall@k and MRR. A chunking
change is only better if retrieval gets better. Grow `PROBES` from real questions in
`vrr_agent.chat_history`.

## Knowing when NOT to answer

`search()` filters by a similarity FLOOR, not just top-k. Without one, `LIMIT k` always
returns k rows: ask something the corpus never covered and the model still receives
excerpts, which is exactly when it invents an answer.

The floor is **measured, not guessed** (`make floor`) — and the measurement mattered:

```
ANSWERABLE  min top-1  0.671    ("how much can I change injection in one step?")
OFF-TOPIC   max top-1  0.564    ("flare gas recovery compressor trip setpoint")
gap +0.107  →  VRR_RETRIEVAL_MIN_SCORE = 0.62
```

`nomic-embed-text` scores *unrelated* text at 0.40-0.56, so an intuitive 0.35 admits
everything and the agent never abstains. At 0.62 it does:

> I don't know — the ingested documents contain nothing relevant to that (nothing scored
> above 0.62). Either the document covering it has not been ingested, or it is a
> field-data question — ask about a pattern and period and the deterministic tools will
> answer it from Postgres.

The model is **not called** on that path, so it cannot be talked into answering anyway.
`rulebook_unanswerable` in `data/evaluation/vrr_questions.py` keeps it honest: without a
negative case, a retriever that always returns its k nearest rows scores identically to
one that knows when it has nothing.

A negative gap from `make floor` means no threshold separates the sets — that is a
retrieval problem (chunking, embedder), not a tuning problem.

## Guardrails preserved from the Databricks version

- **Human relevance gate** before anything is embedded (step 2).
- **PII redacted before storage** — the raw match never lands in the DB or index.
- **Every retrieved chunk is citable** (file_name + page), so the agent grounds
  knowledge claims the same way it grounds numbers.

## Run it

```bash
# local embedding model (once):  ollama pull nomic-embed-text
mkdir -p knowledge_uploads && cp your_report.pdf knowledge_uploads/
python -m vrr_agent_open.pipeline.knowledge_ingest      # register + ingest approved
# approve in SQL first:
#   UPDATE vrr_agent.knowledge_registry SET status='approved' WHERE file_name='your_report.pdf';
```
