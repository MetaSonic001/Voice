"""Settings, read from environment variables and `.env`. Every tunable and every provider choice lives here."""

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # ── LLM ── provider: "openai" = any OpenAI-compatible API (Groq, OpenAI, vLLM...) | "bedrock" = AWS Bedrock
    llm_provider: str = "openai"
    groq_api_key: SecretStr = SecretStr("")  # key for the OpenAI-compatible endpoint; never printed
    llm_base_url: str = "https://api.groq.com/openai/v1"
    understand_model: str = "openai/gpt-oss-20b"  # Bedrock: an India geo profile ID (in.…); never apac./global (ADR 0026)
    answer_model: str = "openai/gpt-oss-120b"
    llm_timeout_s: float = 20.0
    llm_retry_budget_s: float = 4.0  # max total wait for retries (429/5xx) per call; then fail fast with a clear reply
    judge_model: str = "openai/gpt-oss-20b"  # post-call judge and eval judge: a different model from the agent's (120b), so its quota is separate. llama-3.3-70b is no longer offered (404, 6 Nov 2026)
    llm_replay_dir: Path | None = None  # evals only: cache every real LLM response on disk, keyed by the exact request
    llm_fallback: str = ""  # JSON map {"primary model": "fallback model"}; only models that passed the smoke set (PHASE-4-PLAN §7.3)
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
    max_upload_mb: int = 100
    chunk_target_words: int = 180
    chunk_max_words: int = 260
    product_title_prefixes: str = "AU "  # bold headings starting with this are product-level titles ('|'-separated)

    # ── Voice (only loaded when voice_enabled) ──
    voice_enabled: bool = True
    stt_provider: str = "groq"  # groq (Whisper API) | sarvam
    tts_provider: str = "edge"  # edge (free, dev only) | sarvam
    stt_model: str = "whisper-large-v3-turbo"  # sarvam: saaras:v3
    stt_vocabulary: str = "extended"  # base (product names) | extended (+ banking terms) | none; "extended" won the A/B test of 6 Nov (evals/stt_ab.py)
    sarvam_api_key: SecretStr = SecretStr("")
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_speaker: str = "priya"
    # Edge voices per reply language. Neerja: Indian English and the fastest Edge voice (~1 s to first audio);
    # the "Expressive" and "Multilingual" voices sound a little more natural but take 3-5 s. Production: Sarvam.
    tts_voice_en: str = "en-IN-NeerjaNeural"
    tts_voice_hi: str = "hi-IN-SwaraNeural"
    tts_voice_hinglish: str = "en-IN-NeerjaNeural"
    tts_speed_pct: int = 10  # speak this much faster than the voice's default (Edge rate / Sarvam pace)
    audio_rate: int = 16000  # PCM16 mono in and out of the voice API
    vad_start_ms: int = 200  # speech must last this long to count as speech (ignores clicks/noise bursts)
    end_silence_ms: int = 700  # silence that ends a turn when the transcript looks complete
    max_end_silence_ms: int = 1500  # wait this long when the caller paused mid-sentence ("aur...", "and...")
    max_utterance_s: float = 30.0
    bargein_min_ms: int = 350  # caller speech while the agent talks must last this long to lower the agent's volume
    no_input_s: float = 8.0  # re-prompt after this much silence; end the call after the second time
    filler_after_ms: int = 1200  # say "one moment" if the first sentence isn't ready by then
    playback_lead_ms: int = 200  # how far ahead of real time agent audio is sent (smaller = faster barge-in)

    # ── Call audit: recordings ──
    record_calls: bool = True  # live calls (stereo) and voice notes are recorded, encrypted, under data_dir/recordings
    recording_key: SecretStr = SecretStr("")  # base64 of 32 bytes (AES-256); empty = a local key file (dev only)
    judge_mode: str = "all"  # post-call review: all (every call, development) | selective (risk-weighted + 10% sample) | off
    recording_retention_days: int = 0  # delete recordings older than this; 0 = keep (set by the bank's policy)

    # ── Personal questions (MVP 2): mock core banking + caller verification ──
    policy_pack: bool = True  # add seed/service-policies.md (synthetic, labelled) to the knowledge base at startup
    desk_hours: str = "00:00-23:59"  # when the agent desk is staffed (fraud is always 24x7); e.g. "08:00-20:00"
    handoff_wait_s: int = 180  # on hold this long with nobody picking up -> a callback is booked instead
    bank_provider: str = "http"  # http (the bank's systems over HTTP: the mock bank) | mock (in-process, no HTTP) | off
    bank_base_url: str = ""  # empty = start the mock bank systems inside this process; else AU's (or a separate mock) gateway
    bank_service_token: SecretStr = SecretStr("")  # brain -> bank systems; empty = random for this process (embedded mock bank)
    bank_token_secret: SecretStr = SecretStr("")  # signs customer-scoped tokens; empty = random for this process
    bank_token_ttl_s: int = 900  # customer token lifetime (about the length of a call)
    bank_timeout_s: float = 3.0  # per bank call; reads retry once, writes retry with the same Idempotency-Key
    mockbank_port: int = 0  # embedded mock bank: 0 = any free port; set 8100 to browse its /docs
    mockbank_latency_ms: str = "5-25"  # simulated time per bank call
    mock_bank_fail: str = ""  # fault injection, e.g. "cards:lost_response,cbs:latency=400"; or read | card_update | all
    bank_data_file: Path = ROOT.parent / "Sample Questions" / "Inbound_Voice_Agent_Dummy_Data.xlsx"  # "" = off
    demo_today: date = Field(default_factory=date.today)  # "today" for date questions; the mock bank is seeded relative to it
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
