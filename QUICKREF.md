# cli-agents: quick reference

```bash
# Проверить собранную команду без вызова модели
python3 cli_caller.py --model codex --info
python3 cli_caller.py --model claude-opus --info

# Независимый read-only review (default)
python3 cli_caller.py --model claude-opus \
  --cwd /project \
  --systemprompt default_codereviewer \
  --prompt "Проведи review текущего diff"

# Явно выбрать provider model
python3 cli_caller.py --model codex \
  --provider-model gpt-5.6-sol \
  --prompt "Проверь решение"

# Разрешить реализацию без approval-промптов; только для проверяемого git diff
python3 cli_caller.py --model claude-opus \
  --access workspace-write \
  --cwd /project \
  --prompt "Реализуй утверждённое изменение"

# Продолжить конкретный диалог
python3 cli_caller.py --model claude-opus \
  --session <session-id> \
  --cwd /project \
  --prompt "Уточни второй риск"
```

Таймауты:

- `--idle-timeout` — допустимая тишина stdout и stderr;
- `--timeout` — жёсткая длительность всего вызова, default 1800 секунд.

System prompts: `default`, `default_planner`, `default_codereviewer`,
`codex_codereviewer`, `architect_reviewer`, `system_analyst`, `business_analyst`.

Gemini (`gemini`, `gemini-json`) необязателен и оставлен только для совместимости.

Все профили: `codex`, `codex-review`, `codex-review-uncommitted`, `codex-json`,
`claude`, `claude-opus`, `claude-sonnet`, `claude-haiku`, `gemini`, `gemini-json`.
