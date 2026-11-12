import json
from core.firewall_v2_publish import policy_drafts, document_body

def nest(depth):
    value = "x"
    for _ in range(depth):
        value = [value]
    return value

def json_depth(obj):
    best, stack = 0, [(obj, 1)]
    while stack:
        item, d = stack.pop()
        if isinstance(item, (list, dict)):
            best = max(best, d)
            stack.extend((v, d + 1) for v in (item.values() if isinstance(item, dict) else item))
    return best

deep = nest(500)
condition = {"regex": "secret", "pattern": deep, "keywords": [deep, "kw"], "field": deep,
             "direction": deep, "surfaces": [deep, "chat"], "detector_class": deep,
             "allow_hosts": deep, "extra": deep}
policy = {"code": "P", "policy_domain": "pipeline", "enabled": True, "category": deep, "priority": 5,
          "rules": [
              {"id": 1, "name": "r", "enabled": True, "rule_type": "regex", "action": "block",
               "priority": 1, "pipeline_stage": deep, "condition": condition},
              {"id": 2, "name": "k", "enabled": True, "rule_type": "keywords", "action": "block",
               "priority": 1, "pipeline_stage": "", "condition": condition},
          ]}
rows, unsupported = policy_drafts([policy], "acme")
body, _ = document_body("acme", {"firewall_enabled": True}, rows, "acme")
raw = json.dumps(body, separators=(",", ":"), sort_keys=True)
print("customer condition/policy JSON depth in:", json_depth(policy))
print("published plan body JSON depth out:", json_depth(json.loads(raw)), "| rules:", len(body["rules"]))
print("customer rows published:", [r["rule_id"] for r in body["rules"] if r["rule_id"].startswith("pol-")])
