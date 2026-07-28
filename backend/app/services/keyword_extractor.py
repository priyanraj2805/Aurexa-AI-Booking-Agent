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

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]+")


def extract_keywords(query: str, max_keywords: int = 10) -> List[str]:
    """Extract lowercased keywords from the query, dropping stopwords."""
    if not query:
        return []

    tokens = _WORD_RE.findall(query.lower())
    seen: set = set()
    keywords: List[str] = []
    for tok in tokens:
        if tok in _STOPWORDS:
            continue
        if len(tok) <= 2:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        keywords.append(tok)
        if len(keywords) >= max_keywords:
            break

    return keywords
