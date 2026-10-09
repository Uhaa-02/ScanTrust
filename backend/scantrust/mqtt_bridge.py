"""Bridge from the ESP32 shelf (MQTT) and the vision script into the engine.

Topics (JSON payloads):
  scantrust/shelf/<zone>/event   {"delta": 1}                       from the ESP32
  scantrust/vision/<zone>        {"sku": "SOAP", "conf": 0.91,
                                  "session_hint": "S002"}           from vision/detect.py

The bridge waits up to FUSION_WINDOW_S after a weight event for a matching
vision detection on the same zone, then sends one fused event to the engine.

Enable by setting MQTT_HOST (and optionally MQTT_PORT) before starting the API.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import paho.mqtt.client as mqtt

FUSION_WINDOW_S = 1.5


def start_bridge(engine, lock: asyncio.Lock, loop: asyncio.AbstractEventLoop):
    vision: dict[str, dict] = {}  # latest detection per zone

    async def fuse(zone: str, delta: int, ts: float):
        await asyncio.sleep(FUSION_WINDOW_S)
        v = vision.get(zone)
        if v and abs(v["ts"] - ts) > FUSION_WINDOW_S * 2:
            v = None
        async with lock:
            engine.shelf_event(zone, delta, ts,
                               vision_sku=v and v.get("sku"),
                               vision_conf=(v or {}).get("conf", 0.0),
                               session_hint=v and v.get("session_hint"))

    def on_message(_client, _userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            return
        parts = msg.topic.split("/")
        now = time.time()
        if parts[1] == "shelf" and len(parts) == 4:
            asyncio.run_coroutine_threadsafe(fuse(parts[2], int(data["delta"]), now), loop)
        elif parts[1] == "vision" and len(parts) == 3:
            vision[parts[2]] = {**data, "ts": now}

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(os.environ["MQTT_HOST"], int(os.getenv("MQTT_PORT", "1883")))
    client.subscribe([("scantrust/shelf/+/event", 0), ("scantrust/vision/+", 0)])
    client.loop_start()
    return client
