"""Tokenize realistic assistant outputs with o200k_base (GPT-4o) into per-chunk token strings."""
import base64, json, os, random, sys
import tiktoken
enc = tiktoken.get_encoding("o200k_base")
random.seed(7)
samples = {
 "prose (control)": "The quick brown fox jumps over the lazy dog. Farmers rotate their crops so that the soil stays healthy from one season to the next, and the committee thanked the volunteers.",
 "markdown + URL": "See the official list at https://cloud.google.com/compute/docs/gpus/gpu-regions-zones#available-gpus-by-region-zone before you pick a zone, then retry.",
 "UUIDs": "Failed request IDs: 123e4567-e89b-12d3-a456-426614174000, 9f1c2d3e-4b5a-6c7d-8e9f-0a1b2c3d4e5f and 00000000-0000-4000-8000-000000000000 were retried.",
 "sha256 digest": "The image digest is sha256:5660b60f56e33e01513cf8885ebca18e46d82766ea3eb05348e3eff19cbccd51 and it matches the manifest.",
 "file paths": "Edit /home/user/projects/ai-mesh/gateway_v2/runtime/resources.py and /etc/nginx/conf.d/default.conf, then restart.",
 "numbers table": "| year | revenue | growth |\n| 2021 | 1,234,567.89 | 3.14 |\n| 2022 | 2,345,678.90 | 4.20 |\n| 2023 | 3,456,789.01 | 5.55 |",
 "python code": "def load(path: str) -> dict:\n    with open(path, encoding='utf-8') as fh:\n        return json.loads(fh.read())\n\nresult = load(os.path.join(BASE_DIR, 'config.settings.production.json'))\n",
 "json object": '{"order_id": "ORD-2024-000123", "customer": "acme-industries-international", "amount": 1234.56, "currency": "USD"}',
 "base64 (2 KB data URI)": "Here is the icon: data:image/png;base64," + base64.b64encode(random.randbytes(1536)).decode() + " — embed it inline.",
}
out = {name: [enc.decode([t]) for t in enc.encode(txt)] for name, txt in samples.items()}
big = base64.b64encode(random.randbytes(48 * 1024)).decode()   # 64 KB base64 blob
out["BIG base64 64KB"] = ["Here is the file: "] + [enc.decode([t]) for t in enc.encode(big)] + [" done."]
words = " ".join(random.choice(["the","and","for","you","that","with","this","from","have","are"]) for _ in range(16000))
out["BIG prose 64KB (control)"] = [enc.decode([t]) for t in enc.encode(words)]
json.dump(out, open(sys.argv[1], "w"))
print({k: (len(v), sum(map(len, v))) for k, v in out.items()})
