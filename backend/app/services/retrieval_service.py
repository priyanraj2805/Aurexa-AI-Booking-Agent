import logging
from typing import List, Dict, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import asyncio
import json
import re

from app.services.keyword_extractor import extract_keywords
from app.engine.orchestrator import LLMOrchestrator

logger = logging.getLogger(__name__)

RERANK_HARD_TIMEOUT_SECONDS = 6.0

# Matches messages that refer to an uploaded/crawled knowledge source
# rather than a specific fact inside it ("summarize the pdf", "this book").
_SOURCE_REFERENCE_RE = re.compile(
    r"\b(pdf|file|document|docs?|docx|book|books|novel|website|site|upload|"
    r"uploaded|attachment|attached)\b",
    flags=re.IGNORECASE,
)
# Same set minus "book(s)" — used to disambiguate the English verb
# "to book an appointment" from the noun "this book".
_SOURCE_REFERENCE_NO_BOOK_RE = re.compile(
    r"\b(pdf|file|document|docs?|docx|novel|website|site|upload|"
    r"uploaded|attachment|attached)\b",
    flags=re.IGNORECASE,
)
_BOOKING_OBJECT_RE = re.compile(
    r"\b(appointments?|slots?|tables?|tickets?|demos?|meetings?|calls?)\b",
    flags=re.IGNORECASE,
)


def references_uploaded_source(message: Optional[str]) -> bool:
    """True when the message talks about a knowledge source itself."""
    text = str(message or "")
    if not _SOURCE_REFERENCE_RE.search(text):
        return False
    if re.search(r"\bbooks?\b", text, flags=re.IGNORECASE):
        # "book" is also a verb ("book an appointment"). Treat it as
        # booking talk only when a booking object is present AND no other
        # unambiguous source word appears.
        if (
            _BOOKING_OBJECT_RE.search(text)
            and not _SOURCE_REFERENCE_NO_BOOK_RE.search(text)
        ):
            return False
    return True

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
        # isn't a letter/digit/combining mark. Combining marks MUST be kept
        # for scripts like Devanagari where vowels attach as marks —
        # dropping them would break matching against the stored tsvector.
        import unicodedata

        cleaned = "".join(
            ch
            for ch in tok.lower()
            if ch.isalnum()
            or unicodedata.category(ch).startswith(("M", "Lm"))
        )
        return cleaned or "0"  # fallback to a never-match token to avoid crash

    sanitized = [_sanitize(k) for k in keywords if _sanitize(k)]
    if not sanitized:
        return []

    # Coverage-first BM25: rank primarily by HOW MANY DISTINCT question
    # terms a chunk contains, secondarily by ts_rank_cd. Plain OR-ranking
    # drowns decisive rare terms ("kumquat") under thousands of big-corpus
    # pages that happen to contain generic words ("monthly", "allowed").
    coverage_parts = []
    params: Dict[str, Any] = {"org_id": str(org_id), "k": k}
    for i, tok in enumerate(sanitized[:10]):
        params[f"t{i}"] = tok
        coverage_parts.append(
            f"(tsvector_col @@ to_tsquery('english', :t{i}))::int"
        )
    coverage_expr = " + ".join(coverage_parts)

    query_sql = f"""
        SELECT
            id, content, source_type, source_url, metadata, doc_id, chunk_index,
            ( {coverage_expr} ) AS coverage,
            ts_rank_cd(tsvector_col, to_tsquery('english', :any)) AS rank
        FROM document_chunks
        WHERE org_id = :org_id
          AND tsvector_col @@ to_tsquery('english', :any)
        ORDER BY coverage DESC, rank DESC
        LIMIT :k
    """
    params["any"] = " | ".join(sanitized)

    try:
        result = await db.execute(text(query_sql), params)
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
        raw = await asyncio.wait_for(
            orchestrator._call_llm(
                prompt=user_prompt,
                system_prompt=system_prompt,
                json_mode=True,
            ),
            timeout=RERANK_HARD_TIMEOUT_SECONDS,
        )
        if not raw:
            logger.debug("rerank_chunks: LLM returned empty, fallback to BM25")
            return chunks[:top_k]

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
        scores_map = parsed.get("scores", {})
        if not isinstance(scores_map, dict):
            logger.debug("rerank_chunks: invalid scores format, fallback to BM25")
            return chunks[:top_k]

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
    except asyncio.TimeoutError:
        logger.debug("rerank_chunks timeout after %ss, fallback to BM25", RERANK_HARD_TIMEOUT_SECONDS)
        return chunks[:top_k]
    except Exception as e:
        logger.debug(f"rerank_chunks failed (fallback to BM25): {e}")
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


