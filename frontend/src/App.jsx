import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  CircleDollarSign,
  ClipboardCheck,
  Cpu,
  FileScan,
  FileText,
  Gauge,
  MessageSquareText,
  PlayCircle,
  RefreshCw,
  Save,
  Send,
  ShieldCheck,
  Upload,
  X
} from "lucide-react";
import {
  analyzeSampleClaim,
  analyzeUploadedClaim,
  askRecordQuestion,
  checkBackendHealth,
  fetchEvaluationResults,
  previewBillExtraction,
  recordReviewerAction
} from "./api.js";

const severityTone = {
  high: "danger",
  medium: "warning",
  low: "neutral",
  none: "good"
};

const statusLabel = {
  supported: "Supported",
  unsupported: "Unsupported",
  rule_violation: "Rule issue",
  needs_review: "Needs review",
  multiple_issues: "Multiple issues"
};

const actionLabel = {
  pay: "Pay",
  request_records: "Request records",
  deny_line: "Deny line",
  escalate: "Escalate"
};

const reviewTabs = [
  { id: "overview", label: "Overview" },
  { id: "lines", label: "Billed lines" },
  { id: "gaps", label: "Documentation gaps" },
  { id: "evidence", label: "Evidence" }
];

const processingSteps = [
  { label: "Reading uploaded bill", detail: "Claim lines, charges, codes" },
  { label: "Extracting clinical evidence", detail: "Diagnoses, procedures, dates" },
  { label: "Retrieving record passages", detail: "Top supporting passages" },
  { label: "Verifying billed lines", detail: "Bounded LLM checks" },
  { label: "Applying rule guardrails", detail: "Duplicate, MUE, NCCI, necessity" },
  { label: "Generating reviewer packet", detail: "Risk, citations, actions" }
];

const chartColors = {
  accent: "#10715C",
  critical: "#B24A38",
  warning: "#B58824",
  neutral: "#8B857A"
};

function displayText(value) {
  return String(value ?? "").replace(/[—–]/g, "-").replaceAll("·", "/");
}

function currency(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value || 0);
}

function fileSize(file) {
  if (!file) return "";
  if (file.size < 1024) return `${file.size} B`;
  if (file.size < 1024 * 1024) return `${(file.size / 1024).toFixed(1)} KB`;
  return `${(file.size / (1024 * 1024)).toFixed(1)} MB`;
}

function claimTotal(claim) {
  return (claim?.lines || []).reduce((total, line) => total + Number(line.charge || 0), 0);
}

function normalizedPreviewClaim(claim) {
  return {
    ...claim,
    lines: (claim?.lines || []).map((line) => ({
      ...line,
      units: Math.max(1, Number.parseInt(line.units, 10) || 1),
      charge: Math.max(0, Number.parseFloat(line.charge) || 0)
    }))
  };
}

function claimPreviewFile(claim) {
  const normalizedClaim = normalizedPreviewClaim(claim);
  return new File([JSON.stringify(normalizedClaim, null, 2)], `${normalizedClaim.claim_id || "claim"}_confirmed_bill.json`, {
    type: "application/json"
  });
}

function niceRule(rule) {
  return (rule || "OTHER").replaceAll("_", " ");
}

function flagsForLine(report, lineId) {
  return report.flags.filter((flag) => flag.line_id === lineId);
}

function traceForLine(report, lineId) {
  return report.ai_traces?.find((trace) => trace.line_id === lineId) || null;
}

function keyFindingForResult(result) {
  if (result?.key_finding_summary) return displayText(result.key_finding_summary);
  if (result?.status === "supported") return "No issues found. Documentation supports this service.";
  return "Review needed before payment.";
}

function titleCase(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatConfidencePercent(value) {
  return `${Math.round((Number(value) || 0) * 100)}%`;
}

function percent(value) {
  return `${((Number(value) || 0) * 100).toFixed(1)}%`;
}

function fallbackBreakdownForFlag(flag) {
  if (flag?.confidence_breakdown) return flag.confidence_breakdown;
  return {
    rule_match: flag?.confidence === 1 ? "Deterministic (certain)" : "Not applicable",
    retrieval_score: null,
    retrieval_passages_found: 0,
    llm_verdict: flag?.confidence === 1 ? "Not applicable" : "uncertain",
    conflicting_evidence: false
  };
}

function breakdownFromTrace(trace) {
  if (!trace) return null;
  const bestScore = trace.retrieved_passages?.length
    ? Math.max(...trace.retrieved_passages.map((passage) => Number(passage.score || 0)))
    : null;
  return {
    rule_match: "Not applicable",
    retrieval_score: bestScore,
    retrieval_passages_found: (trace.retrieved_passages || []).filter((passage) => Number(passage.score || 0) >= 0.05).length,
    llm_verdict: trace.llm_supported ? "supported" : titleCase(trace.llm_issue).toLowerCase(),
    conflicting_evidence: false
  };
}

function breakdownFromChat(message) {
  return {
    rule_match: "Not applicable",
    retrieval_score: null,
    retrieval_passages_found: message?.citation ? 1 : 0,
    llm_verdict: message?.confidence >= 0.5 ? "answered from record" : "uncertain",
    conflicting_evidence: false
  };
}

function confidenceBreakdownRows(breakdown) {
  const safeBreakdown = breakdown || {
    rule_match: "Not available",
    retrieval_score: null,
    retrieval_passages_found: 0,
    llm_verdict: "uncertain",
    conflicting_evidence: false
  };

  return [
    {
      label: "Rule check",
      value: safeBreakdown.rule_match || "Not applicable",
      warning: false
    },
    {
      label: "Best evidence match",
      value:
        safeBreakdown.retrieval_score === null || safeBreakdown.retrieval_score === undefined
          ? "No retrieval used"
          : formatConfidencePercent(safeBreakdown.retrieval_score),
      warning: safeBreakdown.retrieval_score === null || safeBreakdown.retrieval_score === undefined
    },
    {
      label: "Passages found",
      value: `${safeBreakdown.retrieval_passages_found || 0} above threshold`,
      warning: !safeBreakdown.retrieval_passages_found
    },
    {
      label: "AI verdict",
      value: titleCase(safeBreakdown.llm_verdict || "uncertain"),
      warning: String(safeBreakdown.llm_verdict || "").toLowerCase().includes("uncertain")
    },
    {
      label: "Conflicting evidence found",
      value: safeBreakdown.conflicting_evidence ? "Yes" : "No",
      warning: Boolean(safeBreakdown.conflicting_evidence)
    }
  ];
}

function ConfidenceTooltip({ value, breakdown, suffix = "" }) {
  return (
    <span className="confidence-tooltip">
      <button className="confidence-trigger" type="button" aria-label="Show confidence breakdown">
        {formatConfidencePercent(value)}
        {suffix}
      </button>
      <span className="confidence-popover" role="tooltip">
        <strong>How we got here</strong>
        {confidenceBreakdownRows(breakdown).map((row) => (
          <span className="confidence-row" key={row.label}>
            {row.warning ? <AlertTriangle size={13} /> : <CheckCircle2 size={13} />}
            <span>
              <em>{row.label}</em>
              <b>{row.value}</b>
            </span>
          </span>
        ))}
      </span>
    </span>
  );
}

function Header({ health, onHome }) {
  const statusLabel = health.status === "online" ? "Live" : health.status === "offline" ? "Offline" : "Checking";

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button className="brand-zone" onClick={onHome}>
          <span className="brand-mark" aria-hidden="true">
            <span />
          </span>
          <span className="brand-name">AuditLens</span>
        </button>
      </div>
      <div className="topbar-actions">
        <div className={`live-status ${health.status}`}>
          <span aria-hidden="true" />
          <strong>{statusLabel}</strong>
        </div>
      </div>
    </header>
  );
}

