# cli-agents examples

## Review постановки

```bash
python3 cli_caller.py --model claude-opus \
  --cwd /project \
  --systemprompt system_analyst \
  --prompt "Проверь связность требований, API, данных и AC. Ничего не меняй."
```

## Native Codex review

```bash
python3 cli_caller.py --model codex-review-uncommitted \
  --cwd /project

python3 cli_caller.py --model codex-review \
  --cwd /project \
  --prompt "Фокус на security, race conditions и потере данных"
```

## Конкретная модель провайдера

```bash
python3 cli_caller.py --model codex \
  --provider-model gpt-5.6-sol \
  --cwd /project \
  --prompt "Проверь архитектурное решение"
```

## Длинный вызов

```bash
python3 cli_caller.py --model claude-opus \
  --stream \
  --idle-timeout 900 \
  --timeout 3600 \
  --cwd /project \
  --prompt "Проведи сквозной review корпуса документов"
```

## Panel

```bash
python3 agent_council.py --mode panel \
  --agents codex,claude-opus \
  --synthesize-with claude-opus \
  --topic-file /tmp/question.md \
  --cwd /project \
  --output /tmp/panel.md
```

## Debate

```bash
python3 agent_council.py --mode debate \
  --agents codex,claude-opus \
  --rounds 4 \
  --topic "Выбрать стратегию миграции" \
  --cwd /project \
  --output /tmp/debate.md
```
