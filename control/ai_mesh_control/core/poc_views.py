import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
import msal
import requests as http_requests
from botocore.exceptions import ClientError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from core.models import PocSubmission

def _resolve_questionnaire_html() -> Path:
    """Locate questionnaire HTML under docs/poc (local dev) or /app (Docker)."""
    core_dir = Path(__file__).resolve().parent
    repo_root = core_dir.parent.parent
    candidates = (
        repo_root / "docs" / "poc" / "CLIENT_POC_PREREQUISITE_QUESTIONNAIRE.html",
        repo_root / "CLIENT_POC_PREREQUISITE_QUESTIONNAIRE.html",
        core_dir.parent / "CLIENT_POC_PREREQUISITE_QUESTIONNAIRE.html",
        Path("/app/docs/poc/CLIENT_POC_PREREQUISITE_QUESTIONNAIRE.html"),
        Path("/app/CLIENT_POC_PREREQUISITE_QUESTIONNAIRE.html"),
    )
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]
_POC_RECIPIENTS = ["ansh@zeroshield.ai", "shivam@cyberultron.com", "spartan@cyberultron.com", "tarun@zeroshield.ai"]
_SENDER_EMAIL = "support@zeroshield.ai"
_TENANT_ID = os.environ.get("TENANT_ID", "")
_CLIENT_ID = os.environ.get("CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
_S3_BUCKET = os.environ.get("POC_S3_BUCKET", "")
_AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")


def poc_questionnaire_page(request):
    """Serve the POC questionnaire HTML page (public, no login)."""
    html_path = _resolve_questionnaire_html()
    if not html_path.is_file():
        return HttpResponse(
            f"<h1>POC questionnaire unavailable</h1><p>Missing file: {html_path}</p>",
            status=500,
            content_type="text/html; charset=utf-8",
        )
    content = html_path.read_text(encoding="utf-8")
    return HttpResponse(content, content_type="text/html; charset=utf-8")


@csrf_exempt
@require_http_methods(["POST"])
def poc_questionnaire_submit(request):
    """Receive form JSON, store in S3, send SES email."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    # L2: a valid JSON ARRAY/string/number parses fine but isn't a dict, so the
    # later data.get(...) raised AttributeError -> unhandled 500. Reject at the
    # boundary with 400 (mirrors the auth/views.py isinstance guard).
    if not isinstance(data, dict):
        return JsonResponse({"error": "Request body must be a JSON object."}, status=400)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:10]
    s3_key = f"poc-questionnaires/{timestamp}_{uid}.json"

    if _S3_BUCKET:
        try:
            s3 = boto3.client(
                "s3",
                region_name=_AWS_REGION,
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            )
            s3.put_object(
                Bucket=_S3_BUCKET,
                Key=s3_key,
                Body=json.dumps(data, indent=2),
                ContentType="application/json",
            )
            print(f"[POC] Stored in S3: s3://{_S3_BUCKET}/{s3_key}")
        except ClientError as exc:
            print(f"[POC] S3 upload failed: {exc}")
            s3_key = f"(S3 upload failed: {exc})"
    else:
        s3_key = "(S3 not configured — set POC_S3_BUCKET env var)"

    # ── persist to database so all submissions are visible in Django admin ──
    meta = data.get("meta", {})
    try:
        PocSubmission.objects.create(
            source="ai-mesh",
            company=meta.get("company", ""),
            contact=meta.get("contact", ""),
            poc_date=meta.get("date", ""),
            raw_data=data,
            s3_key=s3_key,
        )
    except Exception as exc:  # never block the response for a DB write failure
        print(f"[POC] DB save failed: {exc}")

    _send_graph_email(data, s3_key)
    return JsonResponse({"status": "ok", "s3_key": s3_key})


def _get_graph_token() -> str:
    authority = f"https://login.microsoftonline.com/{_TENANT_ID}"
    msal_app = msal.ConfidentialClientApplication(
        _CLIENT_ID, authority=authority, client_credential=_CLIENT_SECRET
    )
    token = msal_app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in token:
        raise RuntimeError(f"MSAL auth failed: {token.get('error_description', token)}")
    return token["access_token"]


def _send_graph_email(data: dict, s3_key: str) -> None:
    meta = data.get("meta", {})
    company = meta.get("company", "Unknown")
    contact = meta.get("contact", "Unknown")
    poc_date = meta.get("date", "—")
    submitted_at = meta.get("submitted_at", "unknown")

    ai = data.get("ai_models", {})
    providers = ai.get("providers", [])
    # HTML form uses provider_details; keep models[] fallback for older payloads
    model_entries = ai.get("provider_details") or ai.get("models") or []

    vdb = data.get("vector_dbs", {})
    dbs = vdb.get("databases", [])
    db_entries = vdb.get("db_deployments") or vdb.get("db_details") or []

    net = data.get("network", {})
    infra = data.get("infra", {})
    mcp_servers = data.get("mcp_servers", [])

    subject = f"[ZeroShield POC] New questionnaire — {company}"

    # ── helper ───────────────────────────────────────────
    def badge(text, color="#2352B8"):
        return (
            f'<span style="display:inline-block;padding:2px 10px;border-radius:20px;'
            f'background:{color}18;color:{color};font-size:11px;font-weight:600;'
            f'border:1px solid {color}44;margin:2px 3px 2px 0;">{text}</span>'
        )

    def row(label, value):
        return (
            f"<tr>"
            f'<td style="padding:10px 14px;font-size:12px;font-weight:600;color:#4B617C;'
            f'background:#F5F8FF;width:200px;border-bottom:1px solid #E8EEFA;white-space:nowrap;">{label}</td>'
            f'<td style="padding:10px 14px;font-size:13px;color:#0B1F4B;border-bottom:1px solid #E8EEFA;">{value}</td>'
            f"</tr>"
        )

    # ── provider badges ───────────────────────────────────
    provider_badges = "".join(badge(p) for p in providers) if providers else badge("None", "#9CA3AF")
    db_badges = "".join(badge(d) for d in dbs) if dbs else badge("None", "#9CA3AF")

    # ── model table rows (questionnaire: provider_details) ──
    model_rows = ""
    for entry in model_entries:
        if isinstance(entry, str):
            model_rows += (
                f"<tr>"
                f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;font-size:12px;">—</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;font-size:12px;font-family:monospace;">{entry}</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;">{badge("—", "#9CA3AF")}</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;font-size:11px;color:#7B93B8;"></td>'
                f"</tr>"
            )
            continue
        if not isinstance(entry, dict):
            continue
        account = entry.get("account_type") or entry.get("env", "—")
        model_rows += (
            f"<tr>"
            f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;font-size:12px;">{entry.get("provider", "")}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;font-size:12px;font-family:monospace;">'
            f'{entry.get("model") or entry.get("details", "—")}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;">{badge(account, "#10B981")}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;font-size:11px;color:#7B93B8;">'
            f'{entry.get("notes", entry.get("details", ""))}</td>'
            f"</tr>"
        )

    # ── DB table rows (questionnaire: db_deployments) ─────
    db_rows = ""
    for entry in db_entries:
        if isinstance(entry, str):
            db_rows += (
                f"<tr>"
                f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;font-size:12px;">{entry}</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;">{badge("—", "#9CA3AF")}</td>'
                f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;font-size:11px;color:#7B93B8;"></td>'
                f"</tr>"
            )
            continue
        if not isinstance(entry, dict):
            continue
        deployment = entry.get("deployment") or entry.get("hosting", "—")
        db_rows += (
            f"<tr>"
            f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;font-size:12px;">{entry.get("db", "")}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;">{badge(deployment, "#7C3AED")}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #F0F4FA;font-size:11px;color:#7B93B8;">{entry.get("notes", "")}</td>'
            f"</tr>"
        )

    # ── MCP section ───────────────────────────────────────
    mcp_html = ""
    for srv in mcp_servers:
        mcp_html += (
            f'<div style="border:1px solid #E8EEFA;border-radius:8px;padding:12px 14px;margin-bottom:10px;">'
            f'<div style="font-size:13px;font-weight:700;color:#0B1F4B;margin-bottom:6px;">⚙ {srv.get("name","Unnamed")}</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 16px;font-size:12px;">'
            f'<div><span style="color:#4B617C;font-weight:600;">Transport:</span> {srv.get("transport","—")}</div>'
            f'<div><span style="color:#4B617C;font-weight:600;">Auth Type:</span> {srv.get("auth_type","—")}</div>'
            f'<div style="grid-column:span 2"><span style="color:#4B617C;font-weight:600;">URL/Command:</span> '
            f'<code style="background:#F5F8FF;padding:1px 6px;border-radius:4px;font-size:11px;">{srv.get("url","—")}</code></div>'
            f'</div></div>'
        )
    if not mcp_html:
        mcp_html = '<div style="font-size:12px;color:#9CA3AF;padding:8px 0;">No MCP servers configured</div>'

    # ── table helper ─────────────────────────────────────
    def tbl(headers, body_rows, empty_msg="No data"):
        if not body_rows:
            return f'<div style="font-size:12px;color:#9CA3AF;padding:8px 0;">{empty_msg}</div>'
        ths = "".join(
            f'<th style="padding:7px 10px;background:#EEF4FF;font-size:11px;font-weight:600;'
            f'color:#2352B8;text-align:left;border-bottom:2px solid #C8D9F5;">{h}</th>'
            for h in headers
        )
        return (
            f'<table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:6px;">'
            f"<thead><tr>{ths}</tr></thead><tbody>{body_rows}</tbody></table>"
        )

    model_table = tbl(
        ["Provider", "Model / Details", "Account / Env", "Notes"],
        model_rows,
        "No provider details specified",
    )
    db_table = tbl(["Database", "Deployment", "Notes"], db_rows, "No databases selected")

    primary_model = ai.get("primary_model", "") or "—"
    fallback_model = ai.get("fallback_model", "") or "—"
    embedding_models = vdb.get("embedding_models", [])
    data_types = vdb.get("data_types", [])
    allowlist = net.get("ip_allowlist") or net.get("allowlist", "—")
    allowlist_notes = net.get("allowlist_notes", "")

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#F0F5FF;font-family:'Segoe UI',Arial,sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#F0F5FF;padding:32px 16px;">
<tr><td>
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:700px;margin:0 auto;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 8px 40px rgba(11,31,75,0.12);">

  <!-- HEADER -->
  <tr>
    <td style="background:linear-gradient(135deg,#0B1F4B 0%,#1A3A8A 60%,#1E4DB7 100%);padding:36px 40px;">
      <div style="display:inline-block;padding:4px 14px;background:rgba(255,255,255,0.10);border:1px solid rgba(255,255,255,0.20);border-radius:20px;font-size:11px;font-weight:600;letter-spacing:0.08em;color:rgba(255,255,255,0.85);text-transform:uppercase;margin-bottom:16px;">
        ● ZeroShield AI Mesh Firewall
      </div>
      <h1 style="color:#ffffff;font-size:24px;font-weight:800;margin:0 0 6px;letter-spacing:-0.02em;">
        New POC Questionnaire
      </h1>
      <p style="color:rgba(255,255,255,0.65);font-size:13px;margin:0;">
        A client has submitted their AI environment details for the upcoming POC session.
      </p>
    </td>
  </tr>

  <!-- SUMMARY META -->
  <tr>
    <td style="padding:28px 40px 0;">
      <h2 style="font-size:14px;font-weight:700;color:#0B1F4B;margin:0 0 14px;text-transform:uppercase;letter-spacing:0.06em;">
        Session Overview
      </h2>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:10px;overflow:hidden;border:1px solid #E8EEFA;">
        {row("Company / Org", f"<strong>{company}</strong>")}
        {row("Primary Contact", contact)}
        {row("POC Date", poc_date)}
        {row("Submitted At", submitted_at)}
        {row("S3 Record", f'<code style="font-size:11px;background:#F5F8FF;padding:2px 6px;border-radius:4px;">{s3_key}</code>')}
      </table>
    </td>
  </tr>

  <!-- SECTION 1: AI Models -->
  <tr>
    <td style="padding:28px 40px 0;">
      <h2 style="font-size:14px;font-weight:700;color:#0B1F4B;margin:0 0 6px;text-transform:uppercase;letter-spacing:0.06em;">
        1 — AI Models &amp; Providers
      </h2>
      <div style="margin-bottom:10px;">{provider_badges}</div>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:10px;overflow:hidden;border:1px solid #E8EEFA;margin-bottom:12px;">
        {row("Primary model", primary_model)}
        {row("Fallback model", fallback_model)}
      </table>
      {model_table}
    </td>
  </tr>

  <!-- SECTION 2: Vector DBs -->
  <tr>
    <td style="padding:28px 40px 0;">
      <h2 style="font-size:14px;font-weight:700;color:#0B1F4B;margin:0 0 6px;text-transform:uppercase;letter-spacing:0.06em;">
        2 — Vector Databases
      </h2>
      <div style="margin-bottom:10px;">{db_badges}</div>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:10px;overflow:hidden;border:1px solid #E8EEFA;margin-bottom:12px;">
        {row("Data types", "".join(badge(t) for t in data_types) if data_types else "—")}
        {row("Embedding models", "".join(badge(m) for m in embedding_models) if embedding_models else "—")}
      </table>
      {db_table}
    </td>
  </tr>

  <!-- SECTION 3: MCP Servers -->
  <tr>
    <td style="padding:28px 40px 0;">
      <h2 style="font-size:14px;font-weight:700;color:#0B1F4B;margin:0 0 10px;text-transform:uppercase;letter-spacing:0.06em;">
        3 — MCP Servers ({len(mcp_servers)} configured)
      </h2>
      {mcp_html}
    </td>
  </tr>

  <!-- SECTION 4: Network & Infra -->
  <tr>
    <td style="padding:28px 40px 0;">
      <h2 style="font-size:14px;font-weight:700;color:#0B1F4B;margin:0 0 14px;text-transform:uppercase;letter-spacing:0.06em;">
        4 — Network &amp; Infrastructure
      </h2>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:10px;overflow:hidden;border:1px solid #E8EEFA;">
        {row("VPN / Tunnel", net.get("vpn", "—") + (f' &mdash; {net.get("vpn_notes","")}' if net.get("vpn_notes") else ""))}
        {row("Outbound Access", net.get("gateway", "—") + (f' &mdash; {net.get("gateway_notes","")}' if net.get("gateway_notes") else ""))}
        {row("IP Allowlisting", allowlist + (f' &mdash; {allowlist_notes}' if allowlist_notes else ""))}
        {row("Deployment Target", infra.get("deployment", "—"))}
        {row("Auth Stack", infra.get("auth", "—"))}
        {row("Observability", infra.get("observability", "—"))}
      </table>
    </td>
  </tr>

  <!-- FOOTER -->
  <tr>
    <td style="padding:28px 40px 32px;margin-top:10px;">
      <div style="border-top:1px solid #EEF2FA;padding-top:20px;text-align:center;">
        <div style="font-size:12px;color:#7B93B8;">
          This submission was automatically captured by
          <strong style="color:#2352B8;">ZeroShield AI Mesh Firewall</strong>.
          Full JSON is stored at <code style="font-size:11px;">{s3_key}</code>.
        </div>
      </div>
    </td>
  </tr>

</table>
</td></tr>
</table>

</body>
</html>"""

    try:
        access_token = _get_graph_token()
        to_list = [{"emailAddress": {"address": r}} for r in _POC_RECIPIENTS]
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": to_list,
            }
        }
        url = f"https://graph.microsoft.com/v1.0/users/{_SENDER_EMAIL}/sendMail"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        resp = http_requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 202:
            print(f"[POC] Email sent from {_SENDER_EMAIL} to {_POC_RECIPIENTS}")
        else:
            print(f"[POC] Graph API error {resp.status_code}: {resp.text}")
    except Exception as exc:
        print(f"[POC] Email send error: {exc}")
