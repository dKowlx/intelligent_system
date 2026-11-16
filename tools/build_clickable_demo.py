"""Package the current frontend with authored examples and a network-free adapter.

Run: .venv/bin/python tools/build_clickable_demo.py
Only the explicit public source files below are read. No .env, uploads or DB.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studynavigator.planner import build_adaptive_plan
from studynavigator.analytics import build_analytics


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def field(text: str) -> dict:
    return {"text": text, "origin": "user", "source_ids": []}


def launch_example() -> tuple[dict, str, list[dict]]:
    requirements = [
        {"id": "req-scenarios", **field("Описать три пользовательских сценария и признаки успешного решения.")},
        {"id": "req-prototype", **field("Подготовить кликабельный прототип основного пути новой функции.")},
        {"id": "req-handoff", **field("Передать проверенную концепцию: сценарии, прототип и открытые вопросы.")},
    ]
    rows = [
        ("brief", "Согласовать результат и критерии", 30, [], ["req-scenarios", "req-handoff"],
         "Короткий бриф с тремя сценариями и границами решения", "Результат и критерии зафиксированы в брифе", "20 минут обсуждение + 10 минут фиксация"),
        ("research", "Уточнить три пользовательских сценария", 60, ["brief"], ["req-scenarios"],
         "Три сценария: ситуация, действие пользователя и ожидаемый результат", "Для каждого сценария указан проверяемый результат", "3 сценария × 15 минут + 15 минут сверка"),
        ("prototype", "Собрать кликабельный прототип", 90, ["brief"], ["req-prototype"],
         "Прототип основного пути с входом, действием и результатом", "Все переходы основного пути доступны по нажатию", "3 экрана × 20 минут + 30 минут связи и состояния"),
        ("assemble", "Объединить сценарии и прототип", 45, ["research", "prototype"], ["req-handoff"],
         "Страница передачи с прототипом, сценариями и открытыми вопросами", "Сценарии связаны с экранами; вопросы вынесены в список", "30 минут сборка + 15 минут сверка соответствий"),
        ("verify", "Проверить три сценария и передать результат", 30, ["assemble"], ["req-scenarios", "req-prototype", "req-handoff"],
         "Заполненный список проверок и ссылка на итоговую концепцию", "Три сценария пройдены; найденные ограничения описаны", "3 сценария × 7 минут + 9 минут итоговые замечания"),
    ]
    tasks = [{"id": ident, "title": title, "minutes": minutes, "depends_on": deps,
              "requirement_ids": refs, "expected_result": result, "done_when": done,
              "estimate_basis": basis, "estimate_source": "estimate", "priority": "high" if ident == "brief" else "medium", "priority_reason": "Открывает зависимые действия" if ident == "brief" else "Основной объём проекта", "source_ids": []}
             for ident, title, minutes, deps, refs, result, done, basis in rows]
    substeps = {
        "brief": [(10, "Описать проблему и итоговый результат"), (10, "Выделить три сценария и границы концепции"), (10, "Зафиксировать критерии проверки и открытые вопросы")],
        "research": [(15, "Описать ситуацию и цель пользователя в первом сценарии"), (15, "Расписать действия и результат второго сценария"), (15, "Расписать действия и результат третьего сценария"), (15, "Сверить три сценария с критериями брифа")],
        "prototype": [(15, "Нарисовать входной экран и точку запуска функции"), (15, "Собрать экран основного действия"), (15, "Показать экран результата и следующий шаг"), (15, "Соединить экраны кликабельными переходами"), (15, "Добавить пустое состояние и ошибку основного действия"), (15, "Пройти основной путь и исправить разрывы")],
        "assemble": [(10, "Создать страницу передачи и добавить ссылку на прототип"), (10, "Связать первый сценарий с нужными экранами"), (10, "Связать второй и третий сценарии с экранами"), (15, "Сверить соответствия и вынести открытые вопросы")],
        "verify": [(7, "Пройти первый сценарий и записать замечания"), (7, "Пройти второй сценарий и записать замечания"), (7, "Пройти третий сценарий и записать замечания"), (9, "Описать ограничения и проверить доступность итоговой ссылки")],
    }
    checks = {
        "brief": ["Проблема и итог записаны", "Есть три сценария и явные границы концепции", "Критерии и открытые вопросы зафиксированы"],
        "research": ["Первый сценарий содержит ситуацию и цель пользователя", "Во втором сценарии указаны действия и результат", "В третьем сценарии указаны действия и результат", "Для всех сценариев указан проверяемый результат"],
        "prototype": ["Входной экран и запуск функции доступны", "Экран основного действия собран", "Экран результата содержит следующий шаг", "Все переходы основного пути работают", "Пустое состояние и ошибка доступны в прототипе", "Основной путь пройден без разрывов"],
        "assemble": ["Страница передачи создана; ссылка на прототип открывается", "Первый сценарий связан с экранами", "Второй и третий сценарии связаны с экранами", "Соответствия проверены; вопросы вынесены в список"],
        "verify": ["Первый сценарий пройден; замечания записаны", "Второй сценарий пройден; замечания записаны", "Третий сценарий пройден; замечания записаны", "Ограничения описаны; итоговая ссылка доступна"],
    }
    for task in tasks:
        task["substeps"] = [{"id": f"{task['id']}-{i}", "title": title, "minutes": minutes,
                             "done_when": checks[task["id"]][i - 1]}
                            for i, (minutes, title) in enumerate(substeps[task["id"]], 1)]
        assert sum(step["minutes"] for step in task["substeps"]) == task["minutes"]
    card = {"title": "Запуск новой функции", "goal": field("Проверить концепцию новой функции до разработки"),
            "deliverable": field("Проверенная концепция: три сценария, кликабельный прототип и страница передачи"),
            "requirements": requirements, "criteria": [], "tasks": tasks, "questions": [],
            "deadline": {"date": None, "origin": "unknown", "source_ids": []}}
    brief = "Авторский пример рабочего проекта. Нужно подготовить концепцию новой функции: три пользовательских сценария, кликабельный прототип и страницу передачи. Разработка функции в этот объём не входит. Дата сдачи не задана. Сценарии и прототип можно готовить независимо после согласования брифа."
    return card, brief, [{"day": i + 1, "minutes": m} for i, m in enumerate([90, 0, 90, 90, 90])]


def seed() -> dict:
    reference = json.loads(read("evals/app/project-reference.json"))
    card, brief, availability = launch_example()
    data = {"assignments": [], "plans": {}, "history": {}}
    for ident, card, brief, availability, reserve in [
        ("demo-launch", card, brief, availability, 10),
        ("demo-research", reference["card"], reference["brief"], reference["availability"], 0),
    ]:
        card = copy.deepcopy(card)
        source_id = ident + "-brief"
        text = brief + "\n\nТребования:\n" + "\n".join("• " + r["text"] for r in card["requirements"])
        source = {"source_id": source_id, "title": "Бриф: " + card["title"], "kind": "text",
                  "section": "Авторский пример", "excerpt": text, "url": "#demo-source-" + source_id}
        data["assignments"].append({
            "assignment_id": ident, "title": card["title"], "description": brief, "is_uploaded": True,
            "input_kind": "text", "status": "ready", "card_revision": 1, "card": card,
            "sources": [source], "documents": [{"kind": "text", "title": source["title"], "character_count": len(text), "url": source["url"]}],
            "requirements": card["requirements"], "criteria": [], "due_date": None, "due_at": None,
        })
        tasks = copy.deepcopy(card["tasks"])
        if ident == "demo-launch":
            tasks[0].update(completed=True, actual_minutes=40)
        result = build_adaptive_plan(tasks, availability, reserve_percent=reserve, card=card)
        result["assignment_id"] = ident
        initial = [{**t, "completed": False, "actual_minutes": None} for t in tasks]
        records = [{"revision": 0, "recorded_at": "2026-11-12T10:00:00Z", "tasks": initial},
                   {"revision": 1, "recorded_at": "2026-11-13T10:00:00Z", "tasks": tasks}]
        data["history"][ident] = build_analytics(tasks, records)["history"]
        data["plans"][ident] = {"revision": 1, "card_revision": 1, "stale": False,
                               "input": {"tasks": result["tasks"], "availability": availability,
                                         "start_date": None, "reserve_percent": reserve}, "result": result}
    return data


def replace_function(code: str, first: str, following: str, replacement: str) -> str:
    start, end = code.index(first), code.index(following, code.index(first))
    return code[:start] + replacement.strip() + "\n\n" + code[end:]


def script(code: str) -> str:
    return "<script>\n" + re.sub(r"</script", r"<\\/script", code, flags=re.I) + "\n</script>"



def main() -> None:
    html = read("web/index.html")
    html = re.sub(r"\s*<script\b[^>]*\bsrc=[^>]+></script>", "", html)
    html = re.sub(r'\s*<link rel="stylesheet"[^>]+>', "", html)
    policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="' + policy + '">')
    html = html.replace("Доступ к проектам сохранён в этом браузере.", "Ваши проекты и текущий прогресс.")
    html = html.replace('<button id="regroup-actions" class="button button-secondary" type="button">Собрать 5 действий</button>', '<button id="regroup-actions" type="button" hidden>Собрать 5 действий</button>')
    html = html.replace("</head>", script(read("web/theme.js")) + "\n<style>\n" + read("web/styles.css") + "\n" + read("web/route-map.css") + read("web/workspace.css") + "\n</style>\n</head>")
    app = replace_function(read("web/app.js"), "async function requestJSON(", "function sourceURL(", """
