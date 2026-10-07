"""WebSocket endpoints: the operator's audio stream and caption broadcast, and private rehearsal."""
import sys
import asyncio
import json
import logging
import uuid
from pathlib import Path

from aiohttp import WSMsgType, web

import materials


sys.path.insert(0, str(Path(__file__).parent / "postprocess"))


from live import Hub, Session
from access import operator
log = logging.getLogger("captions")


async def ws_handler(request):
    app, state = request.app, request.app["state"]
    hub, auth = app["hub"], app["auth"]
    role = auth.role(request)
    if role is None:
        raise web.HTTPForbidden()
    client_id = request.query.get("client", "")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
    await ws.prepare(request)
    hub.viewers.add(ws)
    replay = request.query.get("replay", "3")
    replay = min(60, int(replay)) if replay.isdigit() else 3
    for message in hub.history[max(0, len(hub.history) - replay):]:
        await ws.send_json(message)
    await ws.send_json({"type": "lifecycle", "phase": state.get("phase", "prepared"), "session_id": state["session_id"]})
    session = None
    try:
        async for message in ws:
            if role != "operator":
                continue
            if message.type == WSMsgType.BINARY:
                if session and state.get("source") is session:
                    try:
                        session.feed_audio(message.data)
                    except (asyncio.QueueFull, ValueError):
                        session.accepting = False
                        await ws.send_json({"type": "status", "state": "overloaded", "sid": session.sid,
                                            "detail": "음성 전송이 밀렸습니다. 잠시 후 이어서 하세요."})
                continue
            if message.type != WSMsgType.TEXT:
                continue
            try:
                body = json.loads(message.data)
                if not isinstance(body, dict):
                    raise ValueError()
                async with app["session_lock"]:
                    action = body.get("type")
                    if action == "start":
                        if not client_id or body.get("session_id") != state["session_id"]:
                            raise web.HTTPConflict(text="세션이 바뀌었습니다. 새로 고쳐 주세요.")
                        current = state.get("source")
                        controller = state.get("controller")
                        if controller and controller != client_id and state.get("phase") in ("live", "paused"):
                            raise web.HTTPConflict(text="다른 창에서 통역을 운영하고 있습니다.")
                        if current and current.owner is ws and current.task and not current.task.done():
                            continue
                        intent = body.get("intent", "resume")
                        if intent == "new" and state.get("phase") != "prepared":
                            raise web.HTTPConflict(text="진행 중인 세션은 이어서 하기를 사용하세요.")
                        if intent != "new" and state.get("phase") not in ("live", "paused"):
                            raise web.HTTPConflict(text="새 세션을 준비한 뒤 시작하세요.")
                        if current:
                            await current.stop()
                        if intent == "new":
                            app["record"].clear()
                            hub.history.clear()
                            app["translator"].reset_conversation()
                            await hub.send({"type": "clear"})
                        tr = app["translator"]
                        tr.logical_id = state["session_id"]
                        session = Session(app["cfg"], hub, tr, owner=ws, record=app["record"])
                        session.client_id = client_id
                        state.update(source=session, phase="live", controller=client_id)
                        await ws.send_json({"type": "session", "sid": session.sid, "session_id": state["session_id"]})
                        session.start()
                        await hub.send({"type": "lifecycle", "phase": "live", "session_id": state["session_id"]})
                    elif action in ("pause", "stop"):
                        if state.get("controller") != client_id:
                            raise web.HTTPConflict(text="현재 운영 창에서 멈춰 주세요.")
                        current = state.get("source")
                        if current:
                            await current.stop()
                        state.update(source=None, phase="paused" if action == "pause" else "ended")
                        session = None
                        await hub.send({"type": "lifecycle", "phase": state["phase"], "session_id": state["session_id"]})
                    elif action == "context":
                        if body.get("session_id") != state["session_id"]:
                            raise web.HTTPConflict(text="세션이 바뀌었습니다. 새로 고쳐 주세요.")
                        if state.get("controller") and state.get("controller") != client_id:
                            raise web.HTTPConflict(text="현재 운영 창에서 슬라이드를 변경하세요.")
                        result = materials.select_page(app, body)
                        await hub.send(result)
            except (ValueError, TypeError):
                await ws.send_json({"type": "control_error", "detail": "요청 형식이 올바르지 않습니다."})
            except web.HTTPException as exc:
                await ws.send_json({"type": "control_error", "detail": exc.text})
    finally:
        hub.viewers.discard(ws)
        async with app["session_lock"]:
            if session and state.get("source") is session:
                await session.stop()
                state["source"] = None
                # Keep logical session, record and context. Reconnect resumes without clearing captions.
                if state.get("phase") == "live":
                    state["phase"] = "paused"
    return ws


async def rehearsal_ws(request):
    operator(request)
    app = request.app
    if app["state"].get("rehearsal") or app["state"].get("phase") == "live":
        raise web.HTTPConflict(text="진행 중인 통역이나 리허설을 먼저 멈춰 주세요.")
    app["state"]["rehearsal"] = True
    ws = web.WebSocketResponse(heartbeat=10, max_msg_size=6400)
    session = timer = None
    try:
        await ws.prepare(request)
        private_hub = Hub()
        private_hub.viewers.add(ws)
        tr = app["translator"].snapshot()
        tr.reset_conversation()
        tr.logical_id = "rehearsal-" + uuid.uuid4().hex[:8]
        session = Session(app["cfg"], private_hub, tr, owner=ws, record=[])
        await ws.send_json({"type": "session", "sid": session.sid})
        session.start()
        async def finish():
            await asyncio.sleep(12)
            await session.stop()
            await ws.send_json({"type": "rehearsal_done"})
            await ws.close()
        timer = asyncio.create_task(finish())
        async for message in ws:
            if message.type == WSMsgType.BINARY:
                try:
                    session.feed_audio(message.data)
                except (asyncio.QueueFull, ValueError):
                    await ws.close()
            elif message.type == WSMsgType.TEXT:
                body = json.loads(message.data)
                if body.get("type") == "stop":
                    break
    finally:
        app["state"]["rehearsal"] = False
        if timer:
            timer.cancel()
        if session:
            await asyncio.shield(session.stop())
    return ws
