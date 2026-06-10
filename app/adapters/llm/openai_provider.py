import hashlib
import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from app.adapters.llm.fake_provider import AuditModelRequest, AuditModelResponse
from app.core.config import get_settings
from app.schemas.audit import AuditOutput, Decision

logger = logging.getLogger(__name__)
settings = get_settings()


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def audit(self, request: AuditModelRequest) -> AuditModelResponse:
        input_payload = json.dumps(
            {"prompt": request.prompt, "pdf_text_len": len(request.pdf_text)},
            sort_keys=True,
        )
        input_hash = hashlib.sha256(input_payload.encode()).hexdigest()

        messages = [
            {"role": "system", "content": request.prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "order_snapshot": request.order_snapshot,
                        "pdf_text": request.pdf_text[:50000],
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("AI returned invalid JSON, attempting repair")
            parsed = _attempt_json_repair(content)

        try:
            output = AuditOutput(**parsed)
        except ValidationError:
            logger.warning("AI output failed schema validation, marking MANUAL_REVIEW")
            output = AuditOutput(
                decision=Decision.MANUAL_REVIEW,
                summary="AI output failed schema validation",
                rules=[],
                manual_review_reasons=["AI output schema validation failed"],
            )

        return AuditModelResponse(
            decision=output.decision.value,
            raw_output=parsed,
            normalized_output=output,
            model_provider="openai_compatible",
            model_name=self.model,
            input_hash=input_hash,
        )


def _attempt_json_repair(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"decision": "MANUAL_REVIEW", "summary": "Failed to parse AI response"}
