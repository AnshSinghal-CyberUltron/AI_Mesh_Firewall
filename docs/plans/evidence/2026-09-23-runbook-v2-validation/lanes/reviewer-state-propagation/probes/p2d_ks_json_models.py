"""P2d: kill switch vs a JSON request already admitted (provider takes 3 s) and vs GET /v1/models."""
import threading, time, json
import httpx
from pc import BASE, MODEL, log, r
KEY = "sk-rv-lat-00000-probe"
out = {}
def slow():
    t = time.time()
    with httpx.Client(base_url=BASE, timeout=30) as c:
        x = c.post("/v1/chat/completions", headers={"authorization": f"Bearer {KEY}", "x-synth-ttft-ms": "3000"},
                   json={"model": MODEL, "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]})
    out["json_admitted_before_flip"] = {"status": x.status_code, "completed_after_s": round(time.time() - t, 2), "body": x.text[:140]}
th = threading.Thread(target=slow); th.start()
time.sleep(0.8)
r().set("rv:killswitch", "1"); t_flip = time.time()
time.sleep(1.0)
with httpx.Client(base_url=BASE, timeout=30) as c:
    m = c.get("/v1/models", headers={"authorization": f"Bearer {KEY}"})
    n = c.post("/v1/chat/completions", headers={"authorization": f"Bearer {KEY}"},
               json={"model": MODEL, "max_tokens": 3, "messages": [{"role": "user", "content": "hi"}]})
th.join()
r().set("rv:killswitch", "0")
log("KS global engaged at t_flip", new_chat_request=f"{n.status_code} {n.json()['error']['code']}",
    get_v1_models=m.status_code, **out, flip_to_completion_s=round(time.time() - t_flip, 2))
