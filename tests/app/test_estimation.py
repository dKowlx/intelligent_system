"""Effort review routing and shared provider instructions, without API calls."""

import json

import httpx
import pytest

from studynavigator.config import Settings
from studynavigator.estimation import ESTIMATION_RULES, is_reestimate_request
from studynavigator.providers import ChatCompletionsProvider


@pytest.mark.parametrize("message", [
    "Переоцени время всех действий с учётом сложности и проверки",
    "Пересчитай длительность задач реалистично",
    "Оцени заново трудоёмкость оставшихся шагов",
    "Оцени реалистичное время на подготовку и решение",
])
def test_explicit_effort_review_requests_theory(message):
    assert is_reestimate_request(message)


@pytest.mark.parametrize("message", [
    "Поставь всем задачам 90 минут", "Не переоценивай время задач",
    "Не нужно пересчитывать длительность", "Сколько сейчас времени?",
    "Объясни теорию решения", "У меня нет времени", None,
])
def test_other_requests_do_not_route_to_effort_review(message):
    assert not is_reestimate_request(message)


@pytest.mark.asyncio
async def test_analysis_and_grounded_share_effort_method_and_keep_evidence_untrusted():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title":"fixture"}'},
            "finish_reason": "stop"}]})

    provider = ChatCompletionsProvider(Settings(mode="live",
        api_base_url="https://ai.api.cloud.yandex.net/v1",
        model="gpt://fixture-folder/yandexgpt-5.1/latest", api_key="synthetic-key"),
        transport=httpx.MockTransport(handler))
    await provider.analyze({"assignment_id": "fixture", "title": "Fixture", "sources": []})
    grounded = provider.grounded_payload([
        {"role": "user", "content": "Переоцени время всех действий"},
        {"role": "tool", "content": json.dumps({"actual_minutes": 125, "text": "untrusted evidence"})},
    ])
    for payload in [sent[0], grounded]:
        assert ESTIMATION_RULES in payload["messages"][0]["content"]
        assert "untrusted evidence" not in payload["messages"][0]["content"]
    assert "actual_minutes" in grounded["messages"][1]["content"]
    assert "estimate_basis" in grounded["messages"][0]["content"]
