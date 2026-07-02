from __future__ import annotations

from typing import Any

from app.config import LLM_MODE
from app.pipeline.llm_client import LLMCallError, LLMConfigurationError, LLMMessage, call_openrouter_json
from app.pipeline.retrieval import RetrievedPassage, chunk_record_text, retrieve_passages
from app.schemas import ChatResponse, ClinicalEvidenceSet, DocumentedEvidence


CHAT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "supported": {"type": "boolean"},
        "answer": {"type": "string"},
        "citation": {"type": ["string", "null"]},
        "page": {"type": ["integer", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["supported", "answer", "citation", "page", "confidence"],
}


def _passage_payload(passages: list[RetrievedPassage]) -> str:
    lines = []
    for index, passage in enumerate(passages, start=1):
        page = passage.page if passage.page is not None else "unknown"
        lines.append(f"[Passage {index} | page {page} | score {passage.score}]\n{passage.text}")
    return "\n\n".join(lines)


def _insufficient_answer(claim_id: str, best_passage: RetrievedPassage | None = None) -> ChatResponse:
    if best_passage and best_passage.score > 0:
        return ChatResponse(
            claim_id=claim_id,
            answer="I could not find enough record support to answer that question confidently.",
            citation=best_passage.text,
            page=best_passage.page,
            confidence=0.25,
        )
    return ChatResponse(
        claim_id=claim_id,
        answer="I could not find support for that answer in the uploaded clinical record.",
        citation=None,
        page=None,
        confidence=0.0,
    )


def _extractive_answer(claim_id: str, passages: list[RetrievedPassage]) -> ChatResponse:
    best_passage = passages[0] if passages else None
    if not best_passage or best_passage.score < 0.08:
        return _insufficient_answer(claim_id, best_passage)

    return ChatResponse(
        claim_id=claim_id,
        answer=f"Based on the retrieved record passage: {best_passage.text}",
        citation=best_passage.text,
        page=best_passage.page,
        confidence=min(0.85, max(0.35, best_passage.score + 0.35)),
    )


def _question_mentions_any(question: str, terms: set[str]) -> bool:
    normalized = question.lower()
    return any(term in normalized for term in terms)


def _format_evidence_items(items: list[DocumentedEvidence]) -> str:
    formatted = []
    for item in items:
        if item.code:
            formatted.append(f"{item.description} ({item.code})")
        else:
            formatted.append(item.description)
    return "; ".join(formatted)


def _answer_from_structured_evidence(
    claim_id: str,
    question: str,
    evidence: ClinicalEvidenceSet | None,
) -> ChatResponse | None:
    if evidence is None:
        return None

    if _question_mentions_any(question, {"diagnosis", "diagnoses", "diagnosed", "icd", "assessment"}):
        if not evidence.documented_diagnoses:
            return ChatResponse(
                claim_id=claim_id,
                answer="The uploaded record does not document any diagnoses in the extracted evidence.",
                citation=None,
                page=None,
                confidence=0.7,
            )
        citation = "; ".join(item.source_span for item in evidence.documented_diagnoses)
        page = next((item.page for item in evidence.documented_diagnoses if item.page is not None), None)
        return ChatResponse(
            claim_id=claim_id,
            answer=f"Documented diagnoses: {_format_evidence_items(evidence.documented_diagnoses)}.",
            citation=citation,
            page=page,
            confidence=0.9,
        )

    if _question_mentions_any(question, {"procedure", "procedures", "documented services"}):
        if not evidence.documented_procedures:
            return ChatResponse(
                claim_id=claim_id,
                answer="The uploaded record does not document any procedures in the extracted evidence.",
                citation=None,
                page=None,
                confidence=0.7,
            )
        citation = "; ".join(item.source_span for item in evidence.documented_procedures)
        page = next((item.page for item in evidence.documented_procedures if item.page is not None), None)
        return ChatResponse(
            claim_id=claim_id,
            answer=f"Documented procedures: {_format_evidence_items(evidence.documented_procedures)}.",
            citation=citation,
            page=page,
            confidence=0.9,
        )

    return None


def _llm_answer(claim_id: str, question: str, passages: list[RetrievedPassage]) -> ChatResponse:
    payload = call_openrouter_json(
        messages=[
            LLMMessage(
                role="system",
                content=(
                    "You are a grounded clinical-record assistant for a payment-integrity prototype. "
                    "Answer only from the provided passages. If the passages do not support an answer, "
                    "set supported=false and say that the record does not support the answer. Do not use "
                    "outside knowledge. Return JSON only."
                ),
            ),
            LLMMessage(
                role="user",
                content=(
                    f"Question: {question}\n\n"
                    "Retrieved clinical record passages:\n"
                    f"{_passage_payload(passages)}"
                ),
            ),
        ],
        json_schema=CHAT_SCHEMA,
        schema_name="cited_record_chat",
        temperature=0.0,
        max_tokens=900,
    )
    if not payload.get("supported"):
        return _insufficient_answer(claim_id, passages[0] if passages else None)
    return ChatResponse(
        claim_id=claim_id,
        answer=str(payload.get("answer", "")).strip(),
        citation=payload.get("citation"),
        page=payload.get("page"),
        confidence=float(payload.get("confidence", 0.5)),
    )


def answer_question_from_record(
    claim_id: str,
    question: str,
    record_text: str,
    evidence: ClinicalEvidenceSet | None = None,
) -> ChatResponse:
    structured_answer = _answer_from_structured_evidence(claim_id, question, evidence)
    if structured_answer is not None:
        return structured_answer

    passages = retrieve_passages(question, chunk_record_text(record_text), top_k=3)
    if not passages:
        return _insufficient_answer(claim_id)

    if LLM_MODE == "live":
        try:
            return _llm_answer(claim_id, question, passages)
        except (LLMConfigurationError, LLMCallError, ValueError, KeyError, TypeError):
            return _extractive_answer(claim_id, passages)

    return _extractive_answer(claim_id, passages)
