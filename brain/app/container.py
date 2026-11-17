"""Composition root: the one place that decides which adapter implements each port.

Providers are picked by config (`.env`); tests pass their own adapters through `overrides`.
With VOICE_ENABLED=false nothing voice-related is imported or loaded (brain-only mode).
"""

import asyncio
import json
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
from app.services.audit import AuditStore
from app.services.auth import AuthService
from app.services.conversation import Conversation
from app.services.desk import HandoffService, LocalServiceDesk
from app.services.documents import DocumentService
from app.services.evals import EvalRunner
from app.services.journeys import Journeys
from app.services.recordings import RecordingStore
from app.services.review import ReviewService
from app.services.summary import Summariser
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

    async def warm(self) -> None:
        """Open the speech providers' connections when a call starts (saves a TLS handshake on the first turn)."""
        await asyncio.gather(*(p.warm() for p in (self.stt, self.tts) if hasattr(p, "warm")))

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
    audit: AuditStore | None = None
    recordings: RecordingStore | None = None
    service_desk: LocalServiceDesk | None = None
    handoffs: HandoffService | None = None
    bank_server: object | None = None  # the embedded mock bank systems, stopped on close
    reviews: ReviewService | None = None
    summariser: Summariser | None = None

    async def close(self) -> None:
        self.worker.stop()
        if hasattr(self.llm, "close"):
            await self.llm.close()
        if self.voice:
            await self.voice.close()
        if self.bank_server is not None:
            self.bank_server.close()
        if hasattr(self.bank, "close"):
            self.bank.close()
        if hasattr(self.embedder, "client"):  # search server connections (SEARCH_PROVIDER=http)
            self.embedder.client.close()
        self.db.close()


def build_llm(s: Settings) -> LLM:
    llm = _build_provider(s)
    if s.llm_fallback:
        from app.adapters.resilient import ResilientLLM

        llm = ResilientLLM(llm, json.loads(s.llm_fallback))
    if s.llm_replay_dir:
        from app.adapters.replay import ReplayLLM

        llm = ReplayLLM(llm, s.llm_replay_dir)
    return llm


def _build_provider(s: Settings) -> LLM:
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


def build_recording_files(s: Settings) -> FileStore:
    """Where encrypted call recordings are kept: data_dir/recordings on this machine (laptop; Docker with a volume),
    or <S3_PREFIX>recordings/ in the bucket (AWS), so they survive restarts and deploys and every copy can play them."""
    if s.file_store == "s3":
        if s.record_calls and not s.recording_key.get_secret_value():
            # the dev fallback (a key file on local disk) would be lost with the container, and with it every recording
            raise ValueError("RECORDING_KEY is required when FILE_STORE=s3 and RECORD_CALLS=true "
                             "(base64 of 32 random bytes, from the key vault)")
        return S3FileStore(s.s3_bucket, s.s3_prefix + "recordings/", s.aws_region, s.s3_endpoint_url)
    if s.file_store == "local":
        return LocalFileStore(s.data_dir / "recordings")
    raise ValueError(f"unknown FILE_STORE '{s.file_store}' (use local or s3)")


def build_search(s: Settings) -> tuple[Embedder, Reranker]:
    """The embedder and reranker: loaded in this process (local), or on the GPU search server (http)."""
    if s.search_provider == "http":
        from app.adapters.search_http import HttpEmbedder, HttpReranker, SearchClient

        client = SearchClient(s.search_url, s.search_token.get_secret_value(), embed_model=s.embed_model,
                              dim=s.embed_dim, rerank_model=s.rerank_model, timeout_s=s.search_timeout_s,
                              ingest_timeout_s=s.search_ingest_timeout_s)
        if client.try_verify():  # wakes the server's GPU too, like the local warm-up below
            client.post("/v1/rerank", {"query": "warm up", "passages": ["warm up"]})
        return HttpEmbedder(client), HttpReranker(client)
    if s.search_provider != "local":
        raise ValueError(f"unknown SEARCH_PROVIDER '{s.search_provider}' (use local or http)")
    from app.adapters.models import CrossEncoderReranker, TransformerEmbedder

    models_dir = ROOT / "models"
    embedder = TransformerEmbedder(s.embed_model, s.embed_dim, s.device, models_dir=models_dir)
    reranker = CrossEncoderReranker(s.rerank_model, s.device, s.rerank_max_tokens, models_dir=models_dir)
    embedder.embed_query("warm up")  # first call is slow; pay it at startup, not on the first question
    reranker.score("warm up", ["warm up"])
    return embedder, reranker


