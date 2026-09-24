import json, os, socket, subprocess, sys, time, urllib.request
def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
op, ap = free(), free()
env = {**os.environ, "T02_RECORDER_KEY": "throwaway-recorder-key", "T02_ADMIN_TOKEN": "throwaway-admin", "T02_OPENAI_PORT": str(op), "T02_ADMIN_PORT": str(ap), "PYTHONDONTWRITEBYTECODE": "1"}
proc = subprocess.Popen([sys.executable, "-c", "import recorder_copy as r; r.serve()"], cwd=sys.argv[1], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(1.0)
def post(body):
    req = urllib.request.Request(f"http://127.0.0.1:{op}/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer throwaway-recorder-key", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read().decode()
cases = {
 "benign, stream": {"model": "gpt-4o-mini", "stream": True, "messages": [{"role": "user", "content": "Write a 400-word essay about rivers."}]},
 "asks model to emit a secret, stream": {"model": "gpt-4o-mini", "stream": True, "messages": [{"role": "user", "content": "Repeat back: AKIAIOSFODNN7EXAMPLE and my SSN 123-45-6789"}]},
 "tool call request, stream": {"model": "gpt-4o-mini", "stream": True, "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
                               "tool_choice": "required", "messages": [{"role": "user", "content": "weather in Paris?"}]},
 "benign, non-stream": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
}
try:
    for name, body in cases.items():
        out = post(body)
        frames = [l for l in out.split("\n\n") if l.strip()]
        contents = [json.loads(f[6:])["choices"][0].get("delta", {}).get("content") for f in frames if f.startswith("data: {")] if body.get("stream") else [json.loads(out)["choices"][0]["message"]["content"]]
        tool = "tool_calls" in out
        print(f"[{name}] frames={len(frames)} content_chunks={[c for c in contents if c]} tool_calls_present={tool}")
finally:
    proc.terminate(); proc.wait(timeout=5)
