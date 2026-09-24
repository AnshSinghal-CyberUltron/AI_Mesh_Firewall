"""B9 probe: can a DETECTOR hand a 403 back through the pipeline's typed return path?"""
from __future__ import annotations

from starlette.responses import Response

from gateway_v2.contracts.domain.findings import Finding
from gateway_v2.contracts.domain.plan import ExecutionPlan


class EvilAnnotated:
    detector_id = "evil.annotated"

    def detect(self, text: str, plan: ExecutionPlan) -> tuple[Finding, ...]:
        return Response(None, 403)  # returns an HTTP response where findings are declared


class EvilUnannotated:
    detector_id = "evil.unannotated"

    def detect(self, text, plan):  # no annotations at all
        return Response(None, 403)


class EvilRaises:
    detector_id = "evil.raises"

    def detect(self, text: str, plan: ExecutionPlan) -> tuple[Finding, ...]:
        raise PermissionError("blocked by detector")  # short-circuit by exception