async function requestJSON(path, options = {}, timeout = 50000) {
  try { return await NavigatorDemo.request(path, options); }
  catch (error) { throw new ApiError(error.message || 'Не удалось выполнить действие в демо.', error.code || 'demo_error', null, error.status); }
}
""")
    app = replace_function(app, "function showMode(", "function materialOnly(", """
function showMode(status, assignment = state.detail) {
  state.status = { ...status, mode: 'offline' };
  $('mode-badge').hidden = true;
  $('mode-notice').hidden = true;
}
""")
    app = app.replace("План и прогресс сохранены на сервере для текущего браузера · версия ", "План обновлён · версия ")
    app = app.replace("Сохранённый на сервере план и прогресс восстановлены", "План загружен")
    data = json.dumps(seed(), ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    scripts = ["window.NAVIGATOR_DEMO_SEED = " + data + ";", read("tools/demo-runtime.js"),
               read("web/route-map.js"), read("web/text-format.js"), app, read("web/workspace.js")]
    html = html.replace("</body>", "\n".join(script(code) for code in scripts) + "\n</body>")
    assert not re.search(r'<(?:script|img)\b[^>]*\bsrc\s*=|<link\b[^>]*\bhref\s*=', html, re.I)
    output = ROOT / "var/share"
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / "project-navigator-demo.html"
    artifact.write_text(html, encoding="utf-8")
    guide = """PROJECT NAVIGATOR — ИНТЕРАКТИВНОЕ ДЕМО

