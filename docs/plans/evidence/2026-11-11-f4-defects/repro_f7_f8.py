import asyncio, sys
sys.path.insert(0, ".")
from gateway_v2.dispatch.rag_dev_store import InMemoryVectorAdapter
from gateway_v2.dispatch.vector_adapter import QueryRequest
from gateway_v2.edge import errors
r = asyncio.run(InMemoryVectorAdapter().query(QueryRequest(org_id="o", store_id="s", collection="never_created", vector=(1.0, 0.0), top_k=3)))
print("F7 dev adapter missing collection ->", r.code, "-> HTTP", errors.render_http(r.code).status)
# F8: which detectors the non-stream path chains
from gateway_v2.edge.output_detectors import build_output_detectors
from tests.edge import _gw35_harness as h
import inspect
src = inspect.getsource(build_output_detectors)
print("F8 whole_response chains grounding:", "grounding" in src.split("whole_response=")[-1].split(")")[0])
