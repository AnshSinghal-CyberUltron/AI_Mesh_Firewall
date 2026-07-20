import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
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
    """Receive form JSON, store in S3, persist to database."""
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

    return JsonResponse({"status": "ok", "s3_key": s3_key})
