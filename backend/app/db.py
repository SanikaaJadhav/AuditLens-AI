from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import sqlite3
from pathlib import Path

from .config import PROJECT_ROOT
from .schemas import ClaimReport, ClinicalEvidenceSet, ReviewerAction


DB_PATH = PROJECT_ROOT / "auditlens.sqlite3"


@dataclass(frozen=True)
class StoredClaimArtifacts:
    report: ClaimReport
    record_text: str
    evidence: ClinicalEvidenceSet | None


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS claim_reports (
                claim_id TEXT PRIMARY KEY,
                report_json TEXT NOT NULL,
                record_text TEXT NOT NULL,
                evidence_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reviewer_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                line_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reviewer_note TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def save_claim_artifacts(
    report: ClaimReport,
    record_text: str,
    evidence: ClinicalEvidenceSet | None,
    db_path: Path = DB_PATH,
) -> None:
    initialize_database(db_path)
    now = datetime.utcnow().isoformat()
    evidence_json = evidence.model_dump_json() if evidence is not None else None
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO claim_reports (
                claim_id,
                report_json,
                record_text,
                evidence_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id) DO UPDATE SET
                report_json = excluded.report_json,
                record_text = excluded.record_text,
                evidence_json = excluded.evidence_json,
                updated_at = excluded.updated_at
            """,
            (
                report.claim_id,
                report.model_dump_json(),
                record_text,
                evidence_json,
                now,
                now,
            ),
        )


def load_claim_artifacts(claim_id: str, db_path: Path = DB_PATH) -> StoredClaimArtifacts | None:
    initialize_database(db_path)
    with get_connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT report_json, record_text, evidence_json
            FROM claim_reports
            WHERE claim_id = ?
            """,
            (claim_id,),
        ).fetchone()

    if row is None:
        return None

    evidence = None
    if row["evidence_json"]:
        evidence = ClinicalEvidenceSet.model_validate(json.loads(row["evidence_json"]))

    return StoredClaimArtifacts(
        report=ClaimReport.model_validate(json.loads(row["report_json"])),
        record_text=row["record_text"],
        evidence=evidence,
    )


def save_reviewer_action(action: ReviewerAction, db_path: Path = DB_PATH) -> None:
    initialize_database(db_path)
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO reviewer_actions (
                claim_id,
                line_id,
                action,
                reviewer_note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                action.claim_id,
                action.line_id,
                action.action,
                action.reviewer_note,
                action.created_at.isoformat(),
            ),
        )
