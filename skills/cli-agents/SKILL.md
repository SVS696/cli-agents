---
name: cli-agents
description: |
  Вызывает внешние Claude и Codex CLI для независимого мнения, read-only review,
  продолжительной дискуссии или multi-model panel/debate. Gemini CLI поддерживается
  только как необязательный совместимый провайдер. Use when: нужен второй агент, внешний
  code/spec/architecture review, second opinion, ask another model, проверка спорного
  решения несколькими моделями, продолжение диалога с внешней моделью или синтез
  независимых ответов. Не использовать
  для обычной локальной работы, которую текущий агент способен выполнить сам.
---

# cli-agents

Обёртка вызывает установленные `claude`, `codex` и, опционально, `gemini` через
их штатные CLI. Она не фиксирует рекламные названия моделей и размеры контекста:
эти возможности определяют текущая версия CLI, выбранная модель, аккаунт и локальный
конфиг провайдера.

По умолчанию каждый вызов read-only. Доступ на запись нужно включать явно.

## Где лежат скрипты

В Claude Code используй каталог плагина:

```bash
cd "${CLAUDE_PLUGIN_ROOT}/skills/cli-agents"
```

В Codex и других клиентах пользовательский скилл обычно доступен по ссылке:

```bash
cd "$HOME/.agents/skills/cli-agents"
```

Дальше примеры предполагают, что текущий каталог содержит `cli_caller.py`.

## Провайдеры и профили

| Профиль | Что запускается | Назначение |
|---|---|---|
| `codex` | текущий default Codex CLI/config | анализ, реализация, исследование |
| `codex-review` | нативный `codex review` | review с инструкцией |
| `codex-review-uncommitted` | `codex review --uncommitted` | review локального diff |
| `codex-json` | `codex exec --json` | JSONL-поток событий |
| `claude` | текущий default Claude CLI | общий внешний агент |
| `claude-opus` | стабильный alias `opus` | сложная логика и сквозной review |
| `claude-sonnet` | стабильный alias `sonnet` | быстрый общий review |
| `claude-haiku` | стабильный alias `haiku` | дешёвая локальная проверка |
| `gemini` | текущий default Gemini CLI | необязательная совместимость |
| `gemini-json` | Gemini JSON output | необязательная совместимость |

Gemini CLI не нужен для работы скилла и не рекомендуется только ради большого
контекста. Antigravity не имеет адаптера в этой обёртке.

Если нужен конкретный provider model ID, передай его отдельно:

```bash
python3 cli_caller.py --model codex \
  --provider-model gpt-5.6-sol \
  --prompt "Проверь решение"
```

Без `--provider-model` профиль `codex` уважает текущий Codex config, а Claude-профили
используют стабильные aliases. Так обновление провайдера не требует править скилл.

## Базовый вызов

```bash
python3 cli_caller.py \
  --model claude-opus \
  --stream \
  --cwd "/path/to/project" \
  --systemprompt architect_reviewer \
  --prompt "Проведи независимый review ADR. Верни findings с file:line."
```

Основные параметры:

- `--model` — профиль обёртки из таблицы выше;
- `--provider-model` — необязательный точный model ID провайдера;
- `--prompt` — задача внешнему агенту;
- `--cwd` — рабочий каталог и база для относительных путей;
- `--systemprompt` — имя файла без `.txt` из `systemprompts/`;
- `--access` — `read-only` (default), `workspace-write` или `inherit`;
- `--session` — `new`, `last`/`latest` или конкретный session ID;
- `--idle-timeout` — предел тишины одновременно в stdout и stderr;
- `--timeout` — жёсткий предел полного вызова, default 1800 секунд;
- `--stream` — показывать прогресс и текст ответа вживую без повторной печати финала;
- `--info` — показать собранную команду и найденный binary без вызова модели.

В streaming-режиме Claude использует `stream-json` с partial messages, а Codex
`exec` — JSONL events. Обёртка преобразует их в читаемый текст и короткие tool/status
события; сырые JSON и содержимое reasoning не печатаются. В интерактивном терминале
текст ответа идёт прямо в stdout. При pipe/redirect прогресс идёт в stderr, а чистый
финальный ответ — в stdout, поэтому `--stream > review.md` не загрязняет файл.
Native `codex review` и Gemini получают best-effort forwarding того, что реально
выдаёт их CLI, без JSON-разбора.

Неверное имя system prompt теперь завершает вызов ошибкой. Раньше обёртка молча
продолжала без заданной роли, из-за чего review выглядел успешным, хотя запускался
обычный prompt.

Перед `User Request` обёртка передаёт модели фактический execution context: выбранный
`--access` и рабочий каталог. Ролевые промпты не предполагают вымышленную среду или
безусловный доступ к репозиторию; они задают outcome, evidence contract, формат ответа
и условие остановки. Для Claude роль и границы передаются через настоящий
`--append-system-prompt`, а не маскируются под системный блок внутри user message.

## Доступ к файлам

`--access read-only` используется по умолчанию для внешнего мнения и review:

- Claude запускается с `--safe-mode` в `permission-mode=plan`, а `Edit`, `Write` и
  `NotebookEdit` явно запрещаются. Это отключает пользовательские hooks/plugins,
  auto-memory и запись служебного plan-файла, сохраняя штатную авторизацию;
- Codex получает read-only sandbox и `approval_policy=never`;
- Gemini остаётся в штатном approval mode без `--yolo`.

Для явно порученной реализации:

