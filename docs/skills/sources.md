# Технические источники проектных skills

## Создание и размещение skills

- [Official OpenAI documentation: Build skills](https://learn.chatgpt.com/docs/build-skills):
  проектное размещение `.agents/skills`, точные descriptions, автоматический
  выбор по задаче, короткий SKILL.md и подробности по необходимости.
- [Official OpenAI documentation: Best practices](https://learn.chatgpt.com/docs/learn/best-practices):
  постоянные правила проекта в AGENTS.md, ограниченная задача у каждого skill,
  конкретные входы/выходы и проверка на реальных сценариях.
- [Agent Skills specification](https://agentskills.io/specification): переносимый
  формат каталога с SKILL.md. Проект использует минимальные метаданные name/description.

## Технические источники

- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/) и
  [MCP client](https://py.sdk.modelcontextprotocol.io/client/): проверка версии
  SDK перед выбором API, использование готовой реализации протокола.
- [MCP tools specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools):
  схемы инструментов, структурированные результаты, различение ошибок.
- [MCP testing](https://py.sdk.modelcontextprotocol.io/get-started/testing/) и
  [транспорты](https://py.sdk.modelcontextprotocol.io/client/transports/):
  настоящий client/server обмен плюс проверка выбранного транспорта.
- [FastAPI lifespan tests](https://fastapi.tiangolo.com/advanced/testing-events/):
  жизненный цикл ресурсов в приложении и тестах.
- [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/):
  осмысленная валидация входов без механического глобального strict mode.
- [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents):
  оценка исхода, изоляция прогонов, свидетельства, несколько trials для
  стохастических результатов, различение поведения инструмента и агента.

## Применение в проекте

Skills описывают небольшие законченные изменения, читающий MCP, происхождение
фактов, неизвестные даты как `null` и проверку арифметики плана кодом.
Offline, live и размещённый сервис проверяются отдельно.
