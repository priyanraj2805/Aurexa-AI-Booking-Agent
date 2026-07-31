import logging
from typing import List, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import json
import re

from app.services.keyword_extractor import extract_keywords
from app.engine.orchestrator import LLMOrchestrator

logger = logging.getLogger(__name__)

_rerank_orchestrator: Optional[LLMOrchestrator] = None


def _get_rerank_orchestrator() -> LLMOrchestrator:
    global _rerank_orchestrator
    if _rerank_orchestrator is None:
        _rerank_orchestrator = LLMOrchestrator()
    return _rerank_orchestrator


async def retrieve_chunks(
    db: AsyncSession,
    org_id: str,
    query: str,
    k: int = 20
) -> List[Dict]:
    """Retrieve relevant chunks using BM25 full-text search (PostgreSQL tsvector)."""
    keywords = extract_keywords(query)

    if not keywords:
        return []

    # Build a to_tsquery string with AND semantics: "kw1 & kw2 & kw3".
    # ``plainto_tsquery`` ignores ``&`` so we use ``to_tsquery`` instead,
    # which requires careful sanitization of each keyword.
    def _sanitize(tok: str) -> str:
        # to_tsquery treats some chars as operators; strip everything that
        # isn't a letter/digit and remove anything that would form a bare
        # operator (e.g. &, |, !, (, ), :).
        cleaned = "".join(ch for ch in tok.lower() if ch.isalnum())
        return cleaned or "0"  # fallback to a never-match token to avoid crash

    sanitized = [_sanitize(k) for k in keywords if _sanitize(k)]
    if not sanitized:
        return []
    tsquery_str = " | ".join(sanitized)

    query_sql = """
        SELECT
            id, content, source_type, source_url, metadata, doc_id, chunk_index,
            ts_rank_cd(tsvector_col, to_tsquery('english', :query)) AS rank
        FROM document_chunks
        WHERE org_id = :org_id
          AND tsvector_col @@ to_tsquery('english', :query)
        ORDER BY rank DESC
        LIMIT :k
    """

    try:
        result = await db.execute(
            text(query_sql),
            {"org_id": org_id, "query": tsquery_str, "k": k},
        )
        rows = [dict(r) for r in result.mappings().all()]
        return rows
    except Exception as e:
        logger.error(f"retrieve_chunks failed for org_id={org_id}: {e}")
        return []


async def rerank_chunks(query: str, chunks: List[dict], top_k: int = 5) -> List[dict]:
    """Rerank chunks using the existing LLM (Groq) via LLMOrchestrator."""
    if not chunks:
        return []

    if len(chunks) <= top_k:
        for c in chunks:
            c.setdefault("rerank_score", 0)
        return chunks[:top_k]

    snippet_lines = []
    for i, c in enumerate(chunks):
        snippet = c.get("content", "")[:400].replace("\n", " ")
        snippet_lines.append(f"[{i}] {snippet}")
    chunks_block = "\n".join(snippet_lines)

    system_prompt = (
        "You are a relevance ranker. Score each chunk 1-10 for how well it answers "
        "the user's query. Return ONLY a JSON object with key 'scores' mapping "
        "integer index (as string) to integer score."
    )
    user_prompt = (
        f"Query: {query}\n\n"
        f"Chunks:\n{chunks_block}\n\n"
        "Return JSON: {\"scores\": {\"0\": 8, \"1\": 3, ...}}"
    )

    try:
        orchestrator = _get_rerank_orchestrator()
        raw = await orchestrator._call_llm(
            prompt=user_prompt,
            system_prompt=system_prompt,
            json_mode=True,
        )
        if not raw:
            return chunks[:top_k]

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
        scores_map = parsed.get("scores", {})

        for idx_str, score in scores_map.items():
            try:
                idx = int(idx_str)
                if 0 <= idx < len(chunks):
                    chunks[idx]["rerank_score"] = int(score)
            except (ValueError, TypeError):
                continue

        for c in chunks:
            c.setdefault("rerank_score", 0)
        return sorted(chunks, key=lambda x: x.get("rerank_score", 0), reverse=True)[:top_k]
    except Exception as e:
        logger.warning(f"rerank_chunks failed, returning BM25 order: {e}")
        return chunks[:top_k]


async def retrieve_and_rerank(
    db: AsyncSession,
    org_id: str,
    query: str,
    top_k: int = 5,
    candidate_k: int = 30,
) -> List[Dict]:
    """Full retrieval pipeline: BM25 -> LLM rerank."""
    chunks = await retrieve_chunks(db, org_id, query, k=candidate_k)
    if not chunks:
        return []
    return await rerank_chunks(query, chunks, top_k=top_k)


async def store_document_chunks(
    db: AsyncSession,
    org_id: str,
    chunks: List[dict],
    source_type: str,
    source_url: Optional[str] = None,
    doc_id: Optional[str] = None,
):
    """Insert document chunks into the document_chunks table.

    Populates ``tsvector_col`` explicitly (the column is a plain TSVECTOR,
    not GENERATED) so BM25 retrieval works on the next query.
    """
    import uuid as _uuid
    if doc_id is None:
        doc_id = str(_uuid.uuid4())

    for i, chunk in enumerate(chunks):
        metadata = chunk.get("metadata", {}) or {}
        content_escaped = chunk["content"]
        await db.execute(
            text(
                """
                INSERT INTO document_chunks (
                    id, org_id, doc_id, chunk_index, content, page_number,
                    source_type, source_url, metadata, tsvector_col
                )
                VALUES (
                    gen_random_uuid(), :org_id, :doc_id, :chunk_index, :content,
                    :page_number, :source_type, :source_url, CAST(:metadata AS JSONB),
                    to_tsvector('english', :content)
                )
                """
            ),
            {
                "org_id": org_id,
                "doc_id": doc_id,
                "chunk_index": i,
                "content": chunk["content"],
                "page_number": chunk.get("page_number"),
                "source_type": source_type,
                "source_url": source_url,
                "metadata": json.dumps(metadata),
            },
        )

    # Backfill tsvector_col for any legacy rows that have NULL tsvector_col.
    # Cheap when only a handful are null; retried once per upload call.
    await db.execute(
        text(
            "UPDATE document_chunks SET tsvector_col = to_tsvector('english', content) "
            "WHERE tsvector_col IS NULL"
        )
    )

    return doc_id


async def delete_document_chunks(db: AsyncSession, org_id: str, doc_id: str) -> int:
    """Delete all chunks belonging to a doc_id for an org. Returns row count."""
    result = await db.execute(
        text(
            "DELETE FROM document_chunks WHERE org_id = :org_id AND doc_id = :doc_id"
        ),
        {"org_id": org_id, "doc_id": doc_id},
    )
    return result.rowcount or 0


async def delete_website_chunks(db: AsyncSession, org_id: str) -> int:
    """Delete all website-crawl chunks for an org. Returns row count."""
    result = await db.execute(
        text(
            "DELETE FROM document_chunks WHERE org_id = :org_id AND source_type = 'website'"
        ),
        {"org_id": org_id},
    )
    return result.rowcount or 0
