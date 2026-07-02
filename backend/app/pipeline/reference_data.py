from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import REFERENCE_DIR


@dataclass(frozen=True)
class MueLimit:
    code: str
    code_system: str
    max_units_per_day: int
    rule_description: str


@dataclass(frozen=True)
class NcciPair:
    primary_code: str
    bundled_code: str
    modifier_allowed: bool
    rule_description: str


@dataclass(frozen=True)
class MedicalNecessityRule:
    procedure_code: str
    allowed_diagnosis_codes: set[str]
    rule_description: str


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


@lru_cache
def load_mue_limits(reference_dir: Path = REFERENCE_DIR) -> dict[str, MueLimit]:
    rows = _read_csv(reference_dir / "mue_limits.csv")
    return {
        row["code"]: MueLimit(
            code=row["code"],
            code_system=row["code_system"],
            max_units_per_day=int(row["max_units_per_day"]),
            rule_description=row["rule_description"],
        )
        for row in rows
    }


@lru_cache
def load_ncci_pairs(reference_dir: Path = REFERENCE_DIR) -> list[NcciPair]:
    rows = _read_csv(reference_dir / "ncci_pairs.csv")
    return [
        NcciPair(
            primary_code=row["primary_code"],
            bundled_code=row["bundled_code"],
            modifier_allowed=row["modifier_allowed"].strip().lower() == "true",
            rule_description=row["rule_description"],
        )
        for row in rows
    ]


@lru_cache
def load_medical_necessity_rules(
    reference_dir: Path = REFERENCE_DIR,
) -> dict[str, MedicalNecessityRule]:
    rows = _read_csv(reference_dir / "medical_necessity_rules.csv")
    return {
        row["procedure_code"]: MedicalNecessityRule(
            procedure_code=row["procedure_code"],
            allowed_diagnosis_codes=set(row["allowed_diagnosis_codes"].split("|")),
            rule_description=row["rule_description"],
        )
        for row in rows
    }