function SampleDemoPanel({ loading, onRunSample }) {
  return (
    <section className="sample-panel">
      <div className="sample-row">
        <div>
          <span className="sample-kicker">Sample run</span>
          <strong>Test AuditLens with built-in documents</strong>
          <p>Use a predefined bill and clinical record to see the full review workflow without uploading files.</p>
        </div>
        <button className="primary-button" onClick={onRunSample} disabled={loading}>
          {loading ? <RefreshCw size={17} /> : <PlayCircle size={17} />}
          <span>{loading ? "Running" : "Run sample demo"}</span>
        </button>
      </div>
    </section>
  );
}

function IntakeHero() {
  return (
    <section className="intake-hero">
      <div className="hero-copy">
        <span>Payment integrity workbench</span>
        <h1>Read the record before you pay the bill.</h1>
        <p>Detect unsupported billing before payment is released, with every finding tied back to the clinical record.</p>
      </div>
      <div className="hero-signal-grid" aria-label="AuditLens operating model">
        <div>
          <span>01</span>
          <strong>Clinical evidence first</strong>
        </div>
        <div>
          <span>02</span>
          <strong>LLM bounded by rules</strong>
        </div>
        <div>
          <span>03</span>
          <strong>Cited reviewer packet</strong>
        </div>
      </div>
    </section>
  );
}

