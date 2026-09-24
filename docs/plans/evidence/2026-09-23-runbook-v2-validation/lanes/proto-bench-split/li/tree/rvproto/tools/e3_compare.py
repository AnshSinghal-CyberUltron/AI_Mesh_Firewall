"""E3 verdict: the unmodified app yields the same typed structure directly and via rvproto.
Content words are provider-generated per request id, so only shape is compared."""
import json
import sys

a, b = (json.load(open(p)) for p in sys.argv[1:3])


def shape(d: dict) -> dict:
    return {"json_finish": d["json_finish"], "json_nonempty": d["json_words"] > 0,
            "stream_nonempty": d["stream_words"] > 0, "tool": d["tool"],
            "tool_arg_keys": sorted(d["tool_args"]), "models_listed": d["models_listed"]}


ok = shape(a) == shape(b)
print(json.dumps({"direct": shape(a), "rvproto": shape(b), "same_shape": ok}, sort_keys=True))
sys.exit(0 if ok else 1)
