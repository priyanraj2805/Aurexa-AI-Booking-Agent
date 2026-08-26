"""Lightweight keyword extractor for BM25 / tsquery construction.

Avoids heavy NLP model dependencies (spaCy) so it works out of the box
on minimal Docker images.
"""

import re
from typing import List

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when",
    "at", "by", "for", "with", "about", "against", "between", "into",
    "through", "during", "before", "after", "above", "below", "to", "from",
    "up", "down", "in", "out", "on", "off", "over", "under", "again",
    "further", "once", "here", "there", "where", "why", "how", "all", "any",
    "both", "each", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very", "s",
    "t", "can", "will", "just", "don", "should", "now", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "having", "do",
    "does", "did", "doing", "i", "you", "he", "she", "it", "we", "they",
    "me", "him", "her", "us", "them", "my", "your", "his", "its", "our",
    "their", "what", "which", "who", "whom", "this", "that", "these",
    "those", "of", "as", "am", "would", "could", "may", "might", "must",
    "shall", "tell", "please", "thanks", "thank",
}

# Characters stripped from word edges before keyword checks. Deliberately
# punctuation-only so scripts whose letters carry combining marks
# (Devanagari matras, Arabic diacritics, ...) stay intact inside tokens.
_EDGE_PUNCT = " \t\r\n.,!?;:\"'`()[]{}<>\u2014\u2013\u2026|\\//*~^%=+#@$&_"

_TOKEN_SPLIT_RE = re.compile(r"\s+")


def _clean_token(raw: str) -> str:
    return raw.strip(_EDGE_PUNCT)


def _has_word_content(tok: str) -> bool:
    return any(ch.isalnum() for ch in tok)


def extract_keywords(query: str, max_keywords: int = 10) -> List[str]:
    """Extract lowercased keywords from the query, dropping stopwords.

    Script-agnostic: splits on whitespace and trims edge punctuation, so
    queries in any language (English, Hindi, Arabic, CJK, ...) yield
    keywords for BM25 retrieval instead of silently returning nothing.
    """
    if not query:
        return []

    seen: set = set()
    keywords: List[str] = []
    for raw in _TOKEN_SPLIT_RE.split(str(query).lower()):
        tok = _clean_token(raw)
        if not tok or len(tok) <= 2:
            continue
        if not _has_word_content(tok):
            continue
        if tok in _STOPWORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        keywords.append(tok)
        if len(keywords) >= max_keywords:
            break

    return keywords