```bash
python3 cli_caller.py --model claude-opus \
  --access workspace-write \
  --cwd "/path/to/project" \
  --prompt "Реализуй утверждённое изменение и прогони тесты"
```

`inherit` не добавляет ограничений обёртки и полностью доверяет локальному конфигу
CLI. Не используй его для независимого review без отдельной причины.

`workspace-write` тоже работает без approval-промптов: Codex получает
`approval_policy=never`, Claude автоматически принимает правки, Gemini — edit tools.
Включай этот режим только для явно порученной реализации в git-каталоге, где можно
проверить и откатить diff.

## System prompts

Доступные роли:

- `default` — общая задача;
- `default_planner` — структурированный план;
- `default_codereviewer` — code review по severity;
- `codex_codereviewer` — review с учётом возможностей Codex;
- `architect_reviewer` — ADR, RFC, архитектура и эксплуатационные риски;
- `system_analyst` — требования, сценарии, API, данные и тестируемость;
- `business_analyst` — problem framing, ценность, метрики и альтернативы.

Проверить список напрямую:

```bash
find systemprompts -maxdepth 1 -name '*.txt' -print | sort
```

## Таймауты

У обёртки два разных предела:

1. `--idle-timeout` останавливает процесс, если оба потока вывода молчат. Без
   параметра используется профильное значение, обычно 90–360 секунд.
2. `--timeout` ограничивает всю длительность вызова. Default — 1800 секунд.

Если агент долго думает без вывода, увеличивай `--idle-timeout`. Если задача в целом
должна идти дольше 30 минут, увеличивай `--timeout`. Уменьшать `--timeout` до 60–120
секунд для «лечения таймаута» нельзя: это обрежет вызов ещё раньше.

При остановке обёртка завершает всю process group, поэтому дочерний CLI не остаётся
висеть после timeout.

## Продолжение сессии

```bash
# Новый диалог
python3 cli_caller.py --model claude-opus --cwd /project \
  --prompt "Разбери варианты миграции"

# Последняя сессия провайдера в этом cwd
python3 cli_caller.py --model claude-opus --session last --cwd /project \
  --prompt "Теперь сравни их по риску отката"
```

`last` — это понятие самого provider CLI, а не отдельный namespace `cli-agents`.
Если параллельно идут другие диалоги, используй конкретный session ID. Native
`codex-review*` resume не поддерживает.

Для длинной дискуссии заранее определи результат и критерий завершения. Остановись,
если агент дважды повторяет ответ, две попытки уточнения не дают прогресса или спор
крутится между теми же позициями. Каждые 5–7 ходов фиксируй принятые решения и
оставшиеся вопросы. После 30 ходов лучше переформулировать задачу или сменить источник.

## Council

`agent_council.py` поддерживает два режима. Все вызовы read-only по умолчанию.

Panel запускает независимые ответы параллельно, затем синтезирует общий вывод:

```bash
python3 agent_council.py --mode panel \
  --agents codex,claude-opus \
  --synthesize-with claude-opus \
  --topic "Range или hash partitioning для этой нагрузки?" \
  --cwd /project \
  --output /tmp/partition-panel.md
```

Debate передаёт каждому участнику полный markdown-транскрипт предыдущих ходов:

```bash
python3 agent_council.py --mode debate \
  --agents codex,claude-opus \
  --rounds 4 \
  --topic "SQS или Kafka для заданных требований?" \
  --cwd /project \
  --output /tmp/queue-debate.md
```

Каждый ход debate — новая provider-сессия. Это исключает случайное продолжение
чужого `last` thread при параллельной работе. Общий файл остаётся источником контекста.

Debate завершается по `CONCLUDED`, двум коротким ходам подряд, раунду без прогресса
или лимиту `--rounds`.

## Когда использовать

- нужен независимый внешний review кода, постановки или архитектуры;
- спорное решение полезно проверить двумя разными моделями;
- требуется продолжительная дискуссия с одним внешним агентом;
- нужен panel или debate с явным синтезом расхождений;
- инструкция проекта требует внешнего Claude или Codex.

## Когда не использовать

- текущий агент может сам выполнить обычную локальную задачу;
- пользователь не просил внешнее мнение, а проектный workflow его не требует;
- достаточно штатного субагента текущей среды;
- вызов используют только ради заявленного размера контекста;
- нужен Antigravity: у скилла нет такого провайдера.

## Диагностика

```bash
python3 cli_caller.py --model codex --info
python3 cli_caller.py --model claude-opus --info
codex --version
claude --version
```

Типовые ошибки:

- `CLI not found in PATH` — нужный provider CLI не установлен или не попал в PATH;
- `System prompt ... not found` — возьми точное имя из `systemprompts/`;
- `working directory does not exist` — исправь `--cwd`;
- `output idle timeout` — увеличь `--idle-timeout`;
- `exceeded hard timeout` — увеличь `--timeout`;
- native Codex review требует git-репозиторий и доверенный рабочий каталог.

Сначала проверяй `--info` и реальные `--version`. Не делай вывод о доступной модели
или контексте по тексту этого скилла: provider capabilities меняются независимо.

## Ещё примеры

Короткая шпаргалка лежит в `${CLAUDE_PLUGIN_ROOT}/QUICKREF.md`, расширенные рецепты —
в `${CLAUDE_PLUGIN_ROOT}/examples.md`. Из каталога скилла это `../../QUICKREF.md` и
`../../examples.md`.
