"""Settings, read from environment variables and `.env`. Every tunable and every provider choice lives here."""

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # ── LLM ── provider: "openai" = any OpenAI-compatible API (Groq, OpenAI, vLLM...) | "bedrock" = AWS Bedrock
    llm_provider: str = "openai"
    groq_api_key: SecretStr = SecretStr("")  # key for the OpenAI-compatible endpoint; never printed
    llm_base_url: str = "https://api.groq.com/openai/v1"
    understand_model: str = "openai/gpt-oss-20b"  # Bedrock example: apac.anthropic.claude-haiku-4-5-...
    answer_model: str = "openai/gpt-oss-120b"
    llm_timeout_s: float = 20.0
    llm_retry_budget_s: float = 4.0  # max total wait for retries (429/5xx) per call; then fail fast with a clear reply
    aws_region: str = "ap-south-1"  # Bedrock and S3; credentials come from the standard AWS chain

    # ── Local models ──
    embed_model: str = "BAAI/bge-m3"
    embed_dim: int = 1024
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    device: str = "auto"  # auto = GPU (fp16) when available, else CPU; or force "cpu" / "cuda"

    # ── Storage ──
    data_dir: Path = ROOT / "data"
    database_url: str = ""  # empty = private Postgres (pgserver) under data_dir/pg
    file_store: str = "local"  # local | s3 (AWS S3 or any S3-compatible store such as MinIO)
    s3_bucket: str = ""
    s3_prefix: str = "brain/"
    s3_endpoint_url: str = ""  # e.g. http://127.0.0.1:9000 for MinIO; empty = AWS

    # ── Retrieval and answering ──
    dense_query: str = "english"  # embed the English rewrite ("english") or the caller's own words ("original")
    dense_k: int = 30
    keyword_k: int = 30
    rerank_candidates: int = 16
    rerank_max_tokens: int = 384
    context_chunks: int = 5
    min_rerank_score: float = 0.03  # below this, the top chunk is not trusted (refuse)
    history_turns: int = 6  # recent turns the understanding step sees; caller facts (senior citizen...) last the whole call

    # ── Ingestion ──
    max_upload_mb: int = 25
    chunk_target_words: int = 180
    chunk_max_words: int = 260
    product_title_prefixes: str = "AU "  # bold headings starting with this are product-level titles ('|'-separated)

    # ── Voice (only loaded when voice_enabled) ──
    voice_enabled: bool = True
    stt_provider: str = "groq"  # groq (Whisper API) | sarvam
    tts_provider: str = "edge"  # edge (free, dev only) | sarvam
    stt_model: str = "whisper-large-v3-turbo"  # sarvam: saaras:v3
    sarvam_api_key: SecretStr = SecretStr("")
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_speaker: str = "priya"
    tts_voice_en: str = "en-IN-NeerjaNeural"  # Edge voices per reply language
    tts_voice_hi: str = "hi-IN-SwaraNeural"
    tts_voice_hinglish: str = "en-IN-NeerjaNeural"
    audio_rate: int = 16000  # PCM16 mono in and out of the voice API
    vad_start_ms: int = 200  # speech must last this long to count as speech (ignores clicks/noise bursts)
    end_silence_ms: int = 700  # silence that ends a turn when the transcript looks complete
    max_end_silence_ms: int = 1500  # wait this long when the caller paused mid-sentence ("aur...", "and...")
    max_utterance_s: float = 30.0
    bargein_min_ms: int = 350  # caller speech while the agent talks must last this long to pause the agent
    no_input_s: float = 8.0  # re-prompt after this much silence; end the call after the second time
    filler_after_ms: int = 1200  # say "one moment" if the first sentence isn't ready by then
    playback_lead_ms: int = 200  # how far ahead of real time agent audio is sent (smaller = faster barge-in)

    # ── Personal questions (MVP 2): mock core banking + caller verification ──
    bank_data_file: Path = ROOT.parent / "Sample Questions" / "Inbound_Voice_Agent_Dummy_Data.xlsx"  # "" = off
    demo_today: date = date(2026, 12, 31)  # the dummy transactions run through 2026; "today" for date questions
    otp_delivery: str = "simulated"  # simulated = the code is shown in the test UI (dev only) | sms (gateway, later)
    otp_ttl_s: int = 300
    otp_max_attempts: int = 3  # wrong codes before verification locks for the session
    otp_max_sends: int = 3
    auth_idle_s: int = 600  # verification ends after this much inactivity
    demo_callers: bool = True  # GET /v1/demo/callers lists test customers for the UI's "calling from" picker

    # ── API / ops ──
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"  # the test UI (comma-separated); "" = none
    api_key: str = ""  # if set, write endpoints and voice need header X-API-Key
    rate_limit_per_min: int = 120  # per client, on /v1/ask and voice endpoints; 0 = off
    log_level: str = "INFO"
    log_console: str = "text"  # text (readable) | json (for log shippers); logs/app.jsonl is always JSON
    log_dir: Path = ROOT / "logs"
    worker_poll_s: float = 1.0

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


@lru_cache
def get_settings() -> Settings:
    return Settings()