function EvaluationPage({ report }) {
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError("");
    fetchEvaluationResults()
      .then((payload) => {
        if (mounted) setResults(payload);
      })
      .catch((err) => {
        if (mounted) setError(err.message || "Evaluation results failed to load.");
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  if (loading) {
    return (
      <section className="evaluation-card">
        <RefreshCw size={20} />
        <strong>Loading evaluation metrics</strong>
      </section>
    );
  }

  if (error || !results) {
    return <div className="error-banner">{error || "Evaluation results are unavailable."}</div>;
  }

  const rows = [
    {
      metric: "Precision",
      system: results.precision,
      baseline: results.baseline_precision,
      higherIsBetter: true
    },
    {
      metric: "Recall",
      system: results.recall,
      baseline: results.baseline_recall,
      higherIsBetter: true
    },
    {
      metric: "F1 Score",
      system: results.f1_score,
      baseline: results.baseline_f1,
      higherIsBetter: true
    },
    {
      metric: "False Alarm Rate",
      system: results.false_alarm_rate,
      baseline: results.baseline_false_alarm_rate,
      higherIsBetter: false
    }
  ];

  return (
    <section className="evaluation-page">
      <section className="evaluation-hero">
        <div className="mono-kicker">Model benchmark</div>
        <h2>
          On the synthetic benchmark, AuditLens raised <span>zero false alarms</span> where naive prompting raised one in five.
        </h2>
        <p>
          These metrics come from a fixed labeled test set, not from the current claim
          {report?.claim_id ? ` (${report.claim_id})` : ""}.
        </p>
      </section>

      <section className="evaluation-card">
        <div className="panel-heading">
          <ClipboardCheck size={18} />
          <h2>Pipeline vs Plain LLM Baseline</h2>
        </div>
        <div className="evaluation-table">
          <div className="evaluation-row evaluation-head">
            <span>Metric</span>
            <span>AuditLens AI</span>
            <span>Plain LLM Baseline</span>
          </div>
          {rows.map((row) => {
            const systemWins = row.higherIsBetter ? row.system >= row.baseline : row.system <= row.baseline;
            return (
              <div className="evaluation-row" key={row.metric}>
                <span>{row.metric}</span>
                <span className={systemWins ? "metric-win" : ""}>{percent(row.system)}</span>
                <span>{percent(row.baseline)}</span>
              </div>
            );
          })}
        </div>
      </section>

      <section className="evaluation-note">
        Evaluated on {results.total_cases} synthetic labeled cases with planted ground-truth billing errors.
        These benchmark results intentionally stay the same across uploaded claims because precision, recall, and F1 require labeled ground truth.
        Synthetic data only. Real-world performance would require clinical data partnerships and additional validation.
      </section>

      <section className="evaluation-card">
        <div className="panel-heading">
          <Activity size={18} />
          <h2>Confusion Matrix</h2>
        </div>
        <div className="confusion-grid">
          <div>
            <span>True Positives</span>
            <strong>{results.true_positives}</strong>
          </div>
          <div>
            <span>False Positives</span>
            <strong>{results.false_positives}</strong>
          </div>
          <div>
            <span>False Negatives</span>
            <strong>{results.false_negatives}</strong>
          </div>
          <div>
            <span>True Negatives</span>
            <strong>{results.true_negatives}</strong>
          </div>
        </div>
      </section>
    </section>
  );
}

function UploadDrop({ file, label, detail, accept, icon: Icon, onChange }) {
  return (
    <label className={`upload-drop ${file ? "filled" : ""}`}>
      <input
        type="file"
        accept={accept}
        onChange={(event) => {
          onChange(event.target.files?.[0] || null);
        }}
      />
      <div>
        <span className="upload-step">{label.includes("bill") ? "Step 1" : "Step 2"}</span>
        <strong>{file ? file.name : label}</strong>
        <span>{file ? fileSize(file) : detail}</span>
      </div>
    </label>
  );
}

function BillPreviewPanel({
  billFile,
  previewClaim,
  previewLoading,
  previewError,
  previewConfirmed,
  onPreview,
  onConfirm,
  onLineChange
}) {
  const hasPreview = Boolean(previewClaim);

  return (
    <section className={`intake-panel preview-panel ${previewConfirmed ? "confirmed" : ""}`}>
      <div className="panel-heading preview-heading">
        <div>
          <FileText size={18} />
          <h2>Bill Extraction Preview</h2>
        </div>
        {previewConfirmed ? (
          <span className="preview-badge">
            <Check size={14} />
            Confirmed
          </span>
        ) : null}
      </div>

      {!billFile ? (
        <div className="preview-empty">Upload a bill to extract claim lines before running review.</div>
      ) : null}

      {billFile && !hasPreview ? (
        <div className="preview-start">
          <div>
            <strong>{billFile.name}</strong>
            <span>Extract claim, patient, provider, service lines, units, and charges.</span>
          </div>
          <button className="primary-button" onClick={onPreview} disabled={previewLoading}>
            {previewLoading ? <RefreshCw size={17} /> : <FileText size={17} />}
            <span>{previewLoading ? "Extracting" : "Extract bill lines"}</span>
          </button>
        </div>
      ) : null}

      {previewError ? <div className="preview-error">{previewError}</div> : null}

      {hasPreview ? (
        <>
          <div className="preview-summary">
            <div>
              <span>Claim</span>
              <strong>{previewClaim.claim_id}</strong>
            </div>
            <div>
              <span>Patient</span>
              <strong>{previewClaim.patient?.name || "Unknown"}</strong>
            </div>
            <div>
              <span>Lines</span>
              <strong>{previewClaim.lines.length}</strong>
            </div>
            <div>
              <span>Total charge</span>
              <strong>{currency(claimTotal(previewClaim))}</strong>
            </div>
          </div>

          <div className="preview-table">
            <div className="preview-row preview-head">
              <span>Line</span>
              <span>Code</span>
              <span>Description</span>
              <span>Units</span>
              <span>Charge</span>
            </div>
            {previewClaim.lines.map((line) => (
              <div className="preview-row" key={line.line_id}>
                <span className="preview-line-id">{line.line_id}</span>
                <input
                  value={line.code}
                  onChange={(event) => onLineChange(line.line_id, "code", event.target.value)}
                  aria-label={`${line.line_id} code`}
                />
                <input
                  value={line.description}
                  onChange={(event) => onLineChange(line.line_id, "description", event.target.value)}
                  aria-label={`${line.line_id} description`}
                />
                <input
                  value={line.units}
                  onChange={(event) => onLineChange(line.line_id, "units", event.target.value)}
                  aria-label={`${line.line_id} units`}
                  inputMode="numeric"
                />
                <input
                  value={line.charge}
                  onChange={(event) => onLineChange(line.line_id, "charge", event.target.value)}
                  aria-label={`${line.line_id} charge`}
                  inputMode="decimal"
                />
              </div>
            ))}
          </div>

          <div className="preview-actions">
            <button className="ghost-button" onClick={onPreview} disabled={previewLoading}>
              {previewLoading ? <RefreshCw size={16} /> : <RefreshCw size={16} />}
              <span>Re-extract</span>
            </button>
            <button className="primary-button" onClick={onConfirm}>
              <Check size={17} />
              <span>{previewConfirmed ? "Confirmed" : "Confirm bill"}</span>
            </button>
          </div>
        </>
      ) : null}
    </section>
  );
}

function IntakePanel({
  billFile,
  setBillFile,
  recordFile,
  setRecordFile,
  loading,
  error,
  onRun,
  billPreview,
  previewLoading,
  previewError,
  previewConfirmed,
  onPreviewBill,
  onConfirmPreview,
  onPreviewLineChange,
  onRunSample
}) {
  const ready = Boolean(billPreview && previewConfirmed && recordFile);

  return (
    <section className="intake-layout">
      <IntakeHero />
      <div className="intake-main">
        {error ? <div className="error-banner">{error}</div> : null}
        <SampleDemoPanel loading={loading} onRunSample={onRunSample} />
        <section className="intake-panel custom-review-panel">
          <h2>Custom review</h2>
          <p className="section-note">Bring your own bill and clinical record. AuditLens extracts the bill first, then validates it against the uploaded documentation.</p>
          <div className="upload-grid">
            <UploadDrop
              file={billFile}
              label="Upload bill"
              detail="JSON, CSV, XLSX, PDF, PNG, JPG, or TIFF"
              accept=".json,.csv,.xlsx,.pdf,.png,.jpg,.jpeg,.tif,.tiff,application/json,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/pdf,image/*"
              icon={FileText}
              onChange={setBillFile}
            />
            <UploadDrop
              file={recordFile}
              label="Upload clinical record"
              detail="TXT, PDF, PNG, JPG, or TIFF"
              accept=".txt,.pdf,.png,.jpg,.jpeg,.tif,.tiff,application/pdf,image/*"
              icon={FileScan}
              onChange={setRecordFile}
            />
          </div>
        </section>

        <BillPreviewPanel
          billFile={billFile}
          previewClaim={billPreview}
          previewLoading={previewLoading}
          previewError={previewError}
          previewConfirmed={previewConfirmed}
          onPreview={onPreviewBill}
          onConfirm={onConfirmPreview}
          onLineChange={onPreviewLineChange}
        />
      </div>

      <footer className="run-panel">
        <div className="run-summary">
          <div>
            <span>Bill</span>
            <strong>{billFile ? billFile.name : "Not uploaded"}</strong>
          </div>
          <div>
            <span>Bill preview</span>
            <strong>{previewConfirmed ? "Confirmed" : billPreview ? "Needs confirmation" : "Not extracted"}</strong>
          </div>
          <div>
            <span>Clinical record</span>
            <strong>{recordFile ? recordFile.name : "Not uploaded"}</strong>
          </div>
          <div>
            <span>Verifier</span>
            <strong>OpenRouter LLM + rules</strong>
          </div>
        </div>
        <button className="primary-button run-button" onClick={onRun} disabled={loading || !ready}>
          {loading ? <RefreshCw size={18} /> : <PlayCircle size={18} />}
          <span>{loading ? "Analyzing" : "Run claim review"}</span>
        </button>
      </footer>
    </section>
  );
}

function ProcessingPanel({ activeStep, title }) {
  return (
    <section className="processing-panel">
      <div className="processing-head">
        <div>
          <div className="eyebrow">Running analysis</div>
          <h2>{title || "Claim packet"}</h2>
        </div>
        <div className="source-pill">
          <Cpu size={15} />
          <span>Live analysis</span>
        </div>
      </div>
      <div className="step-list">
        {processingSteps.map((step, index) => {
          const done = index < activeStep;
          const active = index === activeStep;
          return (
            <div className={`step-row ${done ? "done" : ""} ${active ? "active" : ""}`} key={step.label}>
              <div className="step-icon">
                {done ? <Check size={16} /> : active ? <RefreshCw size={16} /> : <span>{index + 1}</span>}
              </div>
              <div>
                <strong>{step.label}</strong>
                <span>{step.detail}</span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function MetricCard({ icon: Icon, label, value, subtext, tone = "default" }) {
  return (
    <section className={`metric-card ${tone}`}>
      <div className="metric-icon">
        <Icon size={20} />
      </div>
      <div>
        <div className="metric-label">{label}</div>
        <div className="metric-value">{value}</div>
        <div className="metric-subtext">{subtext}</div>
      </div>
    </section>
  );
}

function AuditSummaryCard({ report }) {
  const narrative = report.claim_narrative || report.summary;

  return (
    <section className="audit-summary-card">
      <div className="panel-heading">
        <ShieldCheck size={18} />
        <h2>Audit Summary</h2>
      </div>
      <p>{displayText(narrative)}</p>
    </section>
  );
}

function Dashboard({ report }) {
  const ruleData = Object.entries(report.metrics.flag_counts_by_rule).map(([rule, count]) => ({
    rule: niceRule(rule),
    count,
    pct: Math.min(100, (count / 2) * 100)
  }));
  const severityData = Object.entries(report.metrics.flag_counts_by_severity).map(([severity, count]) => ({
    name: severity,
    value: count,
    pct: Math.min(100, (count / 4) * 100),
    color: severity === "high" ? chartColors.critical : severity === "medium" ? chartColors.warning : chartColors.accent
  }));

  return (
    <>
      <section className="dashboard-grid">
        <MetricCard icon={Gauge} label="Risk score" value={`${report.risk_score}`} subtext={actionLabel[report.recommended_action]} tone="danger" />
        <MetricCard icon={CircleDollarSign} label="Dollars at risk" value={currency(report.dollars_at_risk)} subtext={`of ${currency(report.total_charge)} claimed`} />
        <MetricCard icon={AlertTriangle} label="Findings" value={report.flags.length} subtext={`across ${report.metrics.flagged_lines} flagged lines`} tone="warning" />
        <MetricCard icon={CheckCircle2} label="Supported" value={`${report.metrics.supported_lines}/${report.metrics.total_lines}`} subtext="billed lines clear" tone="good" />
      </section>
      <AuditSummaryCard report={report} />
      <section className="chart-grid">
        <section className="chart-panel">
          <div className="mono-kicker">Findings by rule</div>
          <div className="bar-list">
            {ruleData.map((item) => (
              <div className="bar-row" key={item.rule}>
                <span>{item.rule}</span>
                <div className="bar-track">
                  <div style={{ width: `${item.pct}%` }} />
                </div>
                <strong>{item.count}</strong>
              </div>
            ))}
          </div>
        </section>
        <section className="chart-panel">
          <div className="mono-kicker">Severity of findings</div>
          <div className="severity-bars">
            {severityData.map((item) => (
              <div key={item.name}>
                <div className="severity-label">
                  <span>{titleCase(item.name)}</span>
                  <strong>{item.value}</strong>
                </div>
                <div className="bar-track">
                  <div style={{ width: `${item.pct}%`, background: item.color }} />
                </div>
              </div>
            ))}
          </div>
        </section>
      </section>
    </>
  );
}

function ClaimContext({ report }) {
  return (
    <section className="context-grid">
      <div className="context-block">
        <div className="context-label">Patient</div>
        <div className="context-value">{report.claim.patient.name}</div>
        <div className="context-muted">{report.claim.patient.patient_id}</div>
      </div>
      <div className="context-block">
        <div className="context-label">Provider</div>
        <div className="context-value">{report.claim.provider.name}</div>
        <div className="context-muted">{report.claim.provider.specialty}</div>
      </div>
      <div className="context-block">
        <div className="context-label">Service date</div>
        <div className="context-value">{report.claim.service_date}</div>
        <div className="context-muted">{report.claim.claim_id}</div>
      </div>
      <div className="context-block">
        <div className="context-label">Evidence</div>
        <div className="context-value">{report.evidence.documented_procedures.length} procedures</div>
        <div className="context-muted">{report.evidence.documented_diagnoses.length} diagnoses</div>
      </div>
    </section>
  );
}

function VerdictBanner({ report }) {
  return (
    <section className="verdict-banner">
      <div>
        <div className="mono-kicker">Recommended action</div>
        <h3>{titleCase(actionLabel[report.recommended_action] || "Escalate")} this claim for senior review.</h3>
      </div>
      <div className="verdict-figures">
        <div>
          <span>Submitted</span>
          <strong>{currency(report.total_billed ?? report.total_charge)}</strong>
        </div>
        <div>
          <span>Recommended</span>
          <strong>{currency(report.recommended_payment)}</strong>
        </div>
        <div>
          <span>Potential savings</span>
          <strong>{currency(report.potential_savings)}</strong>
        </div>
      </div>
    </section>
  );
}

function GenAiProcessingPanel({ report }) {
  const metadata = report.processing_metadata;
  if (!metadata) return null;

  const cards = [
    {
      label: "Record input",
      value: titleCase(metadata.record_input_type),
      detail: metadata.record_filename || "Uploaded record"
    },
    {
      label: "OCR",
      value: metadata.ocr_used ? "Used" : "Not needed",
      detail: metadata.ocr_engine || "Digital text path"
    },
    {
      label: "Extraction",
      value: "Clinical facts",
      detail: metadata.extraction_method
    },
    {
      label: "Verification",
      value: `${report.ai_traces?.length || 0} line checks`,
      detail: metadata.verification_method
    }
  ];

  return (
    <section className="genai-strip">
      <div className="panel-heading">
        <Cpu size={18} />
        <h2>GenAI Processing</h2>
      </div>
      <div className="genai-cards">
        {cards.map((card) => (
          <div className="genai-card" key={card.label}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
            <p>{card.detail}</p>
          </div>
        ))}
      </div>
      <div className="guardrail-row">
        {(metadata.rule_guardrails_applied || []).map((rule) => (
          <span key={rule}>{niceRule(rule)}</span>
        ))}
      </div>
    </section>
  );
}

function LineTable({ report, selectedLineId, setSelectedLineId, onOpenDetail }) {
  return (
    <section className="line-ledger">
      <div className="ledger-heading">
        <div>
          <h3>Billed lines</h3>
        </div>
        <div className="line-legend" aria-label="Line status legend">
          <span className="legend-supported">Supported</span>
          <span className="legend-review">Needs review</span>
          <span className="legend-critical">Escalate</span>
        </div>
      </div>
      <div className="line-table">
        <div className="line-row line-head">
          <span>Line</span>
          <span>Code</span>
          <span>Description</span>
          <span>Key Finding</span>
          <span>Charge</span>
          <span>Status</span>
        </div>
        {report.claim.lines.map((line) => {
          const result = report.line_results.find((item) => item.line_id === line.line_id);
          const flags = flagsForLine(report, line.line_id);
          const status = result?.status || (flags.length ? "needs_review" : "supported");
          return (
            <button
              className={`line-row line-button ${selectedLineId === line.line_id ? "selected" : ""}`}
              key={line.line_id}
              onClick={() => {
                setSelectedLineId(line.line_id);
                onOpenDetail();
              }}
            >
              <span>{line.line_id}</span>
              <span className="code">{line.code}</span>
              <span>{line.description}</span>
              <span className={`key-finding ${status === "supported" ? "supported" : ""}`}>{keyFindingForResult(result)}</span>
              <span>{currency(line.charge)}</span>
              <span className={`status ${status}`}>{statusLabel[status]}</span>
            </button>
          );
        })}
      </div>
      <p className="ledger-note">Open a row to inspect evidence, confidence, and reviewer actions for that billed service.</p>
    </section>
  );
}

function DocumentationGaps({ gaps }) {
  if (!gaps?.length) return null;

  return (
    <section className="documentation-gap-panel">
      <div className="section-heading">
        <div>
          <h3>Documentation gaps</h3>
        </div>
      </div>
      <div className="documentation-gap-list">
        {gaps.map((gap) => (
          <article className="documentation-gap-card" key={`${gap.cpt_code}-${gap.service_name}-${gap.gap_type}`}>
            <div className="gap-card-top">
              <div>
                <span className="code">{gap.cpt_code}</span>
                <h4>{gap.service_name}</h4>
              </div>
              <div className="gap-risk">
                <span>Amount at risk</span>
                <strong>{currency(gap.dollar_amount)}</strong>
              </div>
            </div>
            <div className="gap-card-grid">
              <div>
                <span>Required documentation</span>
                <p>{displayText(gap.required_documentation)}</p>
              </div>
              <div>
                <span>What is present</span>
                <p>{displayText(gap.what_is_present)}</p>
              </div>
              <div>
                <span>Gap type</span>
                <b className={`gap-badge ${gap.gap_type.toLowerCase().replaceAll(" ", "-")}`}>{gap.gap_type}</b>
              </div>
            </div>
          </article>
        ))}
      </div>
      <div className="documentation-gap-callout">
        These gaps would not survive a medical record audit request. Consider contacting the provider for supplemental documentation before releasing payment on these lines.
      </div>
    </section>
  );
}

function ReviewerActions({ report, line, savedAction, onRecordAction, actionSaving }) {
  if (!line) return null;
  const actions = ["pay", "request_records", "deny_line", "escalate"];

  return (
    <div className="reviewer-actions">
      <div className="action-button-grid">
        {actions.map((action) => (
          <button
            className={`action-button ${savedAction === action ? "selected" : ""}`}
            key={action}
            onClick={() => onRecordAction(report.claim_id, line.line_id, action)}
            disabled={actionSaving === line.line_id}
          >
            {savedAction === action ? <Check size={15} /> : <Save size={15} />}
            <span>{actionLabel[action]}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function AiTracePanel({ trace }) {
  if (!trace) return null;
  const topPassage = trace.retrieved_passages?.[0];
  const confidenceBreakdown = breakdownFromTrace(trace);

  return (
    <div className="ai-trace-panel">
      <div className="ai-trace-head">
        <div>
          <div className="context-label">AI verification</div>
          <strong>{trace.llm_supported ? "Supported by record" : titleCase(trace.llm_issue)}</strong>
        </div>
        <ConfidenceTooltip value={trace.confidence} breakdown={confidenceBreakdown} />
      </div>
      <div className="trace-meta-grid">
        <div>
          <span>Mode</span>
          <strong>{titleCase(trace.verification_mode)}</strong>
        </div>
        <div>
          <span>Fallback</span>
          <strong>{trace.fallback_used ? "Yes" : "No"}</strong>
        </div>
      </div>
      <p>{displayText(trace.rationale)}</p>
      {topPassage ? (
        <blockquote>
          {displayText(topPassage.text)}
          <span>
            Passage {topPassage.rank}
            {topPassage.page ? ` / Page ${topPassage.page}` : ""}
            {` / Score ${Math.round((topPassage.score || 0) * 100)}%`}
          </span>
        </blockquote>
      ) : null}
      {trace.guardrail_rules?.length ? (
        <div className="trace-guardrails">
          {trace.guardrail_rules.map((rule) => (
            <span key={rule}>{niceRule(rule)}</span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function LineDetailPage({
  report,
  selectedLineId,
  setSelectedLineId,
  reviewerActions,
  onRecordAction,
  actionSaving,
  onBackToLines
}) {
  const selectedFlags = flagsForLine(report, selectedLineId);
  const line = report.claim.lines.find((item) => item.line_id === selectedLineId);
  const savedAction = reviewerActions[`${report.claim_id}:${selectedLineId}`];
  const trace = traceForLine(report, selectedLineId);
  const lineIndex = report.claim.lines.findIndex((item) => item.line_id === selectedLineId);
  const previousLine = report.claim.lines[lineIndex - 1];
  const nextLine = report.claim.lines[lineIndex + 1];

  function moveTo(nextLineId) {
    if (!nextLineId) return;
    setSelectedLineId(nextLineId);
  }

  return (
    <section className="line-detail-page">
      <div className="line-detail-nav">
        <button className="text-button" onClick={onBackToLines}>
          <ArrowLeft size={16} />
          Back to billed lines
        </button>
        <div className="line-pager">
          <button onClick={() => moveTo(previousLine?.line_id)} disabled={!previousLine}>
            Previous
          </button>
          <button onClick={() => moveTo(nextLine?.line_id)} disabled={!nextLine}>
            Next
          </button>
        </div>
      </div>

      <div className="line-detail-layout">
        <div className="line-detail-main">
          {line ? (
            <div className="line-detail-head">
              <div>
                <div className="context-label">
                  {line.line_id} / {line.code}
                </div>
                <h3>{line.description}</h3>
                <p>{keyFindingForResult(report.line_results.find((item) => item.line_id === selectedLineId))}</p>
              </div>
              <div className="amount">{currency(line.charge)}</div>
            </div>
          ) : null}

          <AiTracePanel trace={trace} />
          <div className="flag-stack">
            {selectedFlags.length ? (
              selectedFlags.map((flag) => (
                <article className={`flag-card ${severityTone[flag.severity]}`} key={`${flag.line_id}-${flag.rule}-${flag.message}`}>
                  <div className="flag-topline">
                    <span>{niceRule(flag.rule)}</span>
                    <ConfidenceTooltip value={flag.confidence} breakdown={fallbackBreakdownForFlag(flag)} />
                  </div>
                  <p>{displayText(flag.message)}</p>
                  {flag.citation ? (
                    <blockquote>
                      {displayText(flag.citation)}
                      {flag.page ? <span>Page {flag.page}</span> : null}
                    </blockquote>
                  ) : (
                    <div className="no-citation">No direct citation attached</div>
                  )}
                </article>
              ))
            ) : (
              <article className="empty-state">
                <CheckCircle2 size={22} />
                <span>Supported. The record supports payment for this service.</span>
              </article>
            )}
          </div>
        </div>

        <aside className="line-action-panel">
          <div className="mono-kicker">Reviewer action</div>
          <ReviewerActions
            report={report}
            line={line}
            savedAction={savedAction}
            onRecordAction={onRecordAction}
            actionSaving={actionSaving}
          />
        </aside>
      </div>
    </section>
  );
}

function EvidencePanel({ report }) {
  return (
    <section className="evidence-panel">
      <div className="evidence-columns">
        <article className="evidence-card">
          <div className="evidence-card-head">
            <span>Diagnoses</span>
            <strong>{report.evidence.documented_diagnoses.length}</strong>
          </div>
          {report.evidence.documented_diagnoses.map((item) => (
            <div className="evidence-item" key={item.evidence_id}>
              <span className="code">{item.code}</span>
              <span>{item.description}</span>
            </div>
          ))}
        </article>
        <article className="evidence-card">
          <div className="evidence-card-head">
            <span>Procedures</span>
            <strong>{report.evidence.documented_procedures.length}</strong>
          </div>
          {report.evidence.documented_procedures.map((item) => (
            <div className="evidence-item" key={item.evidence_id}>
              <span className="code">{item.code}</span>
              <span>{item.description}</span>
            </div>
          ))}
        </article>
      </div>
    </section>
  );
}

function RecordChat({ claimId, onClose }) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submitQuestion(event) {
    event.preventDefault();
    const cleanQuestion = question.trim();
    if (!cleanQuestion) return;

    setLoading(true);
    setError("");
    setQuestion("");
    try {
      const response = await askRecordQuestion({ claimId, question: cleanQuestion });
      setMessages((current) => [
        ...current,
        {
          question: cleanQuestion,
          answer: response.answer,
          citation: response.citation,
          page: response.page,
          confidence: response.confidence
        }
      ]);
    } catch (err) {
      setError(err.message || "Record chat failed.");
      setQuestion(cleanQuestion);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="chat-panel">
      <div className="panel-heading chat-drawer-heading">
        <MessageSquareText size={18} />
        <h2>Record Chat</h2>
        <button className="chat-close-button" onClick={onClose} aria-label="Close record chat">
          <X size={18} />
        </button>
      </div>
      <form className="chat-form" onSubmit={submitQuestion}>
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about the uploaded clinical record"
        />
        <button className="primary-button icon-button" type="submit" disabled={loading || !question.trim()}>
          {loading ? <RefreshCw size={17} /> : <Send size={17} />}
        </button>
      </form>
      {error ? <div className="chat-error">{error}</div> : null}
      <div className="chat-stack">
        {messages.length ? (
          messages.map((message, index) => (
            <article className="chat-message" key={`${message.question}-${index}`}>
              <div className="chat-question">{message.question}</div>
              <p>{displayText(message.answer)}</p>
              {message.citation ? (
                <blockquote>
                  {displayText(message.citation)}
                  {message.page ? <span>Page {message.page}</span> : null}
                </blockquote>
              ) : null}
              <div className="chat-confidence">
                <ConfidenceTooltip value={message.confidence} breakdown={breakdownFromChat(message)} suffix=" confidence" />
              </div>
            </article>
          ))
        ) : null}
      </div>
    </section>
  );
}

function FloatingRecordChat({ claimId, open, onOpen, onClose }) {
  return (
    <>
      <button className="floating-chat-button" onClick={onOpen}>
        <MessageSquareText size={19} />
        <span>Record Chat</span>
      </button>
      <aside className={`chat-drawer ${open ? "open" : ""}`} aria-hidden={!open}>
        {open ? <RecordChat claimId={claimId} onClose={onClose} /> : null}
      </aside>
    </>
  );
}

function ReportView({
  report,
  selectedLineId,
  setSelectedLineId,
  reportSub,
  setReportSub,
  reviewerActions,
  onRecordAction,
  actionSaving,
  chatOpen,
  onOpenChat,
  onCloseChat
}) {
  const activeTab = reportSub === "detail" ? "lines" : reportSub;

  return (
    <>
      <section className="review-shell">
        <div className="review-heading">
          <div>
            <div className="review-meta">{report.claim_id} / reviewer packet</div>
            <h2>Claim review</h2>
          </div>
          <nav className="review-subnav" aria-label="Claim review sections">
            {reviewTabs.map((tab) => (
              <button
                className={activeTab === tab.id ? "active" : ""}
                key={tab.id}
                onClick={() => setReportSub(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </div>

        {reportSub === "overview" ? (
          <>
            <VerdictBanner report={report} />
            <ClaimContext report={report} />
            <Dashboard report={report} />
            <button className="dark-cta" onClick={() => setReportSub("lines")}>
              Review billed lines
              <ArrowRight size={16} />
            </button>
          </>
        ) : null}

        {reportSub === "lines" ? (
          <LineTable
            report={report}
            selectedLineId={selectedLineId}
            setSelectedLineId={setSelectedLineId}
            onOpenDetail={() => setReportSub("detail")}
          />
        ) : null}

        {reportSub === "detail" ? (
          <LineDetailPage
            report={report}
            selectedLineId={selectedLineId}
            setSelectedLineId={setSelectedLineId}
            reviewerActions={reviewerActions}
            onRecordAction={onRecordAction}
            actionSaving={actionSaving}
            onBackToLines={() => setReportSub("lines")}
          />
        ) : null}

        {reportSub === "gaps" ? <DocumentationGaps gaps={report.documentation_gaps} /> : null}
        {reportSub === "evidence" ? <EvidencePanel report={report} /> : null}
      </section>
      <FloatingRecordChat claimId={report.claim_id} open={chatOpen} onOpen={onOpenChat} onClose={onCloseChat} />
    </>
  );
}

function delay(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export default function App() {
  const [view, setView] = useState("intake");
  const [report, setReport] = useState(null);
  const [reportSub, setReportSub] = useState("overview");
  const [selectedLineId, setSelectedLineId] = useState("");
  const [billFile, setBillFile] = useState(null);
  const [recordFile, setRecordFile] = useState(null);
  const [billPreview, setBillPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [previewConfirmed, setPreviewConfirmed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activeStep, setActiveStep] = useState(0);
  const [processingTitle, setProcessingTitle] = useState("Claim packet");
  const [error, setError] = useState("");
  const [health, setHealth] = useState({ status: "checking", label: "Checking backend" });
  const [reviewerActions, setReviewerActions] = useState({});
  const [actionSaving, setActionSaving] = useState("");
  const [chatOpen, setChatOpen] = useState(false);

  const topLine = useMemo(() => report?.metrics.top_risk_lines[0] || report?.claim.lines[0]?.line_id || "", [report]);

  useEffect(() => {
    let mounted = true;
    checkBackendHealth()
      .then(() => {
        if (mounted) setHealth({ status: "online", label: "Backend online" });
      })
      .catch(() => {
        if (mounted) setHealth({ status: "offline", label: "Backend offline" });
      });
    return () => {
      mounted = false;
    };
  }, []);

  function handleBillFileChange(file) {
    setBillFile(file);
    setBillPreview(null);
    setPreviewError("");
    setPreviewConfirmed(false);
  }

  async function handlePreviewBill() {
    if (!billFile) {
      setPreviewError("Upload a bill file first.");
      return;
    }

    setPreviewLoading(true);
    setPreviewError("");
    setError("");
    setPreviewConfirmed(false);
    try {
      const preview = await previewBillExtraction({ billFile });
      setBillPreview(preview);
    } catch (err) {
      setBillPreview(null);
      setPreviewError(err.message || "Bill extraction preview failed.");
    } finally {
      setPreviewLoading(false);
    }
  }

  function handlePreviewLineChange(lineId, field, value) {
    setBillPreview((current) => {
      if (!current) return current;
      return {
        ...current,
        lines: current.lines.map((line) => (line.line_id === lineId ? { ...line, [field]: value } : line))
      };
    });
    setPreviewConfirmed(false);
  }

  function handleConfirmPreview() {
    if (!billPreview) return;
    setBillPreview(normalizedPreviewClaim(billPreview));
    setPreviewConfirmed(true);
    setPreviewError("");
  }

  async function runAnalysis() {
    if (!billPreview || !previewConfirmed || !recordFile) {
      setError("Extract and confirm the bill preview, then upload a clinical record.");
      return;
    }

    setLoading(true);
    setError("");
    setActiveStep(0);
    setProcessingTitle("Reviewing uploaded claim");
    setView("processing");

    try {
      for (let index = 0; index < 3; index += 1) {
        setActiveStep(index);
        await delay(620);
      }

      setActiveStep(3);
      const confirmedBillFile = claimPreviewFile(billPreview);
      const nextReport = await analyzeUploadedClaim({ billFile: confirmedBillFile, recordFile });

      for (let index = 4; index < processingSteps.length; index += 1) {
        setActiveStep(index);
        await delay(620);
      }

      setReport(nextReport);
      setSelectedLineId(nextReport.metrics.top_risk_lines[0] || nextReport.claim.lines[0]?.line_id || topLine);
      setReportSub("overview");
      setChatOpen(false);
      setView("report");
    } catch (err) {
      setError(err.message || "Analysis failed.");
      setView("intake");
    } finally {
      setLoading(false);
    }
  }

  async function runSampleAnalysis() {
    setLoading(true);
    setError("");
    setActiveStep(0);
    setProcessingTitle("Running sample review");
    setView("processing");

    try {
      for (let index = 0; index < 3; index += 1) {
        setActiveStep(index);
        await delay(620);
      }

      setActiveStep(3);
      const nextReport = await analyzeSampleClaim({ recordSource: "scanned" });

      for (let index = 4; index < processingSteps.length; index += 1) {
        setActiveStep(index);
        await delay(620);
      }

      setReport(nextReport);
      setSelectedLineId(nextReport.metrics.top_risk_lines[0] || nextReport.claim.lines[0]?.line_id || topLine);
      setReportSub("overview");
      setChatOpen(false);
      setView("report");
    } catch (err) {
      setError(err.message || "Sample demo failed.");
      setView("intake");
    } finally {
      setLoading(false);
    }
  }

  function startNewReview() {
    setError("");
    setReport(null);
    setReportSub("overview");
    setSelectedLineId("");
    setBillFile(null);
    setRecordFile(null);
    setBillPreview(null);
    setPreviewError("");
    setPreviewConfirmed(false);
    setPreviewLoading(false);
    setReviewerActions({});
    setActionSaving("");
    setChatOpen(false);
    setProcessingTitle("Claim packet");
    setView("intake");
  }

  async function handleReviewerAction(claimId, lineId, action) {
    setActionSaving(lineId);
    try {
      await recordReviewerAction({ claimId, lineId, action });
      setReviewerActions((current) => ({
        ...current,
        [`${claimId}:${lineId}`]: action
      }));
    } catch (err) {
      setError(err.message || "Could not save reviewer action.");
    } finally {
      setActionSaving("");
    }
  }

  function returnHome() {
    startNewReview();
    if (window.location.pathname !== "/") {
      window.history.pushState({}, "", "/");
    }
  }

  return (
    <main className="app-shell">
      <Header health={health} onHome={returnHome} />
      {error && view !== "intake" ? <div className="error-banner">{error}</div> : null}
      {view === "intake" ? (
        <IntakePanel
          billFile={billFile}
          setBillFile={handleBillFileChange}
          recordFile={recordFile}
          setRecordFile={setRecordFile}
          loading={loading}
          error={error}
          onRun={runAnalysis}
          billPreview={billPreview}
          previewLoading={previewLoading}
          previewError={previewError}
          previewConfirmed={previewConfirmed}
          onPreviewBill={handlePreviewBill}
          onConfirmPreview={handleConfirmPreview}
          onPreviewLineChange={handlePreviewLineChange}
          onRunSample={runSampleAnalysis}
        />
      ) : null}
      {view === "processing" ? <ProcessingPanel activeStep={activeStep} title={processingTitle} /> : null}
      {view === "report" && report ? (
        <ReportView
          report={report}
          selectedLineId={selectedLineId}
          setSelectedLineId={setSelectedLineId}
          reportSub={reportSub}
          setReportSub={setReportSub}
          reviewerActions={reviewerActions}
          onRecordAction={handleReviewerAction}
          actionSaving={actionSaving}
          chatOpen={chatOpen}
          onOpenChat={() => setChatOpen(true)}
          onCloseChat={() => setChatOpen(false)}
        />
      ) : null}
    </main>
  );
}
