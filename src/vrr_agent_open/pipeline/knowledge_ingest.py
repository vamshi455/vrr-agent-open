"""PDF → pgvector knowledge ingestion (OSS equivalent of 11_knowledge_ingest).

Flow (see docs/knowledge-flow.md):
  1. REGISTER   a PDF dropped in ./knowledge_uploads/ → knowledge_registry (pending_review)
  2. REVIEW     a HUMAN sets status='approved' (VRR-relevant only) — not automated
  3. INGEST     parse (pypdf) → chunk (core.knowledge.chunk_text) → PII detect/REDACT
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


def register_new() -> int:
    """Step 1 — register new PDFs in the volume as pending_review."""
    n = 0
    with _conn() as c, c.cursor() as cur:
        for f in os.listdir(UPLOAD_DIR):
            if not f.lower().endswith(".pdf"):
                continue
            doc_id = hashlib.sha1(f.encode()).hexdigest()
            cur.execute(
                "INSERT INTO vrr_agent.knowledge_registry (doc_id, file_name, status) "
                "VALUES (%s, %s, 'pending_review') ON CONFLICT (doc_id) DO NOTHING",
                (doc_id, f))
            n += cur.rowcount
        c.commit()
    return n


def ingest_approved() -> int:
    """Step 3 — chunk + PII-redact + embed every approved-but-not-ingested doc."""
    from pypdf import PdfReader
    done = 0
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT doc_id, file_name FROM vrr_agent.knowledge_registry "
                    "WHERE status='approved' AND n_chunks IS NULL")
        for doc_id, fname in cur.fetchall():
            reader = PdfReader(os.path.join(UPLOAD_DIR, fname))
            kinds, seq = set(), 0
            for pageno, page in enumerate(reader.pages, start=1):
                for chunk in kn.chunk_text(page.extract_text() or ""):
                    clean, k = kn.redact_pii(chunk)          # PII never reaches the DB
                    kinds.update(k)
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


def search(query: str, k: int = 5) -> list[dict]:
    """Step 4 — the SEARCH_KNOWLEDGE tool: cosine nearest chunks (pgvector `<=>`)."""
    with _conn() as c:
        with c.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT file_name, page, text, 1 - (embedding <=> %s::vector) AS score "
                "FROM vrr_agent.reservoir_knowledge ORDER BY embedding <=> %s::vector LIMIT %s",
                (embed(query), embed(query), k))
            return cur.fetchall()


if __name__ == "__main__":
    print("registered:", register_new(), "| ingested approved:", ingest_approved())
