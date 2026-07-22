#!/usr/bin/env python3
"""Generate MODULE2_CLIENT_DEMO_GUIDE.pdf for customer presentations."""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "MODULE2_CLIENT_DEMO_GUIDE.pdf"


class GuidePDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(100, 116, 139)
            self.cell(0, 8, "ZeroShield Module 2 - Client Demo Guide", align="L")
            self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(148, 163, 184)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")

    def section_title(self, title: str):
        self.ln(4)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(15, 118, 110)
        self.multi_cell(0, 8, title)
        self.set_draw_color(204, 251, 241)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def sub_title(self, title: str):
        self.ln(2)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(19, 78, 74)
        self.multi_cell(0, 6, title)
        self.ln(1)

    def body(self, text: str):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 41, 59)
        self.multi_cell(0, 5.5, text)
        self.ln(2)

    def box(self, label: str, text: str, rgb: tuple[int, int, int]):
        self.set_fill_color(*rgb)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(30, 41, 59)
        self.cell(0, 6, label, ln=True, fill=True)
        self.set_font("Helvetica", "", 9.5)
        self.multi_cell(0, 5, text, fill=True)
        self.ln(3)

    def client_line(self, text: str):
        self.set_font("Helvetica", "I", 9.5)
        self.set_text_color(71, 85, 105)
        self.set_fill_color(248, 250, 252)
        self.multi_cell(0, 5, f'"{text}"', fill=True)
        self.ln(2)

    def table(self, headers: list[str], rows: list[list[str]], col_widths: list[int] | None = None):
        if col_widths is None:
            w = 190 / len(headers)
            col_widths = [w] * len(headers)
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(15, 118, 110)
        self.set_text_color(255, 255, 255)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 7, h, border=1, fill=True)
        self.ln()
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(30, 41, 59)
        fill = False
        for row in rows:
            if fill:
                self.set_fill_color(248, 250, 252)
            else:
                self.set_fill_color(255, 255, 255)
            line_h = 6
            x0, y0 = self.get_x(), self.get_y()
            heights = []
            for i, cell in enumerate(row):
                self.set_xy(x0 + sum(col_widths[:i]), y0)
                self.multi_cell(col_widths[i], line_h, cell, border=0, fill=fill)
                heights.append(self.get_y() - y0)
            max_h = max(heights) if heights else line_h
            for i in range(len(row)):
                self.rect(x0 + sum(col_widths[:i]), y0, col_widths[i], max_h)
            self.set_xy(x0, y0 + max_h)
            fill = not fill
        self.ln(3)


