import pytest


def test_invalid_dates_never_match():
    from app.skills.simple_text_consistency.evaluator import date_match

    assert date_match("2024-99-99", "2024-99-99") is False
    assert date_match("not-a-date", "not-a-date") is False


def test_higher_manifest_priority_breaks_equal_specificity(monkeypatch):
    from app.skills import registry

    manifests = [
        {
            "skill_id": "low",
            "version": "1",
            "enabled": True,
            "priority": 10,
            "match": {"scene_ids": ["7"]},
            "prompt_file": "low.md",
        },
        {
            "skill_id": "high",
            "version": "1",
            "enabled": True,
            "priority": 20,
            "match": {"scene_ids": ["7"]},
            "prompt_file": "high.md",
        },
    ]
    monkeypatch.setattr(registry, "load_manifests", lambda: manifests)
    monkeypatch.setattr(registry, "load_prompt", lambda name: (name, name))

    match = registry.match_skill({"scene_id": "7"})
    assert match is not None
    assert match.skill_id == "high"


@pytest.mark.asyncio
async def test_pdf_stream_stops_when_limit_exceeded(monkeypatch):
    from app.adapters.pdf import downloader

    chunks_read = 0

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/pdf"}

        async def aiter_bytes(self):
            nonlocal chunks_read
            for chunk in [b"%PDF-" + b"a" * 5, b"b" * 10, b"c" * 10]:
                chunks_read += 1
                yield chunk

    monkeypatch.setattr(downloader.settings, "PDF_MAX_SIZE_MB", 0)

    with pytest.raises(downloader.PdfDownloadError) as exc:
        await downloader._read_pdf_response_limited(FakeResponse(), max_size=12)

    assert exc.value.code == "PDF_TOO_LARGE"
    assert chunks_read == 2


@pytest.mark.asyncio
async def test_retryable_llm_http_error_is_raised(monkeypatch):
    import httpx

    from app.adapters.llm.fake_provider import AuditModelRequest
    from app.adapters.llm.openai_provider import (
        OpenAICompatibleProvider,
        RetryableLLMProviderError,
    )

    request = httpx.Request("POST", "https://llm.example/chat/completions")
    response = httpx.Response(429, request=request)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return response

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    provider = OpenAICompatibleProvider("https://llm.example", "secret", "model")

    with pytest.raises(RetryableLLMProviderError):
        await provider.audit(AuditModelRequest(
            prompt="audit",
            order_snapshot={"skc": "A"},
            pdf_text="long enough PDF text",
        ))

