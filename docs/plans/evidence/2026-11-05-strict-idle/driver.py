"""Live strict-withhold-idle-timeout driver (task 5). Every call is a REAL HTTP/SSE request against
the booted amff1 gateway-v2 using the stock openai SDK and raw httpx for wire bytes + timing.

AFTER = amff1 (this worktree's fix, built into the image). It runs inside a container on the
amff1_default network, so BASE is http://gateway-v2:8301/v1.

Checks (see SUMMARY.md):
  b  strict dev-org + SLOWSTREAM  -> 200, full content after ~6 s, zero 408, idle counter unchanged
  c  strict dev-org + SLOWLEAK    -> terminal output_blocked, no content before it
  d  strict dev-org + SLOWSTALL   -> cut with stream_inter_chunk_timeout
  e  incremental org-b + SLOWSTREAM -> streams each chunk, unchanged

Not product code. Results are one JSON line per check, written to the mounted evidence dir.
"""
import json
import os
import re
import time

import httpx
import openai

BASE = os.environ.get("GW_BASE", "http://gateway-v2:8301/v1")
STRICT_KEY = os.environ.get("STRICT_KEY", "dev-key-change-me")   # -> org dev-org (strict)
INCR_KEY = os.environ.get("INCR_KEY", "dev-peer-key")            # -> org org-b (incremental)
OUT = os.environ.get("OUT", "/evidence/after-checks.jsonl")

_fh = open(OUT, "w")


def emit(rec):
    _fh.write(json.dumps(rec) + "\n")
    _fh.flush()
    print(json.dumps(rec)[:400])


def metrics_text():
    try:
        r = httpx.get(BASE.rsplit("/v1", 1)[0] + "/metrics", timeout=10)
        return r.text
    except Exception as exc:  # noqa: BLE001
        return f"<metrics error {type(exc).__name__}: {exc}>"


# The authoritative stream-idle-timeout cut counter (confirmed on the live /metrics scrape).
_IDLE_METRIC = "amf_posture_refusals_stream_idle_timeout_total"
_INTER_CHUNK_METRIC = "amf_posture_refusals_stream_inter_chunk_timeout_total"


def _scrape(text, names):
    rows = {}
    total = 0.0
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = re.match(r"(\S+)\s+([0-9eE.+-]+)$", line)
        if m and (m.group(1) in names or m.group(1).split("{")[0] in names):
            rows[m.group(1)] = m.group(2)
            try:
                total += float(m.group(2))
            except ValueError:
                pass
    return total, rows


def idle_counter(text):
    """Value of amf_posture_refusals_stream_idle_timeout_total (the stream-idle cut counter)."""
    return _scrape(text, {_IDLE_METRIC})


def raw_stream(key, prompt, read_timeout=60.0):
    """Raw SSE over httpx: capture status, time-to-first-byte, elapsed, and the decoded body."""
    url = BASE + "/chat/completions"
    body = {"model": "echo-1", "stream": True,
            "messages": [{"role": "user", "content": prompt}]}
    t0 = time.time()
    first_byte_s = None
    status = None
    resp_headers = {}
    raw = []
    try:
        with httpx.stream("POST", url, headers={"authorization": f"Bearer {key}"},
                          json=body, timeout=httpx.Timeout(read_timeout, connect=10)) as r:
            status = r.status_code
            resp_headers = {k.lower(): v for k, v in r.headers.items()}
            for chunk in r.iter_raw():
                if chunk:
                    if first_byte_s is None:
                        first_byte_s = round(time.time() - t0, 3)
                    raw.append(chunk)
    except httpx.HTTPError as exc:
        return {"transport_error": f"{type(exc).__name__}: {exc}", "elapsed_s": round(time.time() - t0, 3)}
    text = b"".join(raw).decode("utf-8", "replace")
    lines = [l for l in text.split("\n") if l]
    data_lines = [l for l in lines if l.startswith("data: ") and l != "data: [DONE]"]
    content = ""
    finish = None
    error_frame = None
    for l in data_lines:
        try:
            obj = json.loads(l[len("data: "):])
        except ValueError:
            continue
        if "error" in obj:
            error_frame = obj["error"]
        for ch in obj.get("choices", []) or []:
            d = ch.get("delta") or {}
            content += d.get("content") or ""
            finish = ch.get("finish_reason") or finish
    return {
        "status": status,
        "resp_headers": {k: resp_headers.get(k) for k in
                         ("content-type", "x-request-id", "x-zeroshield-action") if k in resp_headers},
        "time_to_first_byte_s": first_byte_s,
        "elapsed_s": round(time.time() - t0, 3),
        "content_len": len(content),
        "content_preview": content[:200],
        "finish_reason": finish,
        "error_frame": error_frame,
        "done_on_wire": "data: [DONE]" in lines,
        "n_data_frames": len(data_lines),
        "body_tail": text[-300:],
    }


