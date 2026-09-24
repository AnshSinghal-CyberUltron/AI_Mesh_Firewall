#!/usr/bin/env python3
"""Execute mcp_scan_orchestrator._mcp_default_detection_enabled under controlled config/env.
Run with: env -i PATH=/usr/bin:/bin PYTHONPATH=<root>/gateway/ai_mesh_gateway:<root>/gateway:<root>/shared <venv-python> exec_default_detection.py <label>
"""
import os, sys, types, json
label = sys.argv[1] if len(sys.argv) > 1 else "?"
import mcp_scan_orchestrator as orch
import config as gwconfig
out = {"label": label, "orchestrator_file": orch.__file__, "config_file": gwconfig.__file__,
       "env_GATEWAY_MCP_DEFAULT_DETECTION": os.environ.get("GATEWAY_MCP_DEFAULT_DETECTION"),
       "env_keys_present": sorted(os.environ.keys())}
# 1) no main module loaded, clean env
out["1_no_main_module_clean_env"] = orch._mcp_default_detection_enabled()
# 2) fake 'main' module whose CONFIG is the REAL config.load_config() output under a clean env
cfg = gwconfig.load_config()
out["load_config_has_key"] = "mcp_default_detection_enabled" in cfg
out["load_config_n_keys"] = len(cfg)
fake = types.ModuleType("main"); fake.CONFIG = cfg; sys.modules["main"] = fake
out["2_main.CONFIG=load_config()_clean_env"] = orch._mcp_default_detection_enabled()
# 3) empty CONFIG dict
fake.CONFIG = {}
out["3_main.CONFIG={}_clean_env"] = orch._mcp_default_detection_enabled()
# 4) env var set -> flips
for v in ("1", "true", "on", "0", "false", ""):
    os.environ["GATEWAY_MCP_DEFAULT_DETECTION"] = v
    out[f"4_env={v!r}_CONFIG={{}}"] = orch._mcp_default_detection_enabled()
del os.environ["GATEWAY_MCP_DEFAULT_DETECTION"]
# 5) key present in CONFIG wins over env
fake.CONFIG = {"mcp_default_detection_enabled": True}
out["5_CONFIG_key=True"] = orch._mcp_default_detection_enabled()
os.environ["GATEWAY_MCP_DEFAULT_DETECTION"] = "1"
fake.CONFIG = {"mcp_default_detection_enabled": False}
out["5b_CONFIG_key=False_env=1"] = orch._mcp_default_detection_enabled()
print(json.dumps(out, indent=1))
