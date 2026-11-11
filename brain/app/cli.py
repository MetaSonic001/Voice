"""Command line: `brain serve`, plus thin API clients for everyday tasks.

  brain serve  (or: python -m app)     start the API on http://127.0.0.1:8000
  brain upload FILE... [--category X]  upload documents and wait until they're indexed
  brain ask "question" [--session ID]  ask one question and print the answer + citations + timings
  brain chat                           interactive session with follow-ups
  brain eval [--golden v1]             run the golden set and print the summary
  brain status                         documents, jobs and recent stats
  brain say "text" [--lang hi] -o F    synthesize test audio (WAV) with the configured TTS
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


def upload(args) -> None:
    with _client() as c:
        ids = []
        for name in args.files:
            path = Path(name)
            data = {"category": args.category, "purpose": args.purpose} | ({"sheets": args.sheets} if args.sheets else {})
            r = c.post("/v1/documents", files={"file": (path.name, path.read_bytes())}, data={k: v for k, v in data.items() if v})
            r.raise_for_status()
            doc = r.json()
            ids.append(doc["id"])
            print(f"{'= duplicate' if doc['deduplicated'] else '+ uploaded '} {path.name} -> {doc['id']}")
        print("waiting for indexing...")
        while True:
            docs = {d["id"]: d for d in c.get("/v1/documents").json()}
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
        r = c.post("/v1/ask", json={"question": args.question, "session_id": args.session})
        r.raise_for_status()
        _print_reply(r.json())


def chat(args) -> None:
    with _client() as c:
        session = c.post("/v1/sessions", json={}).json()["id"]
        print(f"session {session} - type a question, empty line to quit")
        while True:
            q = input("\nyou> ").strip()
            if not q:
                break
            r = c.post("/v1/ask", json={"question": q, "session_id": session})
            r.raise_for_status()
            _print_reply(r.json())


def evaluate(args) -> None:
    with _client() as c:
        body = {"golden": args.golden} | ({"only": args.only} if args.only else {})
        run = c.post("/v1/evals/run", json=body).json()
        run_id = run["run_id"]
        print(f"eval {run_id} running...")
        while (res := c.get(f"/v1/evals/{run_id}").json())["status"] == "running":
            time.sleep(3)
        if res["status"] != "done":
            sys.exit(f"eval failed: {res.get('error')}")
        m = res["metrics"]
        print(json.dumps({"overall": m["overall"], "answered_latency_ms": m["answered_total_ms"],
                          "by_language": m["by_language"], "cost_usd": m["cost_usd"]}, indent=2))
        print(f"report: {res['report_path']}")


def status(args) -> None:
    with _client() as c:
        for d in c.get("/v1/documents").json():
            print(f"{d['latest_status']:10} v{d['current_version'] or '-'} chunks={d['chunks']:4} "
                  f"{'on ' if d['enabled'] else 'off'} {d['purpose']:9} {d['title']}")
        print(json.dumps(c.get("/v1/stats").json(), indent=2))


def _ws_url(language: str | None) -> str:
    url = BASE.replace("http", "ws", 1) + "/v1/voice/stream"
    return url + (f"?key={os.environ['API_KEY']}" if os.environ.get("API_KEY") else "")


def _show(event: dict, t0: float) -> None:
    kind = event.pop("type")
    if kind == "turn_metrics":
        event = {k: v for k, v in event.items() if v is not None}
    print(f"{time.perf_counter() - t0:7.2f}s  {kind:15} {json.dumps(event, ensure_ascii=False)}")


def say(args) -> None:
    with _client() as c:
        r = c.post("/v1/voice/tts", json={"text": args.text, "language": args.lang})
        r.raise_for_status()
        Path(args.out).write_bytes(r.content)
        print(f"{args.out}: first audio {r.headers['x-first-audio-ms']} ms, total {r.headers['x-total-ms']} ms")


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
    from app.db import Database
    from app.services import maintenance

    settings = get_settings()
    db = Database(settings.database_url, settings.data_dir)
    try:
        print("This will delete:")
        for name, count in maintenance.preview(db).items():
            print(f"  {name:20} {count}")
        print("The knowledge base and eval history are kept. This cannot be undone.")
        if not args.yes and input("Type DELETE to continue: ").strip() != "DELETE":
            sys.exit("cancelled")
        print(json.dumps(maintenance.clean_slate(db, settings.data_dir, confirm=True)))
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="brain", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=serve)
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
    s = sub.add_parser("eval")
    s.add_argument("--golden", default="v1")
    s.add_argument("--only", nargs="*")
    s.set_defaults(fn=evaluate)
    sub.add_parser("status").set_defaults(fn=status)
    s = sub.add_parser("say")
    s.add_argument("text")
    s.add_argument("--lang", default="en", choices=["en", "hi", "hinglish"])
    s.add_argument("-o", "--out", default="say.wav")
    s.set_defaults(fn=say)
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
    args = p.parse_args()
    if args.cmd is None:  # `python -m app` with no command = start the API
        args = p.parse_args(["serve"])
    args.fn(args)


if __name__ == "__main__":
    main()
