"""P14: a normal multi-turn chat (each turn: ~250-token user message + ~400-token assistant reply
kept in history, as OpenAI SDK apps send it) through rvproto org-b (PG2 selected, FLAG).
Per turn: request PG2 tokens (model tokenizer), guard windows actually run (gateway counter delta),
HTTP status, latency. Shows (a) where the 1,024-token signed band ends, (b) where the firewall's
max-window reject starts, (c) guard work growth over a conversation."""
import json, sys, time, urllib.request
import httpx
from tokenizers import Tokenizer
SP, OUT = sys.argv[1], sys.argv[2]
tok = Tokenizer.from_file(f"{SP}/models/Llama-Prompt-Guard-2-22M/tokenizer.json"); tok.no_truncation(); tok.no_padding()
U = ("Here is the next part of my project notes. The team reviewed the warehouse schedule, the delivery routes "
     "and the training calendar, and we want a short plan for the coming weeks with owners and dates. ") * 6
A = ("Sure. Here is a concise plan: first confirm the warehouse schedule with the site leads, then review the "
     "delivery routes against the new customer list, and finally publish the training calendar to all staff. ") * 9
def counters():
    d = json.load(urllib.request.urlopen("http://127.0.0.1:8480/metrics.json"))["count"]
    return d.get("guard_windows", 0)
msgs = [{"role": "system", "content": "You are a helpful planning assistant."}]
rows = []
with httpx.Client(timeout=120) as c:
    for turn in range(1, 15):
        msgs.append({"role": "user", "content": U})
        n = len(tok.encode("\n".join(m["content"] for m in msgs), add_special_tokens=False).ids)
        g0 = counters(); t = time.perf_counter()
        r = c.post("http://127.0.0.1:8480/v1/chat/completions", json={"model": "gpt-4o-mini", "messages": msgs},
                   headers={"authorization": "Bearer sk-rv-org-b-0001", "x-synth-tokens": "2", "x-synth-ttft-ms": "0"})
        dt = time.perf_counter() - t
        row = {"turn": turn, "pg2_tokens": n, "in_signed_1024_band": n <= 1024, "status": r.status_code,
               "code": (r.json().get("error") or {}).get("code") if r.status_code >= 400 else None,
               "guard_windows_run": counters() - g0, "latency_s": round(dt, 2)}
        rows.append(row); print(json.dumps(row), flush=True)
        msgs.append({"role": "assistant", "content": A})
tot = sum(r["guard_windows_run"] for r in rows)
print(json.dumps({"total_guard_windows_over_conversation": tot}))
json.dump(rows, open(OUT, "w"), indent=1)
