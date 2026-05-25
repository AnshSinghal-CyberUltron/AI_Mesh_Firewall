from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4


@dataclass
class JobEnvelope:
    job_type: str
    request_id: str
    org_id: int | None
    payload: dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload_hash: str = ""
    idempotency_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload_json = str(self.payload).encode("utf-8")
        payload_hash = self.payload_hash or sha256(payload_json).hexdigest()
        idempotency_key = self.idempotency_key or f"{self.job_type}:{self.request_id}:{payload_hash[:12]}"
        data = asdict(self)
        data["payload_hash"] = payload_hash
        data["idempotency_key"] = idempotency_key
        if not data["request_id"]:
            data["request_id"] = f"job-{uuid4().hex[:12]}"
        return data
