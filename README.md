# cli-agents

`cli-agents` вызывает внешние Claude и Codex через их штатные CLI. Основные
сценарии: независимый review, продолжительная дискуссия и multi-model
panel/debate. Gemini CLI остаётся необязательным совместимым провайдером.

Обёртка использует binary из текущего `$PATH`, provider default и стабильные
Claude aliases. Версии моделей и размеры контекста намеренно не зашиты в код:
они меняются независимо от плагина.

## Установка

Через личный marketplace:

```text
/plugin marketplace add SVS696/svs-skills
/plugin install cli-agents@svs
```

Provider CLI устанавливаются и авторизуются отдельно. Для основной работы нужны
`claude` и/или `codex`; `gemini` не обязателен.

```bash
command -v claude codex gemini
claude --version
codex --version
```

## Быстрый старт

```bash
cd "${CLAUDE_PLUGIN_ROOT}/skills/cli-agents"

python3 cli_caller.py --model claude-opus \
  --cwd /path/to/project \
  --systemprompt architect_reviewer \
  --prompt "Проведи независимый read-only review ADR"
```

В Codex каталог обычно доступен как `$HOME/.agents/skills/cli-agents`.

По умолчанию вызовы read-only. Для явно порученной реализации добавь
`--access workspace-write`. Режим `inherit` полностью доверяет локальному
provider config.

## Профили

- `codex`, `codex-review`, `codex-review-uncommitted`, `codex-json`;
- `claude`, `claude-opus`, `claude-sonnet`, `claude-haiku`;
- `gemini`, `gemini-json` — только необязательная совместимость.

Конкретный model ID передаётся отдельно:

```bash
python3 cli_caller.py --model codex \
  --provider-model gpt-5.6-sol \
  --prompt "Проверь решение"
```

Полный контракт, timeout semantics, правила сессий и council описаны в
[`skills/cli-agents/SKILL.md`](skills/cli-agents/SKILL.md).

## Council

Panel собирает независимые ответы параллельно и синтезирует вывод:

```bash
python3 agent_council.py --mode panel \
  --agents codex,claude-opus \
  --synthesize-with claude-opus \
  --topic "Range или hash partitioning?" \
  --cwd /path/to/project \
  --output /tmp/partition-panel.md
```

Debate передаёт каждому участнику полный транскрипт предыдущих ходов:

```bash
python3 agent_council.py --mode debate \
  --agents codex,claude-opus \
  --rounds 4 \
  --topic "SQS или Kafka?" \
  --cwd /path/to/project \
  --output /tmp/queue-debate.md
```

## Что изменилось в 3.0

- удалены устаревающие aliases конкретных Gemini/Codex model ID;
- `codex` использует текущий CLI/config default;
- Claude-профили используют aliases `opus`, `sonnet`, `haiku`;
- добавлен `--provider-model` для явного provider model ID;
- read-only стал default для всех внешних вызовов;
- Gemini больше не запускается с `--yolo`; deprecated `-p` заменён позиционным prompt;
- неизвестный system prompt теперь даёт ошибку вместо тихого fallback;
- исправлен Codex resume, который раньше получал неподдерживаемые flags;
- council больше не использует неоднозначный provider-global `--session last`;
- `--idle-timeout` и hard `--timeout` разведены в документации и CLI council;
- timeout завершает process group целиком;
- удалён заброшенный Ollama/n8n server layer с древними моделями и абсолютными путями.

Изменение намеренно несовместимо со старыми именами вроде `gemini-3-pro` и
`codex-gpt-5.5`. Используй базовый профиль и `--provider-model`.

## Разработка

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q skills/cli-agents
python3 skills/cli-agents/cli_caller.py --model codex --info
python3 skills/cli-agents/cli_caller.py --model claude-opus --info
```

Структура:

```text
skills/cli-agents/
├── SKILL.md
├── cli_caller.py
├── agent_council.py
└── systemprompts/
tests/
├── test_agent_council.py
├── test_cli_caller.py
└── test_contract.py
```

## Лицензия

MIT
