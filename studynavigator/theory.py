"""General theory retrieval; external text never becomes a project requirement.

MediaWiki contract: https://www.mediawiki.org/wiki/API:Search
https://www.mediawiki.org/wiki/Extension:TextExtracts#API
Only short task topics leave the server; uploaded excerpts are never search input.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import re
import time
from urllib.parse import quote

import httpx

_HOSTS = ("ru.wikipedia.org", "en.wikipedia.org")
_STOP = set("изучить изучение подготовить прочитать решить решение выполнить выполнение проверить проверка оформить оформление составить задание задания задач задачи задача работа работы теория теории раздел глава параграф номер учебник учебнику все всех список списка части часть второй первая первый пункты пункт результат результаты подготовка материалов материалы данные способ критерии объем уточнить разобрать собрать сдаваемый по для из на и в с к объясни объяснить расскажи рассказать пожалуйста такое что про the a of to and".split())
THEORY_TOTAL_TIMEOUT = 16

_CACHE: dict[str, tuple[float, list[dict]]] = {}


def theory_query(title: str) -> str:
    """A small topic, never exercise numbers, URLs, email addresses or document text."""
    text = re.sub(r"https?://\S+|\S+@\S+|[§№].*", " ", title[:200], flags=re.I)
    words = re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё-]{2,}", text)
    return " ".join([word for word in words if word.lower() not in _STOP][:8])[:120].strip()


class WikimediaSearch:
    """Fixed HTTPS egress, no redirects, bounded decompressed response and cache."""
    def __init__(self, transport=None):
        self.transport = transport

    async def search(self, query: str) -> list[dict]:
        query = theory_query(query)
        if not query:
            return []
        cached = _CACHE.get(query)
        if self.transport is None and cached and time.monotonic() - cached[0] < 3600:
            return deepcopy(cached[1])
        async with asyncio.timeout(12):
            async with httpx.AsyncClient(timeout=5, follow_redirects=False,
                    transport=self.transport, trust_env=False,
                    headers={"User-Agent": "ProjectNavigator/0.1 (educational theory retrieval)"}) as client:
                for host in _HOSTS:
                    params = {"action": "query", "format": "json", "formatversion": "2",
                        "generator": "search", "gsrsearch": query, "gsrlimit": "2", "gsrnamespace": "0",
                        "prop": "extracts", "exintro": "1", "explaintext": "1", "exchars": "700", "exlimit": "2"}
                    async with client.stream("GET", f"https://{host}/w/api.php", params=params) as response:
                        response.raise_for_status()
                        payload = bytearray()
                        async for chunk in response.aiter_bytes():
                            payload.extend(chunk)
                            if len(payload) > 100_000:
                                raise ValueError("theory_response_too_large")
                    body = json.loads(payload)
                    resources = []
                    for page in sorted(body.get("query", {}).get("pages", []), key=lambda p: p.get("index", 999))[:2]:
                        title, excerpt = page.get("title"), page.get("extract")
                        if not isinstance(title, str) or not isinstance(excerpt, str) or not excerpt.strip():
                            continue
                        url = f"https://{host}/wiki/{quote(title.replace(' ', '_'), safe='')}"
                        resources.append({"source_id": "web-theory-" + hashlib.sha256(url.encode()).hexdigest()[:20],
                            "scope": "web_theory", "kind": "theory", "title": title[:200],
                            "excerpt": excerpt[:700], "summary": excerpt[:700], "url": url,
                            "external_url": url, "page": None, "provider": "Wikipedia",
                            "author": "Участники Википедии", "license": "CC BY-SA", "query": query})
                    if resources:
                        if self.transport is None:
                            if len(_CACHE) >= 256:
                                _CACHE.pop(next(iter(_CACHE)))
                            _CACHE[query] = (time.monotonic(), deepcopy(resources))
                        return resources
        return []


def _local_resources(task, sources):
    terms = [word.lower()[:6] for word in theory_query(task.get("title", "")).split() if len(word) > 3]
    explicit = set(task.get("source_ids", []))
    selected = []
    for source in sources:
        text = " ".join(str(source.get(key) or "") for key in ("title", "section", "excerpt")).lower()
        if source.get("source_id") in explicit or any(term in text for term in terms):
            item = dict(source)
            item["kind"] = "material"
            item.setdefault("scope", "project")
            if re.search(r"учебник|задачник|по\s+(?:книге|учебнику)", source.get("excerpt", ""), re.I):
                item["reference_note"] = "Упомянуто в задании. Наличие текста учебника и условия задач не подтверждено."
            selected.append(item)
    return selected[:3]


async def build_task_theory(assignment, *, tasks=None, search=None):
    """Caller supplies authorized assignment and optionally its saved plan tasks.

    Pure response builder: persistence/ownership belong to the authenticated route.
    General theory cannot establish a textbook exercise's exact condition.
    """
    if tasks is None:
        tasks = (assignment.get("card") or {}).get("tasks", [])
    search = search or WikimediaSearch().search
    tasks = tasks[:20]
    groups, all_sources, queried = [], {}, {}
    queries = list(dict.fromkeys(theory_query(task.get("title", "")) for task in tasks))
    queries = [query for query in queries if query]
    semaphore = asyncio.Semaphore(3)

    async def retrieve(query):
        async with semaphore:
            try:
                result = await search(query)
                queried[query] = (result, "ready" if result else "empty")
            except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
                queried[query] = ([], "unavailable")

    try:
        async with asyncio.timeout(THEORY_TOTAL_TIMEOUT):
            await asyncio.gather(*(retrieve(query) for query in queries[:5]))
    except TimeoutError:
        # gather cancels unfinished requests; completed queries remain useful.
        pass
    for query in queries[:5]:
        queried.setdefault(query, ([], "unavailable"))
    for query in queries[5:]:
        queried[query] = ([], "limited")
    for task in tasks:
        query = theory_query(task.get("title", ""))
        local = _local_resources(task, assignment.get("sources", []))
        web, status = queried.get(query, ([], "no_topic"))
        resources = local + web
        groups.append({"task_id": task.get("id"), "title": task.get("title", ""),
            "query": query, "status": status, "resources": resources})
        for source in resources:
            all_sources[source["source_id"]] = source
    return {"status": "ready" if any(g["status"] == "ready" for g in groups) else
            "unavailable" if any(g["status"] == "unavailable" for g in groups) else "empty",
        "groups": groups, "sources": list(all_sources.values()),
        "notice": "Общая теория из интернета не подтверждает условия и ответы конкретного учебника. Поиск получает только короткую тему действия; выдержки файлов не отправляются."}


def local_task_theory(assignment, *, tasks=None):
    """Local overview with no network request; caller may offer explicit refresh."""
    if tasks is None:
        tasks = (assignment.get("card") or {}).get("tasks", [])
    groups, sources = [], {}
    for task in tasks[:20]:
        resources = _local_resources(task, assignment.get("sources", []))
        groups.append({"task_id": task.get("id"), "title": task.get("title", ""),
            "query": theory_query(task.get("title", "")), "status": "not_searched", "resources": resources})
        sources.update({s["source_id"]: s for s in resources})
    return {"status": "not_searched", "groups": groups, "sources": list(sources.values()),
        "notice": "Интернет-поиск ещё не выполнен. Упоминание учебника не подтверждает наличие его текста."}
