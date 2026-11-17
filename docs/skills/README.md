# Skills для разработки StudyNavigator

Проектные skills находятся в `.agents/skills/`. Общие правила работы — в
[AGENTS.md](../../AGENTS.md), назначение приложения и границы — в
[project-brief.md](../project-brief.md).

| Skill | Область применения |
|---|---|
| [study-build](../../.agents/skills/study-build/SKILL.md) | Функции приложения, интерфейс и исправления |
| [study-mcp](../../.agents/skills/study-mcp/SKILL.md) | MCP-сервер, клиент и контракты инструментов |
| [study-eval](../../.agents/skills/study-eval/SKILL.md) | Проверка ответов, источников и расчётов |
| [study-release](../../.agents/skills/study-release/SKILL.md) | Запуск, размещение и техническая документация |

Выбирайте skill по текущей задаче. Для небольшой правки достаточно инструкций
относящегося к ней навыка. Codex обнаруживает skills в `.agents/skills`;
при изменении их списка может потребоваться новая сессия.

Проверки структуры и вспомогательных программ не требуют API-ключей:

```sh
python3 tools/validate_skills.py
python3 -m unittest discover -s tests/skills -v
```

Эти команды не заменяют тесты приложения. Технические источники приведены
в [sources.md](sources.md).
