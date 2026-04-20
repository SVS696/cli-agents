# AI Models Direct - Quick Reference

## Базовый синтаксис

```bash
python cli_caller.py --model <model> --prompt "<prompt>" [опции]
```

## Модели

- `gemini` - 1M tokens
- `codex` - 128k tokens
- `qwen` - general
- `claude` - 200k tokens

## System Prompts

- `default` - общие задачи
- `default_planner` - планирование
- `default_codereviewer` - ревью кода
- `codex_codereviewer` - ревью для Codex

## Частые команды

```bash
# Ревью кода
python cli_caller.py --model gemini --prompt "Ревью auth.py" --systemprompt default_codereviewer --timeout 60
python cli_caller.py --model codex --prompt "Ревью auth.py" --systemprompt codex_codereviewer

# Планирование
python cli_caller.py --model qwen --prompt "План миграции БД" --systemprompt default_planner

# Большие файлы
python cli_caller.py --model gemini --prompt "Анализ large.py" --timeout 90

# Проверка
python cli_caller.py --model gemini --info
```

## Timeout

- По умолчанию: 30s
- Code review: 60s
- Большие файлы: 90s+
