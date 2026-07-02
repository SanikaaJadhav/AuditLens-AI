from app.pipeline.extraction import load_claim_from_json, load_clinical_evidence_from_json
from app.pipeline.rules import run_medical_necessity_checks, run_rulebook_checks


def test_phase3_deterministic_flags():
    claim = load_claim_from_json()
    evidence = load_clinical_evidence_from_json()

    flags = run_rulebook_checks(claim) + run_medical_necessity_checks(claim, evidence)
    actual = {(flag.line_id, flag.rule) for flag in flags}

    assert ("L4", "NCCI_BUNDLE") in actual
    assert ("L5", "MUE_UNITS") in actual
    assert ("L6", "DUPLICATE") in actual
    assert ("L7", "MEDICAL_NECESSITY") in actual
    assert ("L8", "MEDICAL_NECESSITY") in actual
    assert len(flags) == 5
