"""River embedding inspection worker — heuristic anomaly → Module 3 ingest."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

CONTROL_URL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")
ORG_SLUG = os.environ.get("ORGANIZATION_SLUG", "zeroshield")
REDIS_URL = os.environ.get("REDIS_URL", "redis://module3-redis:6379/0")
QUEUE = os.environ.get("RIVER_QUEUE", "module3:embeddings")
THRESHOLD = float(os.environ.get("ANOMALY_THRESHOLD", "0.85"))
POLL_SEC = float(os.environ.get("POLL_SECONDS", "10"))


def post_inspection(payload: dict) -> None:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{CONTROL_URL}/api/module3/ingest/embedding-inspection/",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AGENT_API_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def score_embedding(raw: dict) -> float:
    """Heuristic score: explicit score wins; else hash-derived demo score."""
    if "anomaly_score" in raw:
        return float(raw["anomaly_score"])
    text = str(raw.get("text") or raw.get("payload") or "")
    digest = hashlib.sha256(text.encode()).hexdigest()
    # Map first byte to 0..1
    return int(digest[:2], 16) / 255.0


def process_one(raw: dict) -> None:
    score = score_embedding(raw)
    collection = str(raw.get("collection") or "corp-docs")
    payload_hash = str(raw.get("payload_hash") or hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest())
    quarantined = score >= THRESHOLD or bool(raw.get("force_quarantine"))
    body = {
        "organization_slug": ORG_SLUG,
        "collection": collection,
        "status": "quarantined" if quarantined else "clean",
        "anomaly_score": score,
        "payload_hash": payload_hash,
        "quarantine_reason": raw.get("quarantine_reason")
        or ("River heuristic anomaly above threshold" if quarantined else ""),
    }
    post_inspection(body)
    print("inspected", body["status"], score, flush=True)


def main() -> None:
    if not AGENT_API_KEY:
        raise SystemExit("AGENT_API_KEY required")
    if redis is None:
        raise SystemExit("redis package required")

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    print("river worker listening", QUEUE, "threshold", THRESHOLD, flush=True)
    while True:
        item = client.blpop(QUEUE, timeout=5)
        if not item:
            time.sleep(POLL_SEC)
            continue
        _, payload = item
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError:
            raw = {"text": payload, "force_quarantine": True}
        try:
            process_one(raw)
        except Exception as exc:  # noqa: BLE001
            print("river error", exc, flush=True)
            try:
                client.rpush(QUEUE, payload)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(min(POLL_SEC, 5.0))


if __name__ == "__main__":
    main()
