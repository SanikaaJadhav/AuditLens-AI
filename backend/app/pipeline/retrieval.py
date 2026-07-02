from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas import ClaimLine


STOPWORDS = {
    "a",
    "and",
    "as",
    "by",
    "complete",
    "for",
    "in",
    "is",
    "of",
    "on",
    "only",
    "patient",
    "service",
    "the",
    "to",
    "was",
    "with",
}

SYNONYMS = {
    "electrocardiogram": {"ecg", "ekg", "cardiac"},
    "ecg": {"electrocardiogram", "ekg", "cardiac"},
    "x": {"xray", "radiograph", "imaging"},
    "ray": {"xray", "radiograph", "imaging"},
    "ct": {"computed", "tomography", "head"},
    "venipuncture": {"venous", "blood", "sample", "collected"},
    "ultrasound": {"abdominal", "sonogram", "imaging"},
    "office": {"visit", "complexity", "decision", "making"},
}


@dataclass(frozen=True)
class RetrievedPassage:
    text: str
    score: float
    page: int | None = None


def _normalize_tokens(text: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[A-Za-z0-9]+", text.lower())
        if len(token) > 1 and token not in STOPWORDS
    }
    expanded = set(tokens)
    for token in tokens:
        expanded.update(SYNONYMS.get(token, set()))
    return expanded


def _current_page(text_before_passage: str) -> int | None:
    matches = list(re.finditer(r"--- Page (\d+) ---", text_before_passage))
    if not matches:
        return None
    return int(matches[-1].group(1))


def chunk_record_text(text: str, chunk_size: int = 450) -> list[RetrievedPassage]:
    raw_parts = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    passages: list[RetrievedPassage] = []
    cursor = 0

    for part in raw_parts:
        part_start = text.find(part, cursor)
        if part_start == -1:
            part_start = cursor
        cursor = part_start + len(part)
        page = _current_page(text[:part_start])

        words = part.split()
        if len(words) <= chunk_size:
            passages.append(RetrievedPassage(text=" ".join(words), score=0.0, page=page))
            continue

        for start in range(0, len(words), chunk_size):
            chunk = " ".join(words[start : start + chunk_size])
            passages.append(RetrievedPassage(text=chunk, score=0.0, page=page))

    return passages


def build_line_query(line: ClaimLine) -> str:
    modifiers = " ".join(line.modifiers)
    return f"{line.code} {line.description} {line.code_system} {modifiers}".strip()


def retrieve_passages(query: str, chunks: list[RetrievedPassage], top_k: int = 3) -> list[RetrievedPassage]:
    query_tokens = _normalize_tokens(query)
    scored: list[RetrievedPassage] = []

    for chunk in chunks:
        passage_tokens = _normalize_tokens(chunk.text)
        if not passage_tokens:
            score = 0.0
        else:
            overlap = query_tokens & passage_tokens
            score = len(overlap) / max(1, len(query_tokens))
            if any(token in passage_tokens for token in {"no", "denies", "not"}):
                score += 0.05
        scored.append(RetrievedPassage(text=chunk.text, score=round(score, 4), page=chunk.page))

    return sorted(scored, key=lambda passage: passage.score, reverse=True)[:top_k]


def retrieve_passages_for_line(line: ClaimLine, record_text: str, top_k: int = 3) -> list[RetrievedPassage]:
    return retrieve_passages(build_line_query(line), chunk_record_text(record_text), top_k=top_k)
