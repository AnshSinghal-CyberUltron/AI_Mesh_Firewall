"""Build (or verify) the ORT TensorRT-EP engine cache once, in ONE process, for every visible GPU,
using exactly the session options the local_gpu workers use; workers then load from the cache
instead of building N engines concurrently at startup."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rvproto.detect.guard.factory import gpu_count, model_hash  # noqa: E402
from rvproto.detect.guard.local_onnx import LocalOnnxBackend  # noqa: E402
from rvproto.runtime.config import load_settings  # noqa: E402
from rvproto.runtime.metrics import Registry  # noqa: E402

s = load_settings()
out = []
for dev in range(gpu_count(s)):
    b = LocalOnnxBackend(s, Registry(0), gpu=True, device_id=dev, model_hash=model_hash(s))
    t0 = time.time()
    b.session = b._make_session()
    t1 = time.time()
    tps = b._warmup()
    out.append({"device": dev, "providers": b.providers, "session_s": round(t1 - t0, 1),
                "warmup_s": round(time.time() - t1, 1), "tokens_per_s": round(tps)})
print(json.dumps({"trt_cache": s.trt_cache_dir, "devices": out}))
