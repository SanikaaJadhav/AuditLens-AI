from pathlib import Path
import os
import re


def _normalize_env_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", key.strip()).strip("_").upper()


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()

        key, value = line.split("=", 1)
        normalized_key = _normalize_env_key(key)
        clean_value = value.strip().strip("\"'")
        if normalized_key and normalized_key not in os.environ:
            os.environ[normalized_key] = clean_value


def _env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(_normalize_env_key(name))
        if value:
            return value.strip()
    return default


APP_NAME = "AuditLens AI"
APP_VERSION = "1.0.0"

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
ENV_FILE = BACKEND_DIR / ".env"
_load_dotenv(ENV_FILE)

DATA_DIR = PROJECT_ROOT / "data"
SAMPLES_DIR = DATA_DIR / "samples"
REFERENCE_DIR = DATA_DIR / "reference"
EVAL_DIR = PROJECT_ROOT / "eval"

SAMPLE_CLAIM_ID = "CLM-1001"
SAMPLE_CLAIM_JSON = SAMPLES_DIR / "claim_CLM-1001.json"
SAMPLE_EVIDENCE_JSON = SAMPLES_DIR / "expected_evidence_CLM-1001.json"
SAMPLE_NOTE_TEXT = SAMPLES_DIR / "clinical_note_CLM-1001.txt"
SAMPLE_NOTE_PDF = SAMPLES_DIR / "clinical_note_CLM-1001.pdf"
SAMPLE_NOTE_SCANNED = SAMPLES_DIR / "clinical_note_CLM-1001_scanned.png"

OPENROUTER_API_KEY = _env_first(
    "OPENROUTER_API_KEY",
    "OPENROUTER_KEY",
    "OPEN_ROUTER_API_KEY",
    "LLM_API_KEY",
    "LLM_API",
    "llm api",
)
OPENROUTER_MODEL = _env_first("OPENROUTER_MODEL", "LLM_MODEL", "MODEL_NAME", "llm model")
LLM_MODE = _env_first("LLM_MODE", default="live" if OPENROUTER_API_KEY and OPENROUTER_MODEL else "mock").lower()
OPENROUTER_BASE_URL = _env_first("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
OPENROUTER_APP_URL = _env_first("OPENROUTER_APP_URL", default="http://localhost:8000")
OPENROUTER_APP_NAME = _env_first("OPENROUTER_APP_NAME", default=APP_NAME)
MAX_UPLOAD_BYTES = int(_env_first("MAX_UPLOAD_BYTES", default=str(10 * 1024 * 1024)))
ALLOWED_ORIGINS_RAW = _env_first("ALLOWED_ORIGINS", "CORS_ORIGINS", default="")
ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in ALLOWED_ORIGINS_RAW.split(",")
    if origin.strip()
]
