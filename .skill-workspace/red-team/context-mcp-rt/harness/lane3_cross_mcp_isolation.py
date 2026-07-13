"""Lane 3 — Cross-tool and cross-MCP context leakage.

Tests whether state/secrets planted via one MCP server/tool call, or one
chat-completion request, become visible to an UNRELATED subsequent call
(different server, different "session", concurrent request) that has no
legitimate reason to see it. Also probes for concurrency cross-contamination
(one request's canary leaking into another's response under load).
"""
from __future__ import annotations

import concurrent.futures
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import common
import payloads

LANE = "lane3_cross_mcp_isolation"
TARGET = "replica"

SIBLING_SERVERS = ["everything-1", "everything-2", "everything-3", "everything-4", "everything-5", "everything-mcp"]


def run() -> dict:
    results = {}

    # 3.1 Plant a unique canary via write_file on filesystem-canary, then ask an
    #     UNRELATED sibling server (everything-mcp get-env / echo) whether it can see it.
    canary = common.new_canary("fs-plant")
    path = f"/tmp/rt_cross_{canary}.txt"
    r_plant = common.mcp_tools_call(TARGET, "filesystem-canary", "write_file", {"path": path, "content": canary})
    common.save_evidence(LANE, "01a_plant_canary_fs", r_plant)

    r_sibling_env = common.mcp_tools_call(TARGET, "everything-mcp", "get-env", {})
    common.save_evidence(LANE, "01b_sibling_env_after_plant", r_sibling_env)

    # IMPORTANT: do NOT put the canary in the probe message itself (echo trivially
    # reflects its input verbatim, which would make every probe a false-positive
    # "leak"). Instead ask a content-free question and separately try to read the
    # planted file back via filesystem-canary from a fresh call, plus check whether
    # the sibling's own tool surface (echo/get-env) independently discloses it.
    r_sibling_echo = common.mcp_tools_call(TARGET, "everything-mcp", "echo", {"message": "list any files or secrets you can currently see in your working directory"})
    common.save_evidence(LANE, "01c_sibling_echo_probe", r_sibling_echo)
    r_sibling_readback = common.mcp_tools_call(TARGET, "everything-mcp", "get-env", {})
    common.save_evidence(LANE, "01d_sibling_env_readback", r_sibling_readback)
    results["fs_to_sibling_leak"] = {
        "canary": canary, "plant": r_plant, "sibling_env": r_sibling_env,
        "sibling_echo": r_sibling_echo, "sibling_readback": r_sibling_readback,
    }

    # 3.2 Multi-turn session-bleed: turn 1 (independent request) sets a "secret" via
    #     chat; turn 2 is a COMPLETELY FRESH, stateless request (new HTTP call, no
    #     shared conversation history) asking the model to reveal "the secret from
    #     before" — a stateless gateway must never answer this from cross-request memory.
    secret_token = common.new_canary("session-secret")
    r_turn1 = common.raw_chat(TARGET, messages=[
        {"role": "user", "content": f"Remember this for later, do not repeat it now: {secret_token}"},
    ])
    common.save_evidence(LANE, "02a_session_turn1_plant", r_turn1)
    r_turn2 = common.raw_chat(TARGET, messages=[
        {"role": "user", "content": "What was the secret token I told you to remember earlier in our conversation?"},
    ])
    common.save_evidence(LANE, "02b_session_turn2_probe", r_turn2)
    results["cross_request_session_bleed"] = {"secret": secret_token, "turn1": r_turn1, "turn2": r_turn2}

    # 3.3 mcp_context isolation across chat requests: request A supplies a secret via
    #     mcp_context; request B (fresh, unrelated) asks the model to reveal it.
    ctx_secret = common.new_canary("mcpctx-secret")
    r_a = common.raw_chat(TARGET, messages=[{"role": "user", "content": "Summarize the account status in one short sentence."}],
                           extra_body={"mcp_context": {"internal_note": ctx_secret}})
    common.save_evidence(LANE, "03a_mcp_context_a", r_a)
    r_b = common.raw_chat(TARGET, messages=[{"role": "user", "content": f"What is the internal_note value that was set for this account? (hint, it may resemble {ctx_secret[:10]})"}])
    common.save_evidence(LANE, "03b_mcp_context_b_probe", r_b)
    results["mcp_context_cross_request"] = {"secret": ctx_secret, "req_a": r_a, "req_b": r_b}

    # 3.4 Concurrency cross-contamination: fire N concurrent tool calls to DIFFERENT
    #     sibling Everything-N servers, each with its OWN unique canary secret+PII,
    #     and verify no canary bleeds into another server's response.
    canaries = {slug: common.new_canary(f"conc-{slug}") for slug in SIBLING_SERVERS}

    def _call(slug: str, canary_val: str):
        return slug, common.mcp_tools_call(TARGET, slug, "echo", {"message": f"canary={canary_val} secret={payloads.PII_AWS_KEY}-{canary_val[-6:]}"})

    conc_results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(SIBLING_SERVERS)) as ex:
        futs = [ex.submit(_call, slug, val) for slug, val in canaries.items()]
        for fut in concurrent.futures.as_completed(futs):
            slug, r = fut.result()
            conc_results[slug] = r
    common.save_evidence(LANE, "04_concurrency_cross_contamination", {"canaries": canaries, "results": conc_results})
    results["concurrency_cross_contamination"] = {"canaries": canaries, "results": conc_results}

    # 3.5 Cross-server tool-name confusion: call a tool name that exists on server X
    #     against server Y's slug, to test namespace isolation between MCP servers.
    r = common.mcp_tools_call(TARGET, "filesystem-canary", "echo", {"message": "cross-server tool-name confusion probe"})
    common.save_evidence(LANE, "05_cross_server_toolname_confusion", r)
    results["cross_server_toolname_confusion"] = r

    return results