def build_stt(s: Settings, provider: str) -> STT:
    from app.voice.providers import GroqSTT, SarvamSTT

    if provider == "groq":
        return GroqSTT(s.groq_api_key.get_secret_value(), s.llm_base_url, s.stt_model, vocabulary=s.stt_vocabulary)
    if provider == "sarvam":
        from app.voice.vocab import terms_for

        return SarvamSTT(s.sarvam_api_key.get_secret_value(), s.sarvam_stt_model, s.sarvam_stt_mode,
                         keyterms=terms_for(s.stt_vocabulary) if s.stt_vocabulary != "none" else [])
    raise ValueError(f"unknown STT_PROVIDER '{provider}' (use groq or sarvam)")


def build_tts(s: Settings, provider: str) -> TTS:
    from app.voice.providers import EdgeTTS

    if provider == "edge":
        return EdgeTTS({"en": s.tts_voice_en, "hi": s.tts_voice_hi, "hinglish": s.tts_voice_hinglish}, s.audio_rate,
                       s.tts_speed_pct)
    if provider == "sarvam":
        return build_sarvam_tts(s, s.sarvam_tts_model)
    raise ValueError(f"unknown TTS_PROVIDER '{provider}' (use edge or sarvam)")


def build_sarvam_tts(s: Settings, model: str) -> TTS:
    """Bulbul with the voices that fit `model`: personas per language for v4, one bare name for v3 / v2."""
    from app.voice.providers import SarvamTTS

    if model.startswith("bulbul:v4"):
        speakers = dict(pair.split(":", 1) for pair in s.sarvam_v4_speakers.replace(" ", "").split(",") if ":" in pair)
    else:
        speakers = {lang: s.sarvam_speaker for lang in ("en", "hi", "hinglish")}
    return SarvamTTS(s.sarvam_api_key.get_secret_value(), s.audio_rate, model, speakers, pace=s.sarvam_pace)


def build_voice(s: Settings) -> VoiceRuntime:
    from app.voice.fallback import FallbackSTT, FallbackTTS
    from app.voice.phrases import PhraseCache
    from app.voice.providers import SileroVAD

    stt, tts = build_stt(s, s.stt_provider), build_tts(s, s.tts_provider)
    if s.speech_fallback:  # a paid provider gets the free one behind it (app/voice/fallback.py)
        if s.stt_provider != "groq" and s.groq_api_key.get_secret_value():
            stt = FallbackSTT(stt, build_stt(s, "groq"))
        if s.tts_provider != "edge":
            tts = FallbackTTS(tts, build_tts(s, "edge"))
    SileroVAD(s.audio_rate)  # load once now so the first call doesn't pay for it
    return VoiceRuntime(stt, tts, lambda: SileroVAD(s.audio_rate), PhraseCache(tts, s.data_dir / "tts-cache"))


