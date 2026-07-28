"""PDF → pgvector knowledge ingestion (OSS equivalent of 11_knowledge_ingest).

Flow (see docs/knowledge-flow.md):
  1. REGISTER   a document dropped in ./knowledge_uploads/ → knowledge_registry
                (pending_review). PDF/txt/md/html/docx/csv — see document_loaders.py
  2. REVIEW     a HUMAN sets status='approved' (VRR-relevant only) — not automated
  3. INGEST     load (document_loaders) → chunk (text_splitters) → PII detect/REDACT
                (core.knowledge.redact_pii) → EMBED (local model) → INSERT into
                vrr_agent.reservoir_knowledge (pgvector), PII never reaching the DB
  4. SEARCH     the agent's SEARCH_KNOWLEDGE tool runs `embedding <=> query` (cosine)

Embeddings are produced by a LOCAL model (Ollama `nomic-embed-text`, 768-dim) so the
whole path stays offline + free. Swap `embed()` for any encoder that matches the
vector(768) column in schema.sql.
"""
from __future__ import annotations

import hashlib
import os
import pathlib

import httpx
import psycopg
from pgvector.psycopg import register_vector

from ..config import load_config
from ..core import knowledge as kn

CFG = load_config()
UPLOAD_DIR = os.environ.get("VRR_KNOWLEDGE_DIR", "./knowledge_uploads")
EMBED_MODEL = os.environ.get("VRR_EMBED_MODEL", "nomic-embed-text")   # 768-dim, local


def _conn():
    c = psycopg.connect(CFG.pg_dsn)
    register_vector(c)                       # enables the `vector` type for pgvector
    return c


def embed(text: str) -> list[float]:
    """Local embedding via Ollama (offline). Returns a 768-dim vector."""
    r = httpx.post(f"{CFG.llm_base_url}/api/embeddings",
                   json={"model": EMBED_MODEL, "prompt": text}, timeout=60)
    return r.json()["embedding"]


def normalize_page(text: str) -> str:
    """Undo PDF layout artefacts before chunking.

    Extracted PDF text carries hard line wraps and run of spaces from the page layout;
    left alone they leak into the chunks (and into anything quoting them). Collapse the
    intra-paragraph wrapping while keeping blank lines, which `core.knowledge.chunk_text`
    uses as paragraph boundaries.
    """
    import re
    text = (text or "").replace("\r\n", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)          # unwrap single newlines
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def register_new() -> int:
    """Step 1 — register new documents in the volume as pending_review."""
    from . import document_loaders as DL

    n = 0
    with _conn() as c, c.cursor() as cur:
        for f in os.listdir(UPLOAD_DIR):
            if pathlib.Path(f).suffix.lower() not in DL.SUFFIX_LOADERS:
                continue          # .pdf/.txt/.md/.html/.docx/.csv — see document_loaders
            doc_id = hashlib.sha1(f.encode()).hexdigest()
            cur.execute(
                "INSERT INTO vrr_agent.knowledge_registry (doc_id, file_name, status) "
                "VALUES (%s, %s, 'pending_review') ON CONFLICT (doc_id) DO NOTHING",
                (doc_id, f))
            n += cur.rowcount
        c.commit()
    return n


def ingest_approved(strategy: str | None = None) -> int:
    """Step 3 — load + chunk + PII-redact + embed every approved-but-not-ingested doc.

    Loading goes through `document_loaders` (so .txt/.html/.docx/.csv ingest exactly like
    a PDF does) and chunking through `text_splitters` (default `recursive`, the strategy
    that measured best — see `make chunks`). `VRR_CHUNK_STRATEGY` overrides it; changing
    it means re-ingesting, since chunk boundaries are baked into the stored embeddings.
    """
    from . import document_loaders as DL
    from . import text_splitters as TS

    strategy = strategy or os.environ.get("VRR_CHUNK_STRATEGY", "recursive")
    done = 0
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT doc_id, file_name FROM vrr_agent.knowledge_registry "
                    "WHERE status='approved' AND n_chunks IS NULL")
        for doc_id, fname in cur.fetchall():
            pages = DL.load_file(os.path.join(UPLOAD_DIR, fname))
            for p in pages:                       # undo PDF layout wrapping before split
                p.page_content = normalize_page(p.page_content)
            kinds, seq = set(), 0
            for piece in TS.split_documents(pages, strategy=strategy):
                clean, k = kn.redact_pii(piece.page_content)  # PII never reaches the DB
                kinds.update(k)
                pageno = int(piece.metadata.get("page", 1))
                cur.execute(
                    "INSERT INTO vrr_agent.reservoir_knowledge "
                    "(chunk_id, doc_id, file_name, page, chunk_seq, text, pii_redacted, embedding) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (chunk_id) DO UPDATE "
                    "SET text=EXCLUDED.text, embedding=EXCLUDED.embedding",
                    (f"{doc_id}:{pageno}:{seq}", doc_id, fname, pageno, seq,
                     clean, bool(k), embed(clean)))
                seq += 1
            cur.execute("UPDATE vrr_agent.knowledge_registry SET n_chunks=%s, "
                        "pii_found=%s, pii_kinds=%s WHERE doc_id=%s",
                        (seq, bool(kinds), ",".join(sorted(kinds)) or None, doc_id))
            done += 1
        c.commit()
    return done


def search(query: str, k: int = 5, min_score: float | None = None) -> list[dict]:
    """Step 4 — the SEARCH_KNOWLEDGE tool: cosine nearest chunks (pgvector `<=>`).

    Filtered by a similarity FLOOR, not just top-k. Without one, `LIMIT k` always
    returns k rows: ask about something the corpus never covered and the model still
    receives four confident-looking excerpts, which is precisely the situation where it
    invents an answer instead of saying it does not know. Below the floor we return
    nothing, and the caller says so.

    `VRR_RETRIEVAL_MIN_SCORE` tunes it (default 0.35); pass `min_score=0` to see the
    raw ranking, which is what `retrieval_check` wants when comparing chunkers.
    """
    floor = CFG.retrieval_min_score if min_score is None else min_score
    vec = str(embed(query))              # embed ONCE, reuse for score + ordering
    with _conn() as c:
        with c.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT file_name, page, text, 1 - (embedding <=> %(v)s::vector) AS score "
                "FROM vrr_agent.reservoir_knowledge "
                "WHERE 1 - (embedding <=> %(v)s::vector) >= %(floor)s "
                "ORDER BY embedding <=> %(v)s::vector LIMIT %(k)s",
                {"v": vec, "k": k, "floor": floor})
            return cur.fetchall()


if __name__ == "__main__":
    print("registered:", register_new(), "| ingested approved:", ingest_approved())