# ─── Knowledge-context storage (org_configs.knowledge_context) ─────────────


async def get_document_overview(
    db: AsyncSession, org_id: str, k: int = 3
) -> List[Dict]:
    """Fetch the opening chunks of the most recently uploaded document.

    Used as a fallback when keyword search finds nothing but the user is
    clearly asking about a source itself ("summarize the pdf I uploaded") —
    whole-document questions rarely match per-chunk keywords.
    """
    try:
        row = (
            await db.execute(
                text(
                    "SELECT doc_id FROM document_chunks "
                    "WHERE org_id = :org_id AND source_type <> 'website' "
                    "GROUP BY doc_id ORDER BY MAX(created_at) DESC LIMIT 1"
                ),
                {"org_id": str(org_id)},
            )
        ).mappings().first()
        if not row:
            return []
        rows = (
            await db.execute(
                text(
                    "SELECT id, content, source_type, source_url, metadata, doc_id, "
                    "chunk_index, 0.0 AS rank "
                    "FROM document_chunks "
                    "WHERE org_id = :org_id AND doc_id = :doc_id "
                    "ORDER BY chunk_index ASC LIMIT :k"
                ),
                {"org_id": str(org_id), "doc_id": row["doc_id"], "k": k},
            )
        ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error(f"get_document_overview failed for org_id={org_id}: {exc}")
        return []


async def newest_doc_filename(db: AsyncSession, org_id: str) -> str:
    """Filename of the most recently uploaded document ("" when none)."""
    try:
        return (
            await db.execute(
                text(
                    "SELECT metadata->>'filename' FROM document_chunks "
                    "WHERE org_id = :org_id AND source_type <> 'website' "
                    "AND metadata->>'filename' IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"org_id": str(org_id)},
            )
        ).scalar() or ""
    except Exception as exc:
        logger.error(f"newest_doc_filename failed for org_id={org_id}: {exc}")
        return ""


async def list_knowledge_sources(db: AsyncSession, org_id: str) -> List[str]:
    """Human-readable list of this org's knowledge sources (docs + site)."""
    lines: List[str] = []
    try:
        result = await db.execute(
            text(
                "SELECT metadata->>'filename' AS filename, MAX(created_at) AS last_ts "
                "FROM document_chunks "
                "WHERE org_id = :org_id AND source_type <> 'website' "
                "GROUP BY doc_id, metadata->>'filename' "
                "ORDER BY MAX(created_at) DESC LIMIT 10"
            ),
            {"org_id": str(org_id)},
        )
        for r in result.mappings().all():
            if r["filename"]:
                lines.append(f"- Document: {r['filename']}")
        url = (
            await db.execute(
                text("SELECT website_url FROM org_configs WHERE org_id = :org_id"),
                {"org_id": str(org_id)},
            )
        ).scalar()
        if url:
            lines.append(f"- Website: {url}")
    except Exception as exc:
        logger.error(f"list_knowledge_sources failed for org_id={org_id}: {exc}")
    return lines


def _empty_knowledge_context() -> Dict[str, Any]:
    return {"business_profile": {}, "sources": {}}


async def save_business_profile(db: AsyncSession, org_id: str, profile: Dict[str, Any]) -> None:
    """Persist (or replace) the business profile on org_configs.knowledge_context."""
    result = await db.execute(
        text("SELECT knowledge_context FROM org_configs WHERE org_id = :org_id"),
        {"org_id": str(org_id)},
    )
    row = result.mappings().first()
    ctx = dict(row["knowledge_context"]) if row and isinstance(row["knowledge_context"], dict) else _empty_knowledge_context()
    sources = ctx.get("sources") if isinstance(ctx.get("sources"), dict) else {}
    ctx["business_profile"] = profile
    ctx["sources"] = sources

    await db.execute(
        text(
            "UPDATE org_configs SET knowledge_context = CAST(:ctx AS JSONB) "
            "WHERE org_id = :org_id"
        ),
        {"org_id": str(org_id), "ctx": json.dumps(ctx)},
    )


async def get_business_profile(db: AsyncSession, org_id: str) -> Dict[str, Any]:
    """Read the stored business profile for an org ({} when unset)."""
    result = await db.execute(
        text("SELECT knowledge_context FROM org_configs WHERE org_id = :org_id"),
        {"org_id": str(org_id)},
    )
    row = result.mappings().first()
    ctx = row["knowledge_context"] if row else None
    if not isinstance(ctx, dict):
        return {}
    profile = ctx.get("business_profile")
    return profile if isinstance(profile, dict) else {}


async def upsert_source_context(
    db: AsyncSession, org_id: str, source_key: str, entry: Dict[str, Any]
) -> None:
    """Store per-source context collected before ingestion.

    ``source_key`` is a doc id or ``"website"`` for the crawled site.
    """
    result = await db.execute(
        text("SELECT knowledge_context FROM org_configs WHERE org_id = :org_id"),
        {"org_id": str(org_id)},
    )
    row = result.mappings().first()
    ctx = dict(row["knowledge_context"]) if row and isinstance(row["knowledge_context"], dict) else _empty_knowledge_context()
    raw_sources = ctx.get("sources")
    sources = dict(raw_sources) if isinstance(raw_sources, dict) else {}
    sources[source_key] = entry
    raw_profile = ctx.get("business_profile")
    ctx["business_profile"] = (
        dict(raw_profile) if isinstance(raw_profile, dict) else {}
    )
    ctx["sources"] = sources

    await db.execute(
        text(
            "UPDATE org_configs SET knowledge_context = CAST(:ctx AS JSONB) "
            "WHERE org_id = :org_id"
        ),
        {"org_id": str(org_id), "ctx": json.dumps(ctx)},
    )


async def get_stored_source_context(
    db: AsyncSession, org_id: str, source_key: str
) -> Dict[str, Any]:
    """Read back a single per-source context entry ({} when unset)."""
    result = await db.execute(
        text("SELECT knowledge_context FROM org_configs WHERE org_id = :org_id"),
        {"org_id": str(org_id)},
    )
    row = result.mappings().first()
    ctx = row["knowledge_context"] if row else None
    if not isinstance(ctx, dict):
        return {}
    sources = ctx.get("sources")
    if not isinstance(sources, dict):
        return {}
    entry = sources.get(source_key)
    return entry if isinstance(entry, dict) else {}


async def delete_source_context_entry(db: AsyncSession, org_id: str, source_key: str) -> None:
    """Remove a per-source context entry (used when a source is deleted)."""
    result = await db.execute(
        text("SELECT knowledge_context FROM org_configs WHERE org_id = :org_id"),
        {"org_id": str(org_id)},
    )
    row = result.mappings().first()
    ctx = row["knowledge_context"] if row else None
    if not isinstance(ctx, dict):
        return
    sources = ctx.get("sources")
    if not isinstance(sources, dict) or source_key not in sources:
        return
    sources = dict(sources)
    sources.pop(source_key, None)
    ctx = dict(ctx)
    ctx["sources"] = sources
    await db.execute(
        text(
            "UPDATE org_configs SET knowledge_context = CAST(:ctx AS JSONB) "
            "WHERE org_id = :org_id"
        ),
        {"org_id": str(org_id), "ctx": json.dumps(ctx)},
    )
