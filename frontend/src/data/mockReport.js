const deterministicConfidenceBreakdown = {
  rule_match: "Deterministic (certain)",
  retrieval_score: null,
  retrieval_passages_found: 0,
  llm_verdict: "Not applicable",
  conflicting_evidence: false
};

function aiConfidenceBreakdown(retrievalScore, passagesFound, llmVerdict, conflictingEvidence = false) {
  return {
    rule_match: "Not applicable",
    retrieval_score: retrievalScore,
    retrieval_passages_found: passagesFound,
    llm_verdict: llmVerdict,
    conflicting_evidence: conflictingEvidence
  };
}

export const mockReport = {
  claim_id: "CLM-1001",
  generated_at: "2026-06-29T12:00:00Z",
  total_charge: 2275,
  dollars_at_risk: 2020,
  total_billed: 2275,
  recommended_payment: 255,
  potential_savings: 2020,
  savings_percentage: 88.8,
  risk_score: 100,
  recommended_action: "escalate",
  summary:
    "AuditLens found 8 review finding(s) across 6 of 8 billed line(s). Priority issues include no documentation, unsupported, ncci bundle. Estimated dollars at risk: $2,020.00. Risk score: 100/100. Recommended next action: escalate.",
  claim_narrative:
    "The priority concern is CT head without contrast code 70450 for $950.00, because the clinical record states that no CT head was ordered, performed, or discussed. Several additional lines also create payment exposure, including code 93005 billed separately from the complete electrocardiogram and code 99215 billed above the documented visit complexity. In plain terms, the claim includes services that are either not documented, not separately payable as submitted, or not supported by the diagnoses in the record. Recommended next action is to escalate the claim because $2,020.00 of the $2,275.00 total charge remains at risk and should not be released without senior review.",
  metrics: {
    total_lines: 8,
    supported_lines: 2,
    flagged_lines: 6,
    high_severity_flags: 3,
    medium_severity_flags: 5,
    low_severity_flags: 0,
    flag_counts_by_rule: {
      DUPLICATE: 1,
      MEDICAL_NECESSITY: 2,
      MUE_UNITS: 1,
      NCCI_BUNDLE: 1,
      NO_DOCUMENTATION: 1,
      UNSUPPORTED: 1,
      UPCODE: 1
    },
    flag_counts_by_severity: {
      high: 3,
      medium: 5
    },
    dollars_at_risk_by_rule: {
      DUPLICATE: 180,
      MEDICAL_NECESSITY: 1370,
      MUE_UNITS: 120,
      NCCI_BUNDLE: 40,
      NO_DOCUMENTATION: 950,
      UNSUPPORTED: 40,
      UPCODE: 310
    },
    top_risk_lines: ["L7", "L4", "L1", "L6", "L5"]
  },
  documentation_gaps: [
    {
      service_name: "Established patient office visit level 5",
      cpt_code: "99215",
      required_documentation:
        "Documentation should support the billed visit complexity for Established patient office visit level 5 code 99215, including the clinical decision making, problem severity, data reviewed, and risk level needed for the submitted service level.",
      what_is_present: "The visit involved moderate complexity.",
      gap_type: "Complexity gap",
      dollar_amount: 310
    },
    {
      service_name: "Electrocardiogram tracing only",
      cpt_code: "93005",
      required_documentation:
        "Documentation should clearly show that Electrocardiogram tracing only code 93005 was ordered, performed, clinically relevant to the visit, and attributable to this service date.",
      what_is_present: "Electrocardiogram was performed and interpreted in clinic because the patient reported chest discomfort.",
      gap_type: "Partial gap",
      dollar_amount: 40
    },
    {
      service_name: "CT head without contrast",
      cpt_code: "70450",
      required_documentation:
        "Documentation should show that code 70450 was ordered or performed and tied to a supporting diagnosis such as R41.82, R51.9, S09.90XA. CT head is supported by headache head injury or altered mental status in this prototype.",
      what_is_present: "No CT head was ordered, performed, or discussed.",
      gap_type: "Complete gap",
      dollar_amount: 950
    },
    {
      service_name: "Complete abdominal ultrasound",
      cpt_code: "76700",
      required_documentation:
        "Documentation should show that code 76700 was ordered or performed and tied to a supporting diagnosis such as K80.20, R10.9, R94.5. Complete abdominal ultrasound is supported by abdominal pain gallbladder disease or abnormal liver studies in this prototype.",
      what_is_present: "Complete abdominal ultrasound was performed on 2025-03-04 after patient concern about nausea.",
      gap_type: "Partial gap",
      dollar_amount: 420
    }
  ],
  claim: {
    claim_id: "CLM-1001",
    service_date: "2025-03-04",
    patient: {
      patient_id: "PAT-001",
      name: "Jordan Miles",
      date_of_birth: "1977-09-18"
    },
    provider: {
      provider_id: "PRV-4421",
      name: "Northstar Family Clinic",
      specialty: "Family Medicine"
    },
    lines: [
      {
        line_id: "L1",
        code: "99215",
        code_system: "CPT",
        description: "Established patient office visit level 5",
        units: 1,
        charge: 310,
        service_date: null,
        modifiers: [],
        place_of_service: "11"
      },
      {
        line_id: "L2",
        code: "71046",
        code_system: "CPT",
        description: "Chest X-ray 2 views",
        units: 1,
        charge: 180,
        service_date: null,
        modifiers: [],
        place_of_service: "11"
      },
      {
        line_id: "L3",
        code: "93000",
        code_system: "CPT",
        description: "Electrocardiogram complete",
        units: 1,
        charge: 75,
        service_date: null,
        modifiers: [],
        place_of_service: "11"
      },
      {
        line_id: "L4",
        code: "93005",
        code_system: "CPT",
        description: "Electrocardiogram tracing only",
        units: 1,
        charge: 40,
        service_date: null,
        modifiers: [],
        place_of_service: "11"
      },
      {
        line_id: "L5",
        code: "36415",
        code_system: "CPT",
        description: "Venipuncture",
        units: 4,
        charge: 120,
        service_date: null,
        modifiers: [],
        place_of_service: "11"
      },
      {
        line_id: "L6",
        code: "71046",
        code_system: "CPT",
        description: "Chest X-ray 2 views",
        units: 1,
        charge: 180,
        service_date: null,
        modifiers: [],
        place_of_service: "11"
      },
      {
        line_id: "L7",
        code: "70450",
        code_system: "CPT",
        description: "CT head without contrast",
        units: 1,
        charge: 950,
        service_date: null,
        modifiers: [],
        place_of_service: "11"
      },
      {
        line_id: "L8",
        code: "76700",
        code_system: "CPT",
        description: "Complete abdominal ultrasound",
        units: 1,
        charge: 420,
        service_date: null,
        modifiers: [],
        place_of_service: "11"
      }
    ]
  },
  evidence: {
    documented_diagnoses: [
      { code: "J20.9", description: "Acute bronchitis, unspecified", page: 1 },
      { code: "R05.9", description: "Cough, unspecified", page: 1 },
      { code: "R07.89", description: "Other chest pain", page: 1 },
      { code: "R11.0", description: "Nausea without vomiting", page: 1 },
      { code: "I10", description: "Essential hypertension", page: 1 }
    ],
    documented_procedures: [
      { code: "71046", description: "Chest X-ray, two views", page: 2 },
      { code: "93000", description: "Electrocardiogram complete", page: 2 },
      { code: "36415", description: "Venipuncture", page: 2 },
      { code: "76700", description: "Complete abdominal ultrasound", page: 2 }
    ],
    documented_visit_complexity: {
      supported_level: "moderate",
      supported_code: "99214",
      source_span: "The visit involved moderate complexity.",
      page: 2
    }
  },
  flags: [
    {
      line_id: "L1",
      status: "rule_violation",
      rule: "UPCODE",
      severity: "medium",
      citation: "The visit involved moderate complexity.",
      page: 2,
      confidence: 0.92,
      confidence_breakdown: aiConfidenceBreakdown(0.34, 2, "upcode"),
      recommended_action: "escalate",
      message: "Line L1 bills 99215, but documentation supports 99214 based on moderate complexity."
    },
    {
      line_id: "L4",
      status: "unsupported",
      rule: "UNSUPPORTED",
      severity: "medium",
      citation: "Electrocardiogram was performed and interpreted in clinic because the patient reported chest discomfort.",
      page: 2,
      confidence: 0.87,
      confidence_breakdown: aiConfidenceBreakdown(0.28, 2, "unsupported"),
      recommended_action: "escalate",
      message: "The record supports a complete ECG, not a separately documented tracing-only ECG service."
    },
    {
      line_id: "L7",
      status: "unsupported",
      rule: "NO_DOCUMENTATION",
      severity: "high",
      citation: "No CT head was ordered, performed, or discussed.",
      page: 2,
      confidence: 0.95,
      confidence_breakdown: aiConfidenceBreakdown(0.52, 3, "not supported"),
      recommended_action: "request_records",
      message: "The clinical record states that no CT head was ordered, performed, or discussed."
    },
    {
      line_id: "L6",
      status: "rule_violation",
      rule: "DUPLICATE",
      severity: "medium",
      citation: null,
      page: null,
      confidence: 1,
      confidence_breakdown: deterministicConfidenceBreakdown,
      recommended_action: "deny_line",
      message: "Line L6 duplicates line L2 for code 71046 on 2025-03-04."
    },
    {
      line_id: "L5",
      status: "rule_violation",
      rule: "MUE_UNITS",
      severity: "medium",
      citation: null,
      page: null,
      confidence: 1,
      confidence_breakdown: deterministicConfidenceBreakdown,
      recommended_action: "deny_line",
      message: "Code 36415 was billed with 4 units, exceeding the prototype limit of 1."
    },
    {
      line_id: "L4",
      status: "rule_violation",
      rule: "NCCI_BUNDLE",
      severity: "high",
      citation: null,
      page: null,
      confidence: 1,
      confidence_breakdown: deterministicConfidenceBreakdown,
      recommended_action: "deny_line",
      message: "Code 93005 on line L4 is bundled into code 93000 on line L3."
    },
    {
      line_id: "L7",
      status: "rule_violation",
      rule: "MEDICAL_NECESSITY",
      severity: "high",
      citation: null,
      page: null,
      confidence: 1,
      confidence_breakdown: deterministicConfidenceBreakdown,
      recommended_action: "escalate",
      message: "Code 70450 lacks a documented diagnosis from the allowed set."
    },
    {
      line_id: "L8",
      status: "rule_violation",
      rule: "MEDICAL_NECESSITY",
      severity: "medium",
      citation: "Complete abdominal ultrasound was performed on 2025-03-04 after patient concern about nausea.",
      page: 2,
      confidence: 1,
      confidence_breakdown: deterministicConfidenceBreakdown,
      recommended_action: "request_records",
      message: "Code 76700 lacks a documented diagnosis from the allowed set."
    }
  ],
  line_results: [
    {
      line_id: "L1",
      code: "99215",
      status: "rule_violation",
      recommended_action: "escalate",
      key_finding_summary: "Visit complexity does not support the billed level.",
      flags: []
    },
    {
      line_id: "L2",
      code: "71046",
      status: "supported",
      recommended_action: "pay",
      key_finding_summary: "No issues found — documentation supports this service.",
      flags: []
    },
    {
      line_id: "L3",
      code: "93000",
      status: "supported",
      recommended_action: "pay",
      key_finding_summary: "No issues found — documentation supports this service.",
      flags: []
    },
    {
      line_id: "L4",
      code: "93005",
      status: "multiple_issues",
      recommended_action: "escalate",
      key_finding_summary: "Service appears included in another billed procedure.",
      flags: []
    },
    {
      line_id: "L5",
      code: "36415",
      status: "rule_violation",
      recommended_action: "deny_line",
      key_finding_summary: "Billed units exceed the allowed daily quantity.",
      flags: []
    },
    {
      line_id: "L6",
      code: "71046",
      status: "rule_violation",
      recommended_action: "deny_line",
      key_finding_summary: "This service appears billed more than once.",
      flags: []
    },
    {
      line_id: "L7",
      code: "70450",
      status: "multiple_issues",
      recommended_action: "escalate",
      key_finding_summary: "No record support was found for this billed service.",
      flags: []
    },
    {
      line_id: "L8",
      code: "76700",
      status: "rule_violation",
      recommended_action: "request_records",
      key_finding_summary: "Required diagnosis support is missing from the record.",
      flags: []
    }
  ]
};
