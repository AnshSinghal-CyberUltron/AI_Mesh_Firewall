"""filter_policies_by_domain must NOT mutate the shared cached policy list it
receives from PolicySync.get_policies() — otherwise concurrent same-org RAG
requests could corrupt the org's policy cache. Pins the returns-new-list,
input-unchanged invariant.
"""
from policy_sync import filter_policies_by_domain


def _entry(domain):
    return {"policy": {"id": 1, "policy_domain": domain}, "rules": []}


def test_returns_new_list_input_unchanged():
    shared = [_entry("rag"), _entry("mcp"), _entry("rag")]
    snapshot = list(shared)
    out = filter_policies_by_domain(shared, "rag")
    # input list object is unchanged (same length, same elements, same order)
    assert shared == snapshot and len(shared) == 3
    # output is a distinct list containing only the rag entries
    assert out is not shared
    assert len(out) == 2 and all(e["policy"]["policy_domain"] == "rag" for e in out)


def test_no_match_returns_empty_not_input():
    shared = [_entry("mcp")]
    out = filter_policies_by_domain(shared, "rag")
    assert out == [] and out is not shared and len(shared) == 1
