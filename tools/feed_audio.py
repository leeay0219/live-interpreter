"""Stream a WAV/PCM file to the caption server as if it were the mic (rehearsal without a microphone).

  say -o /tmp/t.aiff "Hello"  &&  afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/t.aiff /tmp/t.wav
  .venv/bin/python tools/feed_audio.py /tmp/t.wav
"""
import asyncio, json, sys, time, wave
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import aiohttp

async def main(path, url="ws://localhost:8080/ws"):
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "need 16kHz mono s16le"
        pcm = w.readframes(w.getnframes())
    pcm += b"\0" * 16000 * 2 * 3  # trailing silence so the last segment finalizes
    parts = urlsplit(url)
    url = urlunsplit(parts._replace(query=urlencode([*parse_qsl(parts.query), ("client", uuid.uuid4().hex)])))
    async with aiohttp.ClientSession() as s, s.ws_connect(url) as ws:
        while True:
            initial = await ws.receive_json()
            if initial["type"] == "lifecycle":
                break
        if initial["phase"] != "prepared":
            raise RuntimeError("Use a fresh isolated test server; this server already has a session.")
        await ws.send_json({"type": "start", "intent": "new", "session_id": initial["session_id"]})
        async def recv():
            async for m in ws:
                d = json.loads(m.data)
                if d["type"] in ("final", "caption", "status", "control_error", "lifecycle"):
                    print(f"{time.monotonic()-t0:6.2f}s", d)
        t0 = time.monotonic()
        r = asyncio.create_task(recv())
        step = 3200  # 100 ms
        for i in range(0, len(pcm), step):
            await ws.send_bytes(pcm[i:i+step]); await asyncio.sleep(0.1)
        await asyncio.sleep(4)
        await ws.send_str(json.dumps({"type": "stop"}))
        await asyncio.sleep(1); r.cancel()

if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:3]))
