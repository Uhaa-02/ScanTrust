"""FastAPI server: phone app, shelf events, staff dashboard.

Run:  uvicorn scantrust.app:app --reload   (from the backend/ folder)
Open: http://localhost:8000
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .catalog import PRODUCTS, ZONES
from .engine import Reconciler

GRACE_S = float(os.getenv("SCANTRUST_GRACE_S", "10"))
WEB_DIR = Path(__file__).resolve().parents[2] / "web"

engine = Reconciler(grace_s=GRACE_S)
lock = asyncio.Lock()


async def ticker():
    while True:
        async with lock:
            engine.tick(time.time())
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(ticker())
    bridge = None
    if os.getenv("MQTT_HOST"):
        from .mqtt_bridge import start_bridge
        bridge = start_bridge(engine, lock, asyncio.get_running_loop())
    yield
    task.cancel()
    if bridge:
        bridge.loop_stop()


app = FastAPI(title="ScanTrust", lifespan=lifespan)


class NewSession(BaseModel):
    name: str


class AisleIn(BaseModel):
    aisle: str | None


class ScanIn(BaseModel):
    sku: str
    qty: int = 1


class ShelfIn(BaseModel):
    zone: str
    delta: int
    vision_sku: str | None = None
    vision_conf: float = 0.0
    session_hint: str | None = None


class ResolveIn(BaseModel):
    outcome: str  # "ok" | "charged"


def _guard(fn):
    try:
        return fn()
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/api/catalog")
def catalog():
    return {
        "products": [p.__dict__ for p in PRODUCTS.values()],
        "zones": [z.__dict__ for z in ZONES.values()],
        "grace_s": GRACE_S,
    }


@app.get("/api/state")
async def state():
    async with lock:
        return engine.snapshot()


@app.post("/api/sessions")
async def start(body: NewSession):
    async with lock:
        return engine.start_session(body.name, time.time()).to_dict()


@app.post("/api/sessions/{sid}/aisle")
async def aisle(sid: str, body: AisleIn):
    async with lock:
        _guard(lambda: engine.enter_aisle(sid, body.aisle, time.time()))
        return engine.sessions[sid].to_dict()


@app.post("/api/sessions/{sid}/scan")
async def scan(sid: str, body: ScanIn):
    async with lock:
        _guard(lambda: engine.scan(sid, body.sku, time.time(), body.qty))
        return engine.sessions[sid].to_dict()


@app.post("/api/sessions/{sid}/exit")
async def leave(sid: str):
    async with lock:
        return _guard(lambda: engine.exit(sid, time.time()))


@app.post("/api/shelf")
async def shelf(body: ShelfIn):
    async with lock:
        return _guard(lambda: engine.shelf_event(
            body.zone, body.delta, time.time(), body.vision_sku, body.vision_conf, body.session_hint))


@app.post("/api/alerts/{aid}/resolve")
async def resolve(aid: int, body: ResolveIn):
    async with lock:
        try:
            return engine.resolve_alert(aid, body.outcome, time.time()).to_dict()
        except StopIteration:
            raise HTTPException(404, "unknown alert")


@app.post("/api/reset")
async def reset():
    global engine
    async with lock:
        engine.__init__(grace_s=GRACE_S)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