def build() -> None:
    pdf = GuidePDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()

    # Cover
    pdf.ln(30)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(13, 148, 136)
    pdf.cell(0, 8, "ZEROSHIELD AI MESH FIREWALL", align="C", ln=True)
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(15, 118, 110)
    pdf.multi_cell(0, 12, "Module 2\nClient Demo Guide", align="C")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(71, 85, 105)
    pdf.multi_cell(
        0,
        7,
        "SOC Intelligence & Response\nUse Cases, Objectives & Screen-by-Screen Walkthrough",
        align="C",
    )
    pdf.ln(12)
    pdf.set_font("Helvetica", "I", 10)
    pdf.multi_cell(
        0,
        6,
        "For security leaders, SOC managers, and platform owners\nCompanion to the ZeroShield OpenAI Demo Application",
        align="C",
    )

    # Intro
    pdf.add_page()
    pdf.section_title("1. What Module 2 Is")
    pdf.body(
        "Your AI firewall (Module 1) stops bad traffic in real time. Module 2 is where your security team "
        "lives after that. It turns enforcement decisions into dashboards, risk scores, threat indicators, "
        "and formal incident cases."
    )
    pdf.body(
        "Module 1 is the guard at the door. Module 2 is the security operations center watching the cameras, "
        "reviewing who keeps knocking, and deciding when to lock someone out entirely."
    )
    pdf.sub_title("Four Questions Module 2 Answers")
    for q in [
        "Are attacks or misuse going up right now?",
        "Which API keys, models, tools, or data paths are highest risk?",
        "Which cases need immediate attention?",
        "What containment action should we take?",
    ]:
        pdf.body(f"  - {q}")
    pdf.body(
        "Everything on Module 2 screens comes from real enforcement telemetry. Demo app and simulator traffic "
        "appears on these screens within seconds."
    )

    # M2.1
    pdf.add_page()
    pdf.section_title("M2.1 - SOC Command Center (Dashboard)")
    pdf.sub_title("In Plain Language")
    pdf.body(
        "The Dashboard is your single pane of glass for AI security posture: traffic volume, blocks, "
        "redactions, lane pressure (Chat, RAG, Vector, MCP, Threat Intel), and live activity."
    )
    pdf.box(
        "Use Case",
        "Monday morning: Did anything bad happen over the weekend? Scan KPIs, lane cards, and the live ticker, "
        "then drill into UEBA, Model/RAG, or MCP for detail.",
        (239, 246, 255),
    )
    pdf.box(
        "Objective",
        "Give leadership and analysts a fast, trustworthy snapshot without digging through logs.",
        (254, 252, 232),
    )
    pdf.sub_title("Top KPI Cards")
    pdf.table(
        ["KPI", "What It Means"],
        [
            ["Gateway Requests", "Distinct AI requests in the selected window."],
            ["Blocked", "Requests fully stopped - dangerous output never returned."],
            ["Redacted", "Allowed after PII/secrets were masked."],
            ["Monitored", "Allowed but flagged for analyst review."],
            ["Rerouted", "Sent to a different model than requested."],
            ["Block Rate", "Percentage hard-stopped - headline risk number."],
        ],
        [45, 145],
    )
    pdf.sub_title("Charts & Tables")
    pdf.body("Lane Summary Cards - five lanes with total events, blocks, block rate; each links to a deep-dive page.")
    pdf.client_line(
        "If the RAG card is red while Chat is calm, the problem is probably document injection - not general chat abuse."
    )
    pdf.body("Threat Timeline (area chart) - hourly Total (blue), Blocked (red), Redacted (amber).")
    pdf.client_line("A sudden red spike at 2 AM might mean an automated attack or misconfigured integration.")
    pdf.body("High-Risk Ticker - live enforcement events and open incidents with expandable forensic detail.")

    # M2.2
    pdf.add_page()
    pdf.section_title("M2.2 - API Key Behavior Analytics (UEBA)")
    pdf.sub_title("In Plain Language")
    pdf.body(
        "UEBA watches how each API key behaves over time. A key sending 500 injection attempts after normally "
        "sending 10 requests per hour is a compromise signal - even if each request was blocked."
    )
    pdf.box(
        "Use Case",
        "Repeated blocks from one key: open UEBA, review behavior profile, disable key or activate kill switch.",
        (239, 246, 255),
    )
    pdf.box("Objective", "Move from 'something was blocked' to 'this identity is risky and needs containment'.", (254, 252, 232))
    pdf.table(
        ["KPI", "What It Means"],
        [
            ["Total / Active Keys", "Registered fleet vs currently enabled keys."],
            ["Disabled Keys", "Turned off - all requests fail authentication."],
            ["Active Kill Switches", "Emergency containment currently blocking traffic."],
            ["High Behavioral Risk", "Keys in the High UEBA risk band - priority review."],
        ],
        [50, 140],
    )
    pdf.body("Behavior Timeline - total, blocked, redacted over time.")
    pdf.client_line("Shows whether abuse is a one-off spike or a sustained campaign against one credential.")
    pdf.body("Top Risky Keys + Fleet Table - click a key for profile sidebar, containment actions, recent prompts.")
    pdf.client_line("An analyst can disable a key here - the gateway blocks the next request immediately.")

    # M2.3
    pdf.add_page()
    pdf.section_title("M2.3 - Threat Intelligence Ops")
    pdf.body(
        "Manage known-bad patterns (IOCs). The IOC table is configuration; KPIs and charts are live enforcement. "
        "Add/edit/delete syncs to the gateway automatically (Redis firewall:threat_intel:{org})."
    )
    pdf.box(
        "Use Case",
        "Add jailbreak phrase as IOC with Auto-Block, save (auto-sync), send matching prompt in demo - IOC Matches KPI rises.",
        (239, 246, 255),
    )
    pdf.table(
        ["KPI / Chart", "What It Means"],
        [
            ["Injection & Jailbreak", "Prompt injection attempts including simulator blocks."],
            ["PII Detected", "Sensitive data redacted by policy."],
            ["IOC Matches", "Traffic matching your synced indicator library (Threat Intel policy blocks)."],
            ["Attack Categories Over Time", "Hourly mix: Injection, PII, API Keys, IOC Matches."],
            ["Where IOC Matches Fired", "Pipeline stage: Ingress, Query, Retriever, etc."],
        ],
        [55, 135],
    )
    pdf.client_line(
        "IOC blocks use gateway code threat_intel_blocked — distinct from generic Policy Management blocks."
    )

    # M2.4
    pdf.add_page()
    pdf.section_title("M2.4 - Model & RAG Health")
    pdf.body("Two tabs: Model Exposure (which LLMs are under attack) and RAG & Retrieval (where in the pipeline control is lost).")
    pdf.box(
        "Use Case",
        "Elevated blocks on one model - adjust routing. RAG retriever blocks on one collection - investigate poisoned documents.",
        (239, 246, 255),
    )
    pdf.sub_title("Tab A - Model Exposure")
    pdf.table(
        ["Chart / Table", "What It Shows"],
        [
            ["Vulnerability Exposure by Model", "Composite 0-100% score: block + redact + latency stress."],
            ["Block Rate by Model", "Per-model hard-block percentage."],
            ["Active Models Table", "Provider, requests, block %, redact %, latency, exposure band."],
        ],
        [55, 135],
    )
    pdf.client_line("If Model A has 40% block rate and Model B has 2%, attackers are targeting Model A.")
    pdf.sub_title("Tab B - RAG & Retrieval (Query > Retriever > Ranker > Generator)")
    pdf.table(
        ["Chart", "What It Shows"],
        [
            ["Pipeline Stage Volume", "Stacked allowed / flagged / blocked per stage."],
            ["Stage Block Rate", "Block % at each gate."],
            ["Policy Escalation Mix (pie)", "Normal / Elevated / Strict policy tiers."],
            ["Collection Block Rate", "Vector collections ranked by block rate."],
            ["Stage Latency", "Average ms per stage."],
        ],
        [55, 135],
    )
    pdf.client_line("High block rate at Query = bad questions. High at Retriever = bad documents or ACL issues.")

    # M2.5
    pdf.add_page()
    pdf.section_title("M2.5 - MCP & Context Risk")
    pdf.body("Shows which AI agent tools and MCP servers trigger blocks or redactions when agents call external systems.")
    pdf.box(
        "Use Case",
        "Are agents sending dangerous SQL in tool arguments? Are tool responses leaking secrets into the model?",
        (239, 246, 255),
    )
    pdf.table(
        ["KPI / Chart", "What It Means"],
        [
            ["MCP Events", "Every tool call the firewall evaluated."],
            ["Blocked / Redacted Calls", "Stopped completely vs allowed after masking."],
            ["Policy Hit Rate", "% ending in block or redaction."],
            ["Tool Activity vs Policy Hits", "Blue = total calls; amber = policy hits per tool."],
            ["Inbound vs Outbound", "Arguments going in vs responses coming back."],
        ],
        [55, 135],
    )
    pdf.body("Inbound high = review argument policies. Outbound high = tighten what tool results may return.")

    # Incidents
    pdf.add_page()
    pdf.section_title("Incidents & Forensics")
    pdf.body(
        "A security incident is a formal case opened when alert rules decide enforcement is serious enough to track. "
        "Not the same as a single block - it is the SOC record until closed."
    )
    pdf.box(
        "Use Case",
        "Overnight blocks from one key create an incident. Analyst filters Active + High, reviews timeline, escalates, disables key in UEBA.",
        (239, 246, 255),
    )
    pdf.table(
        ["KPI", "What It Means"],
        [
            ["Active Queue", "Open + investigating + escalated cases."],
            ["Open", "Awaiting first review."],
            ["Escalated", "Promoted for senior review or IR."],
            ["Critical / High", "Highest severity active cases."],
            ["Resolved", "Closed - kept for audit."],
        ],
        [45, 145],
    )
    pdf.body("Incidents by Enforcement Lane (bar) - which lane (Chat, RAG, MCP, etc.) drives cases.")
    pdf.client_line("If most incidents are MCP-sourced, your agent tool surface is the hot problem - not chat.")
    pdf.sub_title("Incident Detail Page")
    pdf.body("Enforcement Timeline, Chain-of-Custody pipeline trace, Prompt Evidence, lane drill-down links.")
    pdf.client_line("Audit evidence - exactly where the firewall acted and why, stage by stage.")

    # Closing
    pdf.add_page()
    pdf.section_title("Closing Narrative for Clients")
    pdf.body(
        "Module 1 enforces policy on every request. Module 2 gives your security team the operational layer: "
        "Dashboard for posture, UEBA for suspicious keys, Threat Intel for published IOC patterns, "
        "Model/RAG for pipeline risk, MCP for agent tools, Incidents for formal case management."
    )
    pdf.body(
        "Every number comes from the same enforcement telemetry as production. A block in the demo app appears "
        "on the Dashboard within seconds, in UEBA under that key, and potentially as an incident."
    )
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(15, 118, 110)
    pdf.multi_cell(0, 7, "Module 1 protects. Module 2 lets you prove it, prioritize it, and respond to it.")
    pdf.ln(6)
    pdf.sub_title("Console Navigation")
    pdf.table(
        ["Screen", "Route"],
        [
            ["Dashboard", "/dashboard"],
            ["UEBA", "/ueba/api-keys"],
            ["Threat Intel", "/threat-intel"],
            ["Model Exposure", "/models/exposure"],
            ["RAG Health", "/models/exposure?tab=rag"],
            ["MCP Risk", "/mcp/risk"],
            ["Incidents", "/incidents"],
            ["Demo App", "http://127.0.0.1:8765"],
        ],
        [55, 135],
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    print(f"PDF written: {OUT}")


if __name__ == "__main__":
    build()