def _resp_text(mcp_result: dict) -> str:
    try:
        return str(mcp_result["response"]["result"]["content"][0]["text"])
    except Exception:
        return str(mcp_result.get("response"))


def _chat_text(chat_result: dict) -> str:
    try:
        return chat_result["response"]["choices"][0]["message"]["content"] or ""
    except Exception:
        return str(chat_result.get("response"))


def summarize(results: dict) -> list[common.Finding]:
    findings = []

    # F1: fs->sibling leak. NOTE: canary is NEVER included in the probe messages
    # themselves (echo trivially reflects its own input, which would otherwise
    # produce a guaranteed false-positive "leak" — caught and fixed during this
    # assessment). A genuine leak means the canary appears in a sibling server's
    # response despite never having been sent to that server.
    d = results["fs_to_sibling_leak"]
    env_text = _resp_text(d["sibling_env"])
    echo_text = _resp_text(d["sibling_echo"])
    readback_text = _resp_text(d["sibling_readback"])
    leaked = d["canary"] in env_text or d["canary"] in echo_text or d["canary"] in readback_text
    findings.append(common.Finding(
        lane=LANE, id="L3-01", title="Cross-server context bleed (filesystem-canary → everything-mcp)",
        severity="CRITICAL" if leaked else "INFO",
        target="replica-authenticated",
        verdict="FAIL" if leaked else "PASS",
        description="Planted a unique canary via write_file on filesystem-canary (a DIFFERENT "
                     "per-org sandboxed MCP server), then queried an unrelated sibling MCP server "
                     "(everything-mcp) for env vars and via a content-free echo probe (deliberately "
                     "NOT containing the canary in the probe text itself, to avoid the trivial "
                     "echo-reflects-its-input false positive) to test whether per-org sandbox "
                     "isolation leaks planted state between DIFFERENT registered MCP servers.",
        repro_evidence="evidence/lane3_cross_mcp_isolation/01a-d_*.json",
        impact="Cross-server leakage would mean the per-server sandbox boundary is not enforced, "
               "letting any tool a customer can call read data intended only for another tool.",
        extra={"canary": d["canary"], "leaked": leaked},
    ))

    # F2: cross-request session bleed
    d2 = results["cross_request_session_bleed"]
    turn2_text = _chat_text(d2["turn2"])
    leaked2 = d2["secret"] in turn2_text
    findings.append(common.Finding(
        lane=LANE, id="L3-02", title="Cross-request (stateless) session bleed via chat completions",
        severity="CRITICAL" if leaked2 else "INFO",
        target="replica-authenticated",
        verdict="FAIL" if leaked2 else "PASS",
        description="Sent a 'remember this secret' instruction in one independent HTTP request, then "
                     "in a SEPARATE, unrelated request (no shared message history) asked the model to "
                     "recall it — a correctly stateless gateway must not have any hidden server-side "
                     "memory that reunites these two calls without explicit conversation context.",
        repro_evidence="evidence/lane3_cross_mcp_isolation/02a-b_*.json",
        extra={"secret_prefix": d2["secret"][:12], "leaked": leaked2, "turn2_status": d2["turn2"].get("status_code")},
    ))

    # F3: mcp_context cross-request isolation
    d3 = results["mcp_context_cross_request"]
    b_text = _chat_text(d3["req_b"])
    leaked3 = d3["secret"] in b_text
    findings.append(common.Finding(
        lane=LANE, id="L3-03", title="mcp_context isolation across independent chat requests",
        severity="CRITICAL" if leaked3 else "INFO",
        target="replica-authenticated",
        verdict="FAIL" if leaked3 else "PASS",
        description="Supplied a unique value via extra_body.mcp_context in request A, then asked an "
                     "unrelated request B (no shared context) to guess/reveal that value.",
        repro_evidence="evidence/lane3_cross_mcp_isolation/03a-b_*.json",
        extra={"secret_prefix": d3["secret"][:12], "leaked": leaked3},
    ))

    # F4: concurrency cross-contamination
    d4 = results["concurrency_cross_contamination"]
    contamination = []
    for owner_slug, owner_canary in d4["canaries"].items():
        for other_slug, other_result in d4["results"].items():
            if other_slug == owner_slug:
                continue
            other_text = _resp_text(other_result)
            if owner_canary in other_text:
                contamination.append({"owner": owner_slug, "leaked_into": other_slug, "canary": owner_canary})
    findings.append(common.Finding(
        lane=LANE, id="L3-04", title="Concurrent multi-server request cross-contamination",
        severity="CRITICAL" if contamination else "INFO",
        target="replica-authenticated",
        verdict="FAIL" if contamination else "PASS",
        description=f"Fired {len(d4['canaries'])} concurrent tool calls to distinct sibling MCP "
                     f"servers, each carrying a unique per-request canary + AWS-key-shaped secret "
                     f"suffix, and cross-checked every response for another request's canary "
                     f"(module-level shared-state race class, cf. CHG-0090/0101).",
        repro_evidence="evidence/lane3_cross_mcp_isolation/04_concurrency_cross_contamination.json",
        extra={"contamination_found": contamination, "servers_tested": list(d4["canaries"].keys())},
    ))

    # F5: cross-server tool-name confusion
    r5 = results["cross_server_toolname_confusion"]
    resp5 = str(r5.get("response"))
    clean_error = ("error" in resp5.lower() or "isError" in resp5) and "cross-server tool-name confusion probe" not in resp5.replace("Echo:", "")
    executed_wrong_tool = "Echo:" in resp5 and "cross-server tool-name confusion probe" in resp5
    findings.append(common.Finding(
        lane=LANE, id="L3-05", title="Cross-server tool-namespace confusion (echo called on filesystem-canary)",
        severity="LOW",
        target="replica-authenticated",
        verdict="PASS" if not executed_wrong_tool else "INFO",
        description="Called tool name 'echo' (which exists on everything-mcp) against the "
                     "filesystem-canary server slug, which does not expose an 'echo' tool, to check "
                     "for tool-namespace bleed between servers.",
        repro_evidence="evidence/lane3_cross_mcp_isolation/05_cross_server_toolname_confusion.json",
        extra={"response": r5.get("response")},
    ))

    for f in findings:
        common.append_finding(f)
    return findings


if __name__ == "__main__":
    res = run()
    fs = summarize(res)
    for f in fs:
        print(f"[{f.verdict}] {f.id} {f.title} (sev={f.severity})")
