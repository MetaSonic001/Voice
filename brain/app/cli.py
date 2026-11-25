"""Command line: `brain serve`, plus thin API clients for everyday tasks.

  brain serve  (or: python -m app)     start the API on http://127.0.0.1:8000
  brain search [--host H --port 8200]  GPU search server (embed + rerank) for SEARCH_PROVIDER=http
  brain search --sagemaker             the same, as a SageMaker endpoint (port 8080, /ping, /invocations)
  brain upload FILE... [--category X]  upload documents and wait until they're indexed
  brain ask "question" [--session ID]  ask one question and print the answer + citations + timings
  brain chat                           interactive session with follow-ups
  brain status                         documents, jobs and recent stats
  brain say "text" [--lang hi] -o F    synthesize test audio (WAV) with the configured TTS
  brain sarvam-check [--out-dir D]     Sarvam key check: Bulbul v4-flash vs v3, pronunciation + speed (~Rs 2)
  brain voice FILE [--out reply.wav]   stream an audio file through the live call (WebSocket) in real time
  brain clean-slate [--yes]            delete every recorded call, ticket and handoff; re-seed the mock bank (KB kept)
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("BRAIN_URL", "http://127.0.0.1:8000")


def _client() -> httpx.Client:
    headers = {"X-API-Key": os.environ["API_KEY"]} if os.environ.get("API_KEY") else {}
    return httpx.Client(base_url=BASE, timeout=120, headers=headers)


def serve(args) -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, log_config=None)


def search(args) -> None:
    import uvicorn

    from app.config import get_settings
    from app.search_server import check_bind, from_settings

    settings = get_settings()
    if args.sagemaker:  # SageMaker's contract: port 8080 on every interface; SageMaker checks the caller (IAM)
        uvicorn.run(from_settings(settings, sagemaker=True), host="0.0.0.0", port=8080, log_config=None)
        return
    check_bind(args.host, settings.search_token.get_secret_value())
    uvicorn.run(from_settings(settings), host=args.host, port=args.port, log_config=None)


def upload(args) -> None:
    with _client() as c:
        ids = []
        for name in args.files:
            path = Path(name)
            data = {"category": args.category, "purpose": args.purpose} | ({"sheets": args.sheets} if args.sheets else {})
            r = c.post("/api/v1/documents", files={"file": (path.name, path.read_bytes())}, data={k: v for k, v in data.items() if v})
            r.raise_for_status()
            doc = r.json()
            ids.append(doc["id"])
            print(f"{'= duplicate' if doc['deduplicated'] else '+ uploaded '} {path.name} -> {doc['id']}")
        print("waiting for indexing...")
        while True:
            docs = {d["id"]: d for d in c.get("/api/v1/documents").json()}
            states = [docs[i]["latest_status"] for i in ids if i in docs]
            if all(s in ("indexed", "failed") for s in states):
                break
            time.sleep(1)
        for i in ids:
            d = docs[i]
            print(f"  {d['latest_status']:8} {d['title'][:50]:50} chunks={d['chunks']}")


def _print_reply(reply: dict) -> None:
    print(f"\n[{reply['outcome']}] ({reply['language']}, intent={reply['intent']}) {reply['answer']}")
    for cit in reply["citations"]:
        print(f"   [{cit['marker']}] {cit['section']} (v{cit['version']})")
    t = reply["timings"]
    print("   timings: " + ", ".join(f"{k.removesuffix('_ms')}={v}" for k, v in t.items() if v is not None) + " ms")
    print(f"   trace: GET /v1/traces/{reply['trace_id']}")


def ask(args) -> None:
    with _client() as c:
        r = c.post("/api/v1/ask", json={"question": args.question, "session_id": args.session})
        r.raise_for_status()
        _print_reply(r.json())


def chat(args) -> None:
    with _client() as c:
        session = c.post("/api/v1/sessions", json={}).json()["id"]
        print(f"session {session} - type a question, empty line to quit")
        while True:
            q = input("\nyou> ").strip()
            if not q:
                break
            r = c.post("/api/v1/ask", json={"question": q, "session_id": session})
            r.raise_for_status()
            _print_reply(r.json())


def status(args) -> None:
    with _client() as c:
        for d in c.get("/api/v1/documents").json():
            print(f"{d['latest_status']:10} v{d['current_version'] or '-'} chunks={d['chunks']:4} "
                  f"{'on ' if d['enabled'] else 'off'} {d['purpose']:9} {d['title']}")
        print(json.dumps(c.get("/api/v1/stats").json(), indent=2))


def _ws_url(language: str | None) -> str:
    url = BASE.replace("http", "ws", 1) + "/api/v1/voice/stream"
    return url + (f"?key={os.environ['API_KEY']}" if os.environ.get("API_KEY") else "")


def _show(event: dict, t0: float) -> None:
    kind = event.pop("type")
    if kind == "turn_metrics":
        event = {k: v for k, v in event.items() if v is not None}
    print(f"{time.perf_counter() - t0:7.2f}s  {kind:15} {json.dumps(event, ensure_ascii=False)}")


def say(args) -> None:
    with _client() as c:
        r = c.post("/api/v1/voice/tts", json={"text": args.text, "language": args.lang})
        r.raise_for_status()
        Path(args.out).write_bytes(r.content)
        print(f"{args.out}: first audio {r.headers['x-first-audio-ms']} ms, total {r.headers['x-total-ms']} ms")


# One short reply per language, packed with the words most likely to be said wrong: the bank's name, product names,
# acronyms, an amount, a rate and the app name. Written as the answer model writes them; the check sends them through
# the same rewrite a live call uses (AU -> A.U. / एयू, Zenith+ -> Zenith Plus, amounts in words), so the audio is
# exactly what a caller hears. Kept short on purpose: about 350 characters per model (about Rs 2 for both models).
CHECK_TEXTS = {
    "en": "Welcome to AU Small Finance Bank. Your Zenith+ debit card is blocked, and your FD of Rs 2,50,000 earns 7.25% p.a.",
    "hi": "AU बैंक में स्वागत है। आपके Laksya और BSBDA खाते का KYC पूरा है, OTP आपके मोबाइल पर भेजा है।",
    "hinglish": "AU 0101 app mein UPI aur NEFT dono chalte hain. Aapki EMI Vikaas account se kategi.",
}
TTS_RS_PER_1000 = {"bulbul:v4-flash": 2.0, "bulbul:v3": 3.0, "bulbul:v2": 3.0}  # Nov 2026, pay as you go, before GST


def sarvam_check(args) -> None:
    """Paid, straight to Sarvam (the brain need not run). For each Bulbul model: warm the connection, speak one typical
    reply per language `--rounds` times (first-audio and total time, speaking speed), then transcribe the first take
    with Saaras and print it next to the input (a round-trip check, not a listening test). Prints no key."""
    from statistics import median

    from app.config import get_settings
    from app.container import build_sarvam_tts
    from app.services.numbers import speech_text
    from app.voice.audio import duration_ms, to_wav
    from app.voice.providers import SarvamSTT, SpeechError
    from app.voice.vocab import terms_for

    s = get_settings()
    if not s.sarvam_api_key.get_secret_value():
        sys.exit("SARVAM_API_KEY is empty. Paste your key into brain/.env (SARVAM_API_KEY=...) and run this again.")
    sys.stdout.reconfigure(errors="replace")  # Hindi text must not crash a terminal on an old code page
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    languages = [args.lang] if args.lang else list(CHECK_TEXTS)
    spoken = {lang: speech_text(CHECK_TEXTS[lang], lang) for lang in languages}  # as a live call would send it
    chars = sum(len(spoken[lang]) for lang in languages) * args.rounds
    cost = sum(chars * TTS_RS_PER_1000.get(m, 3.0) / 1000 for m in models) + 0.49 * len(models) * len(languages) * 6 / 60
    print(f"About Rs {cost:.2f} of credit ({chars} characters per model, {len(models) * len(languages)} short transcriptions)")
    print("\nSent to the voice (after the live call's pronunciation rewrite):")
    for lang in languages:
        print(f"  {lang:<8} {spoken[lang]}")
    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    stt = SarvamSTT(s.sarvam_api_key.get_secret_value(), s.sarvam_stt_model, s.sarvam_stt_mode,
                    keyterms=terms_for(s.stt_vocabulary) if s.stt_vocabulary != "none" else [])

    async def speak(tts, text: str, language: str) -> tuple[bytes, float, float]:
        t0, first, pcm = time.perf_counter(), None, bytearray()
        async for chunk in tts.stream(text, language):
            first = first or time.perf_counter()
            pcm += chunk
        return bytes(pcm), ((first or t0) - t0) * 1000, (time.perf_counter() - t0) * 1000

    async def check_one(tts, model: str, lang: str) -> None:
        text, firsts, totals, take = spoken[lang], [], [], b""
        for _ in range(args.rounds):
            pcm, first, total = await speak(tts, text, lang)
            firsts.append(first)
            totals.append(total)
            take = take or pcm
        seconds = duration_ms(take, s.audio_rate) / 1000
        print(f"  {lang:<8} {tts.speaker(lang):<22} first audio {', '.join(f'{f:.0f}' for f in firsts)} ms"
              f" (median {median(firsts):.0f}), total median {median(totals):.0f} ms,"
              f" {seconds:.2f} s of audio, {len(text) / max(seconds, 0.01):.1f} chars/s")
        if out_dir:
            (out_dir / f"{model.replace(':', '_')}_{lang}.wav").write_bytes(to_wav(take, s.audio_rate))
        heard = await stt.transcribe(take, s.audio_rate)
        print(f"           heard back ({s.sarvam_stt_model}): {heard.text}")

    async def run():
        try:
            await stt.warm()
            for model in models:
                try:
                    tts = build_sarvam_tts(s, model)
                except SpeechError as e:
                    print(f"\n{model}: skipped - {e}")
                    continue
                print(f"\n{model}  pace {tts.pace}  (first audio per round, warm connection)")
                await tts.warm()
                try:
                    for lang in languages:
                        try:  # one failing language or voice (say a wrong speaker ID) doesn't stop the rest
                            await check_one(tts, model, lang)
                        except SpeechError as e:
                            if e.out_of_credit:
                                raise
                            hint = (" - check the voice ID against docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/voices"
                                    if e.status in (400, 422) else "")
                            print(f"  {lang:<8} {tts.speaker(lang):<22} FAILED: {e}{hint}")
                finally:
                    await tts.close()
            if out_dir:
                print(f"\nWAV files to listen to: {out_dir.resolve()}")
        except SpeechError as e:
            hint = " (out of credits or wrong key)" if e.out_of_credit else ""
            sys.exit(f"Sarvam failed{hint}: {e}")
        finally:
            await stt.close()

    asyncio.run(run())


def voice(args) -> None:
    """Plays a file into the live call at real-time speed (20 ms frames) and prints every event with a timestamp."""
    import websockets

    from app.voice.audio import decode, to_wav

    async def run():
        async with websockets.connect(_ws_url(args.lang), max_size=None) as ws:
            await ws.send(json.dumps({"type": "start", "language": args.lang, "greet": args.greet}))
            ready = json.loads(await ws.recv())
            rate = ready["sample_rate"]
            pcm = decode(Path(args.file).read_bytes(), rate) + bytes(int(rate * 2 * args.tail))  # trailing silence
            frame = rate * 2 // 50
            reply = bytearray()
            t0 = time.perf_counter()
            print(f"session {ready['session_id']}  ({len(pcm) / rate / 2:.1f}s of audio incl. {args.tail}s silence)")

            async def send():
                if args.greet:
                    await asyncio.sleep(args.wait_greeting)
                start = time.perf_counter()
                for i in range(0, len(pcm), frame):
                    await ws.send(pcm[i:i + frame])
                    await asyncio.sleep(max(0.0, start + (i + frame) / rate / 2 - time.perf_counter()))

            sender = asyncio.create_task(send())
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=args.timeout)
                    if isinstance(msg, bytes):
                        reply += msg
                        continue
                    event = json.loads(msg)
                    done = event["type"] in ("turn_metrics", "end")
                    _show(event, t0)
                    if done and sender.done():
                        break
            except TimeoutError:
                print("(no more events)")
            sender.cancel()
            await ws.send(json.dumps({"type": "stop"}))
            if args.out and reply:
                Path(args.out).write_bytes(to_wav(bytes(reply), rate))
                print(f"agent audio saved to {args.out}")

    asyncio.run(run())


def clean_slate(args) -> None:
    """Works on the database directly (a running brain keeps working: it re-seeds the mock bank on its next start)."""
    from app.config import get_settings
    from app.container import build_call_record_files, build_recording_files
    from app.db import Database
    from app.services import maintenance

    settings = get_settings()
    recording_files = build_recording_files(settings)
    db = Database(settings.database_url, settings.data_dir, settings.vector_store)
    try:
        print("This will delete:")
        for name, count in maintenance.preview(db).items():
            print(f"  {name:20} {count}")
        print("The knowledge base and eval history are kept. This cannot be undone.")
        if not args.yes and input("Type DELETE to continue: ").strip() != "DELETE":
            sys.exit("cancelled")
        print(json.dumps(maintenance.clean_slate(db, recording_files, confirm=True,
                                                    record_files=build_call_record_files(settings))))
    finally:
        db.close()


def content_export(args) -> None:
    """Write the live release of the journey catalogue (or --release N) into brain/content/, to commit it to git."""
    from app.config import get_settings
    from app.db import Database
    from app.services.content import write_seed
    from app.services.releases import Releases

    settings = get_settings()
    db = Database(settings.database_url, settings.data_dir, settings.vector_store)
    try:
        releases = Releases(db)
        release = args.release if args.release is not None else releases.rollout()["stable_id"]
        changed = write_seed(releases.catalog(release))
        print(f"release {release or 0} written to brain/content/: {changed} file(s) changed. Review with git diff, then commit.")
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="brain", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=serve)
    s = sub.add_parser("search", help="GPU search server (embed + rerank) for SEARCH_PROVIDER=http or sagemaker")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8200)
    s.add_argument("--sagemaker", action="store_true", help="SageMaker endpoint mode: /ping and /invocations on port 8080")
    s.set_defaults(fn=search)
    s = sub.add_parser("upload")
    s.add_argument("files", nargs="+")
    s.add_argument("--category")
    s.add_argument("--purpose", default="knowledge")
    s.add_argument("--sheets", help="XLSX: comma-separated sheet names")
    s.set_defaults(fn=upload)
    s = sub.add_parser("ask")
    s.add_argument("question")
    s.add_argument("--session")
    s.set_defaults(fn=ask)
    sub.add_parser("chat").set_defaults(fn=chat)
    sub.add_parser("status").set_defaults(fn=status)
    s = sub.add_parser("say")
    s.add_argument("text")
    s.add_argument("--lang", default="en", choices=["en", "hi", "hinglish"])
    s.add_argument("-o", "--out", default="say.wav")
    s.set_defaults(fn=say)
    s = sub.add_parser("sarvam-check", help="Sarvam key check + Bulbul model comparison with pronunciation sentences (paid, about Rs 2)")
    s.add_argument("--models", default="bulbul:v4-flash,bulbul:v3", help="comma-separated Bulbul models to compare")
    s.add_argument("--lang", choices=["en", "hi", "hinglish"], help="only this reply language (default: all three)")
    s.add_argument("--rounds", type=int, default=1, help="takes per sentence; 3 gives steadier timings (costs 3x)")
    s.add_argument("--out-dir", help="save one WAV per model and language here, to listen to")
    s.set_defaults(fn=sarvam_check)
    s = sub.add_parser("voice")
    s.add_argument("file", help="any audio file (wav/mp3/m4a/...)")
    s.add_argument("--lang", default="en", choices=["en", "hi"], help="greeting language")
    s.add_argument("--greet", action="store_true", help="play the greeting first (audio starts after --wait-greeting)")
    s.add_argument("--wait-greeting", type=float, default=6.0)
    s.add_argument("--tail", type=float, default=1.5, help="seconds of silence appended after the audio")
    s.add_argument("--timeout", type=float, default=20.0)
    s.add_argument("--out", help="save the agent's audio as WAV")
    s.set_defaults(fn=voice)
    s = sub.add_parser("clean-slate")
    s.add_argument("--yes", action="store_true", help="do not ask for the DELETE confirmation")
    s.set_defaults(fn=clean_slate)
    s = sub.add_parser("content-export", help="write the live journey-catalogue release into brain/content/ (for git)")
    s.add_argument("--release", type=int, help="a published release id (default: the one live for most calls)")
    s.set_defaults(fn=content_export)
    args = p.parse_args()
    if args.cmd is None:  # `python -m app` with no command = start the API
        args = p.parse_args(["serve"])
    args.fn(args)


if __name__ == "__main__":
    main()
