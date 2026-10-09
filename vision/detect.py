"""Overhead camera → product-in-hand detections over MQTT.

For each frame, a YOLO model fine-tuned on the shelf's products finds items;
any item whose box overlaps a shelf zone's region is published as the vision
signal for that zone. The backend bridge fuses it with the next weight event.

    pip install ultralytics opencv-python paho-mqtt
    python detect.py --model runs/detect/train/weights/best.pt --source 0 --mqtt localhost
    python detect.py --model yolov8n.pt --source 0 --show      # try the pipeline first

Topic: scantrust/vision/<zone>  payload {"sku": "SOAP", "conf": 0.91, "session_hint": null}

Training your own model: photograph each product 200–300 times (angles, lighting,
in a hand), label with Roboflow or Label Studio, export in YOLO format with class
names equal to the SKUs (CHIPS, SOAP, SHAMPOO, DRYFRUIT), then:
    yolo detect train data=data.yaml model=yolov8n.pt epochs=60 imgsz=640

session_hint stays null here. Adding a person tracker (e.g. ByteTrack via
model.track) and mapping track IDs to app sessions is the next step.
"""

import argparse
import json
import time

import cv2
import paho.mqtt.client as mqtt
from ultralytics import YOLO

# Zone regions in the camera frame as (x1, y1, x2, y2) fractions of width/height.
# Adjust once after mounting the camera: run with --show and read the overlay.
ZONES = {
    "A3-Z1": (0.00, 0.55, 0.25, 1.00),
    "A3-Z2": (0.25, 0.55, 0.50, 1.00),
    "A3-Z3": (0.50, 0.55, 0.75, 1.00),
    "A3-Z4": (0.75, 0.55, 1.00, 1.00),
}
MIN_CONF = 0.5
PUBLISH_EVERY_S = 0.3


def overlap(box, region):
    x1, y1, x2, y2 = box
    rx1, ry1, rx2, ry2 = region
    ix = max(0, min(x2, rx2) - max(x1, rx1))
    iy = max(0, min(y2, ry2) - max(y1, ry1))
    area = max(1e-6, (x2 - x1) * (y2 - y1))
    return ix * iy / area


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="yolov8n.pt")
    ap.add_argument("--source", default="0", help="camera index, video file or RTSP/IP-camera URL")
    ap.add_argument("--mqtt", default=None, help="MQTT host; omit to only print")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    model = YOLO(a.model)
    client = None
    if a.mqtt:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(a.mqtt, 1883)
        client.loop_start()

    cap = cv2.VideoCapture(int(a.source) if a.source.isdigit() else a.source)
    last = {}
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        res = model(frame, verbose=False)[0]
        for b in res.boxes:
            conf = float(b.conf)
            if conf < MIN_CONF:
                continue
            sku = res.names[int(b.cls)].upper()
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            box = (x1 / w, y1 / h, x2 / w, y2 / h)
            for zone, region in ZONES.items():
                if overlap(box, region) < 0.3 or time.time() - last.get(zone, 0) < PUBLISH_EVERY_S:
                    continue
                last[zone] = time.time()
                msg = {"sku": sku, "conf": round(conf, 3), "session_hint": None}
                if client:
                    client.publish(f"scantrust/vision/{zone}", json.dumps(msg))
                print(zone, msg)
        if a.show:
            out = res.plot()
            for zone, (rx1, ry1, rx2, ry2) in ZONES.items():
                cv2.rectangle(out, (int(rx1 * w), int(ry1 * h)), (int(rx2 * w), int(ry2 * h)), (255, 160, 40), 2)
                cv2.putText(out, zone, (int(rx1 * w) + 6, int(ry1 * h) + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 160, 40), 2)
            cv2.imshow("ScanTrust vision", out)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
