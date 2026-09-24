"""P03b: tool-call arguments whose string values use JSON escapes (\\u0040, \\/, \\u0051).
Output: through rvproto org-a (output PII/secrets -> REDACT), reconstructed by the OpenAI SDK, then
json.loads'ed the way an agent framework does. Input: the same escaped arguments sent back in the
assistant history (org-a input secrets -> BLOCK)."""
import json, sys, httpx, openai, uuid
cl = openai.OpenAI(base_url="http://127.0.0.1:8480/v1", api_key="sk-rv-org-a-0001", max_retries=0,
                   default_headers={"x-synth-edge": "tool-escaped"})
tools = [{"type": "function", "function": {"name": "send_mail", "parameters": {"type": "object"}}}]
with cl.chat.completions.stream(model="gpt-4o-mini", messages=[{"role": "user", "content": "send it"}], tools=tools) as s:
    for _ in s: pass
    fin = s.get_final_completion()
args = fin.choices[0].message.tool_calls[0].function.arguments
parsed = json.loads(args)
out = {"output_args_on_wire": args, "output_args_after_json_loads": parsed,
       "raw_email_after_decode": parsed.get("to") == "alice.canary@example.com",
       "raw_aws_after_decode": parsed.get("key") == "AKIAQYLPMN5HHHFPZAM2"}
rid = "p03b-in-" + uuid.uuid4().hex[:6]
body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "send_mail", "arguments": args}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "sent"}]}
r = httpx.post("http://127.0.0.1:8480/v1/chat/completions", json=body, headers={"authorization": "Bearer sk-rv-org-a-0001", "x-request-id": rid})
out["input_escaped_args_status"] = r.status_code
out["input_escaped_args_disposition"] = r.headers.get("x-rv-disposition")
print(json.dumps(out, indent=1)); json.dump(out, open(sys.argv[1], "w"), indent=1)