1. Распакуйте ZIP в любую папку.
2. Откройте project-navigator-demo.html в современном Chrome, Edge, Firefox или Safari.
Установка, сервер, интернет и API-ключи не нужны.

Что попробовать:
• Переключите два проекта в боковой панели.
• Нажмите узел схемы: посмотрите результат, критерий готовности и зависимости.
• Откройте действия и план. Измените время, занятость по дням, резерв или прогресс; пересчитайте план.
• Во втором примере 470 минут работы при 450 доступных: виден дефицит 20 минут.
• Переключите светлую и тёмную темы.
• Откройте материал или создайте свой проект текстом и заполните карточку вручную.
• В ассистенте можно увидеть демонстрационный ответ по карточке.

Это демо интерфейса на авторских примерах, не подключённая AI-модель.
Загрузка PDF, AI-анализ и серверное хранение в этом файле недоступны.
Чат имитирует ответы по карточке. Команда «отметь Собрать кликабельный прототип выполненным»
создаёт предложение изменения; оно выполняется только кнопкой применения.
Аналитика исходных проектов использует авторскую историю примеров.
Изменения живут в памяти: перезагрузка возвращает исходные примеры.
Настройка темы может сохраняться браузером. ZIP не содержит ключей, базы данных или пользовательских файлов.
"""
    archive = output / "project-navigator-demo.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.write(artifact, artifact.name)
        package.writestr("START_HERE.txt", guide)
    print(json.dumps({"html": str(artifact), "zip": str(archive), "html_bytes": artifact.stat().st_size,
                      "zip_bytes": archive.stat().st_size, "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
