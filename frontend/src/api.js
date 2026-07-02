const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

function friendlyError(message) {
  if (message === "Failed to fetch") {
    return `Cannot reach the AuditLens backend at ${API_BASE_URL}. Confirm the FastAPI server is running.`;
  }
  if (message.includes("Upload the bill as JSON, CSV, XLSX")) {
    return "Upload the bill as JSON, CSV, XLSX, PDF, or image. Use the demo bill file or a billing export with CPT code, units, and charge columns.";
  }
  if (message.includes("Could not parse uploaded bill JSON")) {
    return "The bill JSON could not be parsed or does not match the required claim schema.";
  }
  if (message.includes("missing required column")) {
    return message;
  }
  if (message.includes("XLSX bill parsing requires openpyxl")) {
    return "XLSX bill parsing needs the backend spreadsheet dependency. Install backend requirements and retry.";
  }
  if (message.includes("Could not find a readable bill table")) {
    return "AuditLens could not read a claim-line table from that PDF/image bill. Use the demo export layout, CSV, or XLSX.";
  }
  if (message.includes("OCR failed for uploaded bill image")) {
    return "Bill image OCR failed. Confirm Tesseract is installed and try a clearer scan, PDF, CSV, or XLSX bill.";
  }
  if (message.includes("accepts record files")) {
    return "Upload the clinical record as TXT, PDF, PNG, JPG, TIF, or TIFF.";
  }
  if (message.includes("OCR failed")) {
    return "Image OCR failed. Confirm Tesseract is installed and try a clearer scan or the PDF/TXT record.";
  }
  return message;
}

async function readError(response) {
  let detail = `Backend returned ${response.status}`;
  const payload = await response.json().catch(() => null);
  if (payload) {
    detail = payload.detail || detail;
  }
  return friendlyError(detail);
}

async function fetchJson(url, options = {}) {
  try {
    const response = await fetch(url, options);
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    return response.json();
  } catch (error) {
    throw new Error(friendlyError(error.message || "Request failed."));
  }
}

export async function checkBackendHealth() {
  return fetchJson(`${API_BASE_URL}/health`);
}

export async function fetchEvaluationResults() {
  return fetchJson(`${API_BASE_URL}/evaluation/results`);
}

export async function analyzeUploadedClaim({ billFile, recordFile }) {
  const formData = new FormData();
  formData.append("bill", billFile);
  formData.append("record", recordFile);

  return fetchJson(`${API_BASE_URL}/analyze/upload`, {
    method: "POST",
    body: formData
  });
}

export async function analyzeSampleClaim({ recordSource = "scanned" } = {}) {
  return fetchJson(`${API_BASE_URL}/analyze/sample/${encodeURIComponent(recordSource)}`, {
    method: "POST"
  });
}

export async function previewBillExtraction({ billFile }) {
  const formData = new FormData();
  formData.append("bill", billFile);

  return fetchJson(`${API_BASE_URL}/preview/bill`, {
    method: "POST",
    body: formData
  });
}

export async function askRecordQuestion({ claimId, question }) {
  return fetchJson(`${API_BASE_URL}/chat/${encodeURIComponent(claimId)}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ question })
  });
}

export async function recordReviewerAction({ claimId, lineId, action, reviewerNote = "" }) {
  return fetchJson(`${API_BASE_URL}/action`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      claim_id: claimId,
      line_id: lineId,
      action,
      reviewer_note: reviewerNote || null
    })
  });
}
