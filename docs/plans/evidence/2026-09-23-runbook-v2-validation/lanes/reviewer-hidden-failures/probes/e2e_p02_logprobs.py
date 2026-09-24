"""E2E P02: official OpenAI SDK through rvproto (org-a: output secret/PII -> REDACT) against a provider that
honours logprobs=true.  Does the redacted value still reach the application?"""
import json, sys
import openai
can = {c["id"]: c["value"] for c in json.load(open(sys.argv[1]))["canaries"]}
AWS, MAIL = can["out.aws"], can["out.email"]
cli = openai.OpenAI(base_url="http://127.0.0.1:47400/v1", api_key="sk-rv-org-a-0001", max_retries=0)
for lp in (False, True):
    r = cli.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], logprobs=lp)
    ch = r.choices[0]
    via_lp = "".join(t.token for t in (ch.logprobs.content if ch.logprobs else []))
    print(f"JSON logprobs={lp!s:<5}: content has AWS={AWS in (ch.message.content or '')} MAIL={MAIL in (ch.message.content or '')} | "
          f"logprobs tokens reassembled have AWS={AWS in via_lp} MAIL={MAIL in via_lp}")
    s = cli.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], logprobs=lp, stream=True)
    content, lpt = "", ""
    for ev in s:
        for c in ev.choices:
            content += c.delta.content or ""
            if c.logprobs and c.logprobs.content:
                lpt += "".join(t.token for t in c.logprobs.content)
    print(f"SSE  logprobs={lp!s:<5}: content has AWS={AWS in content} MAIL={MAIL in content} | "
          f"logprobs tokens reassembled have AWS={AWS in lpt} MAIL={MAIL in lpt}")