def sdk_stream(key, prompt):
    """Stock openai SDK streaming: what a real client sees (status via APIStatusError)."""
    client = openai.OpenAI(api_key=key, base_url=BASE, max_retries=0, timeout=60)
    t0 = time.time()
    got = 0
    text = ""
    try:
        stream = client.chat.completions.create(
            model="echo-1", stream=True, messages=[{"role": "user", "content": prompt}])
        for c in stream:
            if c.choices and c.choices[0].delta and c.choices[0].delta.content:
                got += 1
                text += c.choices[0].delta.content
        return {"ok": True, "chunks": got, "text_len": len(text), "text_preview": text[:200],
                "elapsed_s": round(time.time() - t0, 3)}
    except openai.APIStatusError as exc:
        code = (exc.body or {}).get("code") if isinstance(exc.body, dict) else None
        return {"ok": False, "status": exc.status_code, "code": code,
                "message": str(exc)[:200], "elapsed_s": round(time.time() - t0, 3)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                "elapsed_s": round(time.time() - t0, 3)}


def run():
    # Metric name discovery + before snapshot.
    m_before = metrics_text()
    before_total, before_rows = idle_counter(m_before)
    emit({"check": "metric-name-probe",
          "idle_metric_rows_before": before_rows,
          "idle_sum_before": before_total,
          "note": "rows whose name contains 'stream' and 'idle' (the stream-idle cut counter)"})

    # (b) strict + SLOWSTREAM
    b_raw = raw_stream(STRICT_KEY, "SLOWSTREAM please answer slowly")
    b_sdk = sdk_stream(STRICT_KEY, "SLOWSTREAM please answer slowly")
    m_after_b = metrics_text()
    after_total, after_rows = idle_counter(m_after_b)
    emit({"check": "b_strict_slowstream_after", "org": "dev-org(strict)",
          "raw": b_raw, "sdk": b_sdk,
          "idle_sum_before": before_total, "idle_sum_after": after_total,
          "idle_counter_unchanged": before_total == after_total,
          "idle_metric_rows_after": after_rows})

    # (c) strict + SLOWLEAK (internal IP mid-stream -> output_blocked)
    c_raw = raw_stream(STRICT_KEY, "SLOWLEAK describe the server")
    c_sdk = sdk_stream(STRICT_KEY, "SLOWLEAK describe the server")
    emit({"check": "c_strict_slowleak_after", "org": "dev-org(strict)",
          "raw": c_raw, "sdk": c_sdk,
          "note": "expect terminal output_blocked, zero content before the block"})

    # (d) strict + SLOWSTALL (1.5 s mid-stream pause -> stream_inter_chunk_timeout)
    ic_before, _ = _scrape(metrics_text(), {_INTER_CHUNK_METRIC})
    idle_before_d, _ = idle_counter(metrics_text())
    d_raw = raw_stream(STRICT_KEY, "SLOWSTALL then stall")
    d_sdk = sdk_stream(STRICT_KEY, "SLOWSTALL then stall")
    m_after_d = metrics_text()
    ic_after, _ = _scrape(m_after_d, {_INTER_CHUNK_METRIC})
    idle_after_d, _ = idle_counter(m_after_d)
    emit({"check": "d_strict_slowstall_after", "org": "dev-org(strict)",
          "raw": d_raw, "sdk": d_sdk,
          "inter_chunk_before": ic_before, "inter_chunk_after": ic_after,
          "inter_chunk_incremented": ic_after > ic_before,
          "idle_before": idle_before_d, "idle_after": idle_after_d,
          "idle_unchanged": idle_before_d == idle_after_d,
          "note": "expect stream_inter_chunk_timeout (upstream stall), NOT stream_idle_timeout"})

    # (e) incremental org-b + SLOWSTREAM (unchanged, streams each chunk)
    e_raw = raw_stream(INCR_KEY, "SLOWSTREAM please answer slowly")
    e_sdk = sdk_stream(INCR_KEY, "SLOWSTREAM please answer slowly")
    emit({"check": "e_incremental_slowstream_after", "org": "org-b(incremental)",
          "raw": e_raw, "sdk": e_sdk,
          "note": "expect 200, streams each chunk, unchanged by the fix"})

    emit({"check": "openai-sdk-version", "openai": openai.__version__, "httpx": httpx.__version__})


if __name__ == "__main__":
    run()
