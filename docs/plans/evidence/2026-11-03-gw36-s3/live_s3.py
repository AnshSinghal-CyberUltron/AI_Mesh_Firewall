"""GW36 S3 live check: a production-profile gateway reading the stack's real signed state.

Runs on the dev VM with the gateway_v2 venv (it has the official `openai` SDK and `httpx`):

    .venv/bin/python live_s3.py

Environment: CONTROL_URL (console API), GATEWAY_URL (the AMF_PROFILE=production gateway),
ADMIN_EMAIL, ADMIN_PASSWORD_FILE. The plaintext keys exist only in memory; nothing here prints
one. Outage drills stop and start this stack's OWN containers (`docker` = the rootless daemon).
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import httpx
import openai

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:28100")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:28302")
MODEL = "m"
MSG = [{"role": "user", "content": "hello"}]
IDENTITY = {"invalid_api_key", "shared_state_unavailable"}
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def console_token() -> str:
    password = open(os.environ["ADMIN_PASSWORD_FILE"]).read().strip()
    response = httpx.post(
        f"{CONTROL}/api/auth/token/",
        json={"email": os.environ.get("ADMIN_EMAIL", "admin@zeroshield.io"), "password": password},
    )
    response.raise_for_status()
    return str(response.json()["access"])


def gateway(method: str, path: str, key: str | None, body: object = None) -> httpx.Response:
    headers = {} if key is None else {"authorization": f"Bearer {key}"}
    return httpx.request(method, f"{GATEWAY}{path}", headers=headers, json=body, timeout=10)


def code_of(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return f"<{response.status_code} {response.text[:80]!r}>"


def wait_for(predicate, timeout_s: float, step_s: float = 0.05) -> float | None:  # type: ignore[no-untyped-def]
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        if predicate():
            return time.monotonic() - started
        time.sleep(step_s)
    return None


def steadily(predicate, times: int = 6):  # type: ignore[no-untyped-def]
    """True only when `times` consecutive answers agree: the gateway runs several workers, and a
    single answer says nothing about the others (each has its own cache and gates)."""
    return lambda: all(predicate() for _ in range(times))


def docker(*args: str) -> None:
    subprocess.run(["docker", *args], check=True, capture_output=True)


def main() -> int:
    token = console_token()
    api = httpx.Client(base_url=CONTROL, headers={"authorization": f"Bearer {token}"}, timeout=10)

    # --- E1: a console key resolves --------------------------------------------------------
    t0 = time.monotonic()
    created = api.post("/api/gateways/keys/", json={"name": "gw36-s3-live", "project_id": "s3"})
    created.raise_for_status()
    key, key_id = created.json()["key"], created.json()["id"]
    lag = wait_for(lambda: gateway("GET", "/v1/models", key).status_code == 200, 15)
    check("E1 new console key resolves", lag is not None,
          f"GET /v1/models 200 after {None if lag is None else round(lag * 1000)} ms "
          f"(console 201 at +{round((time.monotonic() - t0 - (lag or 0)) * 1000)} ms)")
    chat = gateway("POST", "/v1/chat/completions", key, {"model": MODEL, "messages": MSG})
    check("E1 chat passes identity and plan", code_of(chat) not in IDENTITY | {"plan_unavailable"},
          f"HTTP {chat.status_code} {code_of(chat)} x-request-id={chat.headers.get('x-request-id')}"
          " (the next stage, admission, is the fail-closed default until S4)")

    # --- E2: unknown / missing key -> 401, the SDK's own AuthenticationError -----------------
    for label, bad in (("unknown key", "A" * 48), ("junk token", "sk-not-a-key"), ("no key", "")):
        for stream in (False, True):
            client = openai.OpenAI(base_url=f"{GATEWAY}/v1", api_key=bad or "x", max_retries=0)
            if not bad:
                client = openai.OpenAI(
                    base_url=f"{GATEWAY}/v1", api_key="x", max_retries=0,
                    default_headers={"Authorization": ""},
                )
            try:
                client.chat.completions.create(model=MODEL, messages=MSG, stream=stream)
                check(f"E2 {label} stream={stream}", False, "no error raised")
            except openai.AuthenticationError as exc:
                body = exc.body if isinstance(exc.body, dict) else {}
                check(f"E2 {label} stream={stream}", exc.status_code == 401
                      and body.get("code") == "invalid_api_key",
                      f"AuthenticationError 401 code={body.get('code')} "
                      f"request_id={exc.request_id}")
            except openai.APIError as exc:
                check(f"E2 {label} stream={stream}", False, f"{type(exc).__name__}: {exc}")
    models = gateway("GET", "/v1/models", "A" * 48)
    check("E2 /v1/models unknown key", models.status_code == 401,
          f"HTTP {models.status_code} {code_of(models)} www-authenticate="
          f"{models.headers.get('www-authenticate')}")

    # --- E3: a key deactivated in the console -> 401 ------------------------------------------
    t0 = time.monotonic()
    api.patch(f"/api/gateways/keys/{key_id}/", json={"is_active": False}).raise_for_status()
    lag = wait_for(steadily(lambda: gateway("GET", "/v1/models", key).status_code == 401), 20)
    check("E3 deactivated key is 401 on every worker", lag is not None,
          f"401 after {None if lag is None else round(lag * 1000)} ms from the console PATCH")

    # A second live key for the outage drills.
    second = api.post("/api/gateways/keys/", json={"name": "gw36-s3-live-2", "project_id": "s3"})
    key2 = second.json()["key"]
    wait_for(lambda: gateway("GET", "/v1/models", key2).status_code == 200, 15)

    # --- E4 / E5: Valkey down -> 503 (never 401); back -> recovers ------------------------------
    docker("stop", "ai_mesh_firewall-redis-1")
    try:
        unseen = "B" * 48  # never cached: must go to the store
        lag = wait_for(lambda: gateway("GET", "/v1/models", unseen).status_code == 503, 15, 0.2)
        down = gateway("GET", "/v1/models", unseen)
        check("E4 store down: unseen key is 503 not 401", lag is not None and
              code_of(down) == "shared_state_unavailable",
              f"HTTP {down.status_code} {code_of(down)}")
        # Inside FRESH_MS (5 s, owner decision 0.7-2) a worker may still serve what it verified;
        # past it every worker must refuse a cached key too, and stop reporting ready.
        lag = wait_for(steadily(lambda: gateway("GET", "/v1/models", key2).status_code == 503),
                       20, 0.2)
        cached = gateway("GET", "/v1/models", key2)
        check("E4 store down: a CACHED valid key is 503 on every worker past FRESH_MS",
              lag is not None and code_of(cached) == "shared_state_unavailable",
              f"after {None if lag is None else round(lag, 1)} s: HTTP {cached.status_code} "
              f"{code_of(cached)}")
        ready_lag = wait_for(steadily(lambda: httpx.get(f"{GATEWAY}/readyz").status_code == 503),
                             10, 0.2)
        ready = httpx.get(f"{GATEWAY}/readyz")
        live = httpx.get(f"{GATEWAY}/livez")
        check("E4 /readyz 503 and /livez 200", ready_lag is not None and live.status_code == 200,
              f"/readyz {ready.status_code} {ready.text[:90]!r}; /livez {live.status_code}")
    finally:
        docker("start", "ai_mesh_firewall-redis-1")
    lag = wait_for(steadily(lambda: gateway("GET", "/v1/models", key2).status_code == 200),
                   60, 0.2)
    check("E5 store back: recovers by itself", lag is not None,
          f"valid key 200 again {None if lag is None else round(lag, 1)} s after redis start")

    # --- E6: no stamp (re-hydrators stopped) -> 503 --------------------------------------------
    docker("stop", "ai_mesh_firewall-state-rehydrator-a-1", "ai_mesh_firewall-state-rehydrator-b-1")
    try:
        lag = wait_for(steadily(lambda: gateway("GET", "/v1/models", key2).status_code == 503),
                       20, 0.2)
        stale = gateway("GET", "/v1/models", key2)
        check("E6 stamp ages out: 503 not 401", lag is not None and
              code_of(stale) == "shared_state_unavailable",
              f"503 {None if lag is None else round(lag, 1)} s after the re-hydrators stopped "
              f"({code_of(stale)})")
    finally:
        docker("start", "ai_mesh_firewall-state-rehydrator-a-1",
               "ai_mesh_firewall-state-rehydrator-b-1")
    lag = wait_for(steadily(lambda: gateway("GET", "/v1/models", key2).status_code == 200),
                   60, 0.2)
    check("E6 stamp back: recovers", lag is not None,
          f"200 again {None if lag is None else round(lag, 1)} s after the re-hydrators started")

    # --- E7: /metrics ------------------------------------------------------------------------
    metrics = httpx.get(f"{GATEWAY}/metrics")
    line = next((x for x in metrics.text.splitlines()
                 if x.startswith('amf_rejected{status="401",reason="rejected"}')), "")
    check("E7 /metrics 200 and counts 401s", metrics.status_code == 200 and line != ""
          and float(line.split()[-1]) > 0, line or "no 401 series")

    api.patch(f"/api/gateways/keys/{second.json()['id']}/", json={"is_active": False})
    print(json.dumps({"passed": sum(ok for _, ok, _ in results), "total": len(results)}))
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