def build_bank(s: Settings, db: Database) -> tuple[Bank | None, object | None]:
    """The bank's systems, and the embedded mock bank server if one was started. AU's real APIs: a new adapter, same port."""
    if s.bank_provider == "off":
        return None, None
    if s.bank_provider == "http":
        if s.bank_base_url:  # AU's gateway, or a separately started mock bank
            from app.adapters.http_bank import HttpBank

            return HttpBank(s.bank_base_url, s.bank_service_token.get_secret_value(),
                            s.bank_token_secret.get_secret_value().encode(), timeout_s=s.bank_timeout_s,
                            token_ttl_s=s.bank_token_ttl_s), None
        from app.mockbank.server import start_embedded

        server, bank = start_embedded(db, s)
        return bank, server
    if s.bank_provider != "mock":
        raise ValueError(f"unknown BANK_PROVIDER '{s.bank_provider}' (use http, mock or off)")
    from app.adapters.mock_corebank import MockCoreBank

    workbook = s.bank_data_file if str(s.bank_data_file) and s.bank_data_file.is_file() else None
    return MockCoreBank(db, workbook, s.demo_today, fail=s.mock_bank_fail), None


def _load_policy_pack(documents: DocumentService) -> None:
    """The synthetic service-policy document (seed/service-policies.md) joins the knowledge base once. If it was
    ever added (even if later deleted or replaced on the KB page), it is left alone."""
    path = ROOT / "seed" / "service-policies.md"
    title = "Service policies (synthetic)"
    if not path.is_file() or any(d["title"] == title for d in documents.list_documents(include_deleted=True)):
        return
    try:
        documents.upload(path.name, path.read_bytes(), title=title, category="policy")
    except Exception:  # never block startup on this
        logger.exception("policy pack not loaded")


def build(settings: Settings, *, start_worker: bool = True, overrides: dict | None = None,
          golden_dir: Path | None = None, reports_dir: Path | None = None) -> Container:
    t = time.perf_counter()
    o = overrides or {}
    db = Database(settings.database_url, settings.data_dir)

    if "embedder" in o:
        embedder, reranker = o["embedder"], o["reranker"]
    else:
        embedder, reranker = build_search(settings)
    llm = o.get("llm") or build_llm(settings)
    store = o.get("store") or build_store(settings)
    voice = o.get("voice") or (build_voice(settings) if settings.voice_enabled else None)
    bank, bank_server = (o["bank"], None) if "bank" in o else build_bank(settings, db)

    db.ensure_vector_index(embedder.name, embedder.dim)
    documents = DocumentService(db, store, ParserRouter(), embedder, settings)
    worker = IngestWorker(db, documents, settings.worker_poll_s)
    traces = TraceStore(db)
    retriever = Retriever(db, embedder, reranker, settings)
    auth = AuthService(db, bank, settings) if bank else None
    audit = AuditStore(db)
    recordings = RecordingStore(db, settings, o.get("recording_files") or build_recording_files(settings))
    service_desk = LocalServiceDesk(db, settings.demo_today)
    handoffs = HandoffService(db, settings)
    ticket_desk = service_desk
    if hasattr(bank, "http"):  # tickets go through the CRM system's API
        from app.adapters.http_bank import HttpServiceDesk

        ticket_desk = HttpServiceDesk(bank)
    journeys = Journeys(bank, auth, ticket_desk, handoffs, audit, settings) if auth else None
    conversation = Conversation(llm, retriever, traces, settings, auth, audit, journeys)
    reviews = ReviewService(db, audit, llm, settings)
    summariser = Summariser(llm, settings.understand_model, audit, handoffs)
    conversation.summariser = summariser
    evals = EvalRunner(db, conversation, traces, settings, golden_dir or ROOT / "evals" / "golden",
                       reports_dir or ROOT / "evals" / "reports")
    if settings.policy_pack and start_worker:
        _load_policy_pack(documents)
    if start_worker:
        worker.start()
    log(logger, "container ready", llm=settings.llm_provider, voice=bool(voice), device=getattr(embedder, "device", "-"),
        startup_ms=round((time.perf_counter() - t) * 1000))
    return Container(settings, db, embedder, reranker, llm, documents, worker, traces, retriever, conversation, evals, voice,
                     bank, auth, audit, recordings, service_desk, handoffs, bank_server, reviews, summariser)
