import httpx
import pytest

from studynavigator.theory import WikimediaSearch, build_task_theory, theory_query


def test_query_excludes_numbers_links_and_instruction_verbs():
    assert theory_query('Решить интегралы № 123 по https://private.example') == 'интегралы'
    assert theory_query('Изучить https://private.example a@private.example') == ''


@pytest.mark.asyncio
async def test_wikipedia_contract_and_untrusted_urls_ignored():
    def handler(request):
        assert request.url.host == 'ru.wikipedia.org'
        assert request.url.params['explaintext'] == '1'
        return httpx.Response(200, json={'query': {'pages': [{'title': 'Интеграл', 'extract': 'Общая теория.', 'fullurl': 'http://127.0.0.1'}]}})
    result = await WikimediaSearch(httpx.MockTransport(handler)).search('Интегралы')
    assert result[0]['scope'] == 'web_theory'
    assert result[0]['external_url'].startswith('https://ru.wikipedia.org/wiki/')
    assert result[0]['page'] is None


@pytest.mark.asyncio
async def test_unavailable_preserves_local_pages_and_mentions():
    async def fail(query):
        assert 'PRIVATE' not in query
        raise httpx.ConnectError('offline')
    result = await build_task_theory({'card': {'tasks': [{'id': 'one', 'title': 'Решить интегралы', 'source_ids': ['s1']}]},
        'sources': [{'source_id': 's1', 'title': 'Лист', 'excerpt': 'Учебник PRIVATE', 'page': 3, 'url': '/material/3'}]}, search=fail)
    assert result['status'] == 'unavailable'
    source = result['groups'][0]['resources'][0]
    assert source['page'] == 3
    assert 'reference_note' in source


@pytest.mark.asyncio
async def test_bounded_queries_and_deduplication():
    seen = []
    async def search(query):
        seen.append(query)
        return []
    await build_task_theory({}, tasks=[{'id': str(i), 'title': 'Интегралы'} for i in range(20)], search=search)
    assert seen == ['Интегралы']


@pytest.mark.asyncio
async def test_redirect_and_oversize_fail_closed():
    for response in [httpx.Response(302, headers={'location': 'http://127.0.0.1'}), httpx.Response(200, content=b'x' * 100001)]:
        async def search(query):
            return await WikimediaSearch(httpx.MockTransport(lambda request: response)).search(query)
        result = await build_task_theory({}, tasks=[{'id': 'one', 'title': 'Интегралы'}], search=search)
        assert result['status'] == 'unavailable'


@pytest.mark.asyncio
async def test_empty_russian_result_uses_english_fallback():
    hosts = []
    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(200, json={} if len(hosts) == 1 else {'query': {'pages': [{'title': 'Integral', 'extract': 'Mathematical theory.'}]}})
    result = await WikimediaSearch(httpx.MockTransport(handler)).search('Integral')
    assert hosts == ['ru.wikipedia.org', 'en.wikipedia.org']
    assert result[0]['provider'] == 'Wikipedia'


def test_local_overview_never_needs_network():
    from studynavigator.theory import local_task_theory
    result = local_task_theory({}, tasks=[{'id': 'a', 'title': 'Интегралы'}])
    assert result['groups'][0]['status'] == 'not_searched'
    assert result['sources'] == []


def test_conversational_query_keeps_subject_terms():
    assert theory_query('Расскажи про матричную экспоненту пожалуйста') == 'матричную экспоненту'
    assert theory_query('Объясни что такое линейные уравнения') == 'линейные уравнения'


@pytest.mark.asyncio
async def test_refresh_limits_concurrent_requests_and_total_queries():
    import asyncio
    active = maximum = calls = 0
    async def search(query):
        nonlocal active, maximum, calls
        calls += 1
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.01)
        active -= 1
        return []
    tasks = [{'id': str(i), 'title': title} for i, title in enumerate(
        ['Интегралы', 'Матрицы', 'Производные', 'Геометрия', 'Алгебра', 'Вероятности'])]
    result = await build_task_theory({}, tasks=tasks, search=search)
    assert calls == 5
    assert maximum == 3
    assert result['groups'][-1]['status'] == 'limited'


@pytest.mark.asyncio
async def test_total_timeout_keeps_completed_results_and_cancels_pending(monkeypatch):
    import asyncio
    from studynavigator import theory
    monkeypatch.setattr(theory, 'THEORY_TOTAL_TIMEOUT', .03)
    cancelled = []
    async def search(query):
        if query == 'Интегралы':
            return [{'source_id': 'web-test', 'title': 'Интеграл', 'scope': 'web_theory'}]
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(query)
    result = await asyncio.wait_for(build_task_theory({}, tasks=[
        {'id': 'a', 'title': 'Интегралы'}, {'id': 'b', 'title': 'Матрицы'}], search=search), timeout=.5)
    assert result['groups'][0]['status'] == 'ready'
    assert result['groups'][1]['status'] == 'unavailable'
    assert cancelled == ['Матрицы']
