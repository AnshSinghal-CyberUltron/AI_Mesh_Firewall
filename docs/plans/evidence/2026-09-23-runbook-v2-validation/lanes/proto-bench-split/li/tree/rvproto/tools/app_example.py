"""An ordinary OpenAI-SDK application. It knows nothing about rvproto: the SDK reads
OPENAI_BASE_URL and OPENAI_API_KEY from the environment; swapping those is the only change."""

import json

from openai import OpenAI

client = OpenAI()

reply = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "system", "content": "You are terse."},
              {"role": "user", "content": "Name three rivers."}],
    max_tokens=10,
)
streamed = ""
for chunk in client.chat.completions.create(
    model="gpt-4o-mini", messages=[{"role": "user", "content": "Tell me a story."}],
    max_tokens=10, stream=True,
):
    if chunk.choices and chunk.choices[0].delta.content:
        streamed += chunk.choices[0].delta.content
tools = [{"type": "function", "function": {"name": "get_weather",
                                           "parameters": {"type": "object", "properties": {}}}}]
with client.chat.completions.stream(
    model="gpt-4o-mini", messages=[{"role": "user", "content": "Weather in Paris?"}], tools=tools,
    extra_headers={"x-synth-tool": "1"},  # synthetic-provider control header (test harness only)
) as s:
    final = s.get_final_completion()
call = final.choices[0].message.tool_calls[0]
models = [m.id for m in client.models.list().data]
print(json.dumps({
    "json_words": len((reply.choices[0].message.content or "").split()),
    "json_finish": reply.choices[0].finish_reason,
    "stream_words": len(streamed.split()),
    "tool": call.function.name,
    "tool_args": json.loads(call.function.arguments),
    "models_listed": bool(models),
}, sort_keys=True))
