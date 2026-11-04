"""Composition root: the one place that decides which adapter implements each port.

Providers are picked by config (`.env`); tests pass their own adapters through `overrides`.
With VOICE_ENABLED=false nothing voice-related is imported or loaded (brain-only mode).
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.adapters.parsers import ParserRouter
from app.adapters.storage import LocalFileStore, S3FileStore
from app.config import ROOT, Settings
from app.db import Database
from app.logs import log
from app.ports import LLM, STT, TTS, VAD, Bank, Embedder, FileStore, Reranker
from app.services.auth import AuthService
from app.services.conversation import Conversation
from app.services.documents import DocumentService
from app.services.evals import EvalRunner
from app.services.retrieval import Retriever
from app.services.store import TraceStore
from app.services.worker import IngestWorker

logger = logging.getLogger(__name__)


@dataclass
class VoiceRuntime:
    stt: STT
    tts: TTS
    vad_factory: Callable[[], VAD]  # VAD models are stateful: one per call
    phrases: object  # PhraseCache

    async def close(self) -> None:
        for p in (self.stt, self.tts):
            if hasattr(p, "close"):
                await p.close()


@dataclass
class Container:
    settings: Settings
    db: Database
    embedder: Embedder
    reranker: Reranker
    llm: LLM
    documents: DocumentService
    worker: IngestWorker
    traces: TraceStore
    retriever: Retriever
    conversation: Conversation
    evals: EvalRunner
    voice: VoiceRuntime | None = None
    bank: Bank | None = None  # MVP 2 (personal questions); None = switched off
    auth: AuthService | None = None

    async def close(self) -> None:
        self.worker.stop()
        if hasattr(self.llm, "close"):
            await self.llm.close()
        if self.voice:
            await self.voice.close()
        self.db.close()


def build_llm(s: Settings) -> LLM:
    if s.llm_provider == "bedrock":
        from app.adapters.bedrock import BedrockLLM

        return BedrockLLM(s.aws_region, s.llm_timeout_s)
    if s.llm_provider == "openai":
        from app.adapters.llm import OpenAICompatibleLLM

        return OpenAICompatibleLLM(s.llm_base_url, s.groq_api_key.get_secret_value(), s.llm_timeout_s,
                                   retry_budget_s=s.llm_retry_budget_s)
    raise ValueError(f"unknown LLM_PROVIDER '{s.llm_provider}' (use openai or bedrock)")


def build_store(s: Settings) -> FileStore:
    if s.file_store == "s3":
        return S3FileStore(s.s3_bucket, s.s3_prefix, s.aws_region, s.s3_endpoint_url)
    if s.file_store == "local":
        return LocalFileStore(s.files_dir)
    raise ValueError(f"unknown FILE_STORE '{s.file_store}' (use local or s3)")


def build_voice(s: Settings) -> VoiceRuntime:
    from app.voice.phrases import PhraseCache
    from app.voice.providers import EdgeTTS, GroqSTT, SarvamSTT, SarvamTTS, SileroVAD

    if s.stt_provider == "groq":
        stt = GroqSTT(s.groq_api_key.get_secret_value(), s.llm_base_url, s.stt_model)
    elif s.stt_provider == "sarvam":
        stt = SarvamSTT(s.sarvam_api_key.get_secret_value(), s.stt_model)
    else:
        raise ValueError(f"unknown STT_PROVIDER '{s.stt_provider}' (use groq or sarvam)")
    if s.tts_provider == "edge":
        tts = EdgeTTS({"en": s.tts_voice_en, "hi": s.tts_voice_hi, "hinglish": s.tts_voice_hinglish}, s.audio_rate)
    elif s.tts_provider == "sarvam":
        tts = SarvamTTS(s.sarvam_api_key.get_secret_value(), s.audio_rate, s.sarvam_tts_model, s.sarvam_speaker)
    else:
        raise ValueError(f"unknown TTS_PROVIDER '{s.tts_provider}' (use edge or sarvam)")
    SileroVAD(s.audio_rate)  # load once now so the first call doesn't pay for it
    return VoiceRuntime(stt, tts, lambda: SileroVAD(s.audio_rate), PhraseCache(tts))


def build_bank(s: Settings) -> Bank | None:
    if not str(s.bank_data_file) or not s.bank_data_file.is_file():
        logger.warning("personal questions are off: BANK_DATA_FILE not found (%s)", s.bank_data_file)
        return None
    from app.adapters.bank_excel import ExcelBank

    return ExcelBank(s.bank_data_file)


def build(settings: Settings, *, start_worker: bool = True, overrides: dict | None = None,
          golden_dir: Path | None = None, reports_dir: Path | None = None) -> Container:
    t = time.perf_counter()
    o = overrides or {}
    db = Database(settings.database_url, settings.data_dir)

    if "embedder" in o:
        embedder, reranker = o["embedder"], o["reranker"]
    else:
        from app.adapters.models import CrossEncoderReranker, TransformerEmbedder

        models_dir = ROOT / "models"
        embedder = TransformerEmbedder(settings.embed_model, settings.embed_dim, settings.device, models_dir=models_dir)
        reranker = CrossEncoderReranker(settings.rerank_model, settings.device, settings.rerank_max_tokens, models_dir=models_dir)
        embedder.embed_query("warm up")  # first call is slow; pay it at startup, not on the first question
        reranker.score("warm up", ["warm up"])
    llm = o.get("llm") or build_llm(settings)
    store = o.get("store") or build_store(settings)
    voice = o.get("voice") or (build_voice(settings) if settings.voice_enabled else None)
    bank = o.get("bank") or build_bank(settings)

    db.ensure_vector_index(embedder.name, embedder.dim)
    documents = DocumentService(db, store, ParserRouter(), embedder, settings)
    worker = IngestWorker(db, documents, settings.worker_poll_s)
    traces = TraceStore(db)
    retriever = Retriever(db, embedder, reranker, settings)
    auth = AuthService(db, bank, settings) if bank else None
    conversation = Conversation(llm, retriever, traces, settings, auth)
    evals = EvalRunner(db, conversation, traces, settings, golden_dir or ROOT / "evals" / "golden",
                       reports_dir or ROOT / "evals" / "reports")
    if start_worker:
        worker.start()
    log(logger, "container ready", llm=settings.llm_provider, voice=bool(voice), device=getattr(embedder, "device", "-"),
        startup_ms=round((time.perf_counter() - t) * 1000))
    return Container(settings, db, embedder, reranker, llm, documents, worker, traces, retriever, conversation, evals, voice, bank, auth)
