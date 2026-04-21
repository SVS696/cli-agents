# cli-agents Skill

Direct CLI access to multiple AI models without MCP overhead. Binary paths resolved via `$PATH`, so always uses the newest installed version (fnm / homebrew / `~/.local/bin`).

## Supported Models

| Family | Context Window | Default Model | Best For | Command |
|--------|---------------|---------------|----------|---------|
| **Gemini** | 1,000,000 tokens | gemini-3-pro-preview → 2.5-pro fallback | Large context tasks, huge files | `call_gemini` |
| **Codex** | 400,000 tokens | gpt-5.4 (also gpt-5-codex, gpt-5.1-codex) | Code generation, refactoring, reasoning | `call_codex` |
| **Claude** | 200,000 tokens (Opus 1M beta) | Sonnet 4.6 / Opus 4.7 / Haiku 4.5 | General tasks | `call_claude` |

## Usage from Claude Code

### Basic Calls

```
Используй cli-agents skill для вызова Gemini с промптом:
"Проанализируй архитектуру проекта и предложи улучшения"
```

### With System Prompts

Available system prompts:
- **default** - General CLI agent behavior
- **planner** - Planning tasks (JSON output)
- **codereviewer** - Code review tasks
- **codex_codereviewer** - Codex-specific code review

```
Используй cli-agents skill для вызова Codex с системным промптом codereviewer
для ревью файла src/main.py
```

### With Timeout

```
Используй cli-agents skill для вызова Gemini с timeout 60 секунд
для обработки большого файла
```

## Direct CLI Usage

```bash
# Basic call
python cli_caller.py --model gemini --prompt "Your prompt here"

# With system prompt
python cli_caller.py --model codex --prompt "Review this code" --systemprompt codereviewer

# With timeout
python cli_caller.py --model gemini --prompt "Long task" --timeout 60

# Show model info
python cli_caller.py --model gemini --info
```

## System Prompts

### default.txt
External CLI agent with terminal access for general tasks.

### planner.txt
Planning agent that responds with JSON schemas for structured planning.

### codereviewer.txt
Code review agent that inspects files and reports findings by severity.

### codex_codereviewer.txt
Codex-specific code reviewer with repository access.

## Advantages

1. **Speed**: No MCP initialization overhead
2. **Direct Access**: Calls CLI directly with minimal wrapper
3. **Flexibility**: Easy to add new models or prompts
4. **Simple**: Pure Python, no complex dependencies

## Requirements

- Python 3.9+
- CLI tools installed:
  - `gemini` (Google Gemini CLI)
  - `codex` (OpenAI Codex CLI)
  - `claude` (Claude CLI)

Install missing CLIs:
```bash
# Check which are installed
which gemini codex claude

# Install as needed (example for Homebrew)
brew install gemini-cli codex claude-code
```

## Architecture

```
cli-agents/
├── skill.json              # Skill configuration for Claude Code
├── cli_caller.py           # Main Python script
├── systemprompts/          # System prompts
│   ├── default.txt
│   ├── planner.txt
│   ├── codereviewer.txt
│   └── codex_codereviewer.txt
└── README.md               # This file
```

## Example Workflows

### Large Context Analysis with Gemini
```
Используй cli-agents для вызова Gemini:
Проанализируй все файлы в директории src/ и создай документацию архитектуры
```

### Code Review with Codex
```
Используй cli-agents для вызова Codex с systemprompt=codex_codereviewer:
Проведи полный ревью кода в файлах src/auth/*.py
```

### Planning with Multiple Models
```
1. Используй cli-agents/call_gemini с systemprompt=planner для создания плана
2. Используй cli-agents/call_codex для проверки плана
```

### Multi-turn Discussion (`--session`)
Продолжение диалога в одной сессии без передачи истории вручную:
```bash
# Turn 1 — fresh session
python cli_caller.py --model claude --cwd /project \
  --prompt "Обсуждаем архитектуру auth. Что сейчас не так?"

# Turn 2+ — продолжить тот же thread
python cli_caller.py --model claude --session last --cwd /project \
  --prompt "А если middleware + JWT refresh через Redis?"
```
Значения `--session`: `new` (default), `last`/`latest`, конкретный id (UUID для codex, индекс для gemini). Работает для всех моделей кроме `codex-review*`.

### Multi-agent Council: Panel (параллельно)
Несколько моделей отвечают на один вопрос одновременно, синтезатор сводит их в consensus/divergence/recommendation. Быстро и дёшево — хорошо для разведки вариантов.
```bash
python agent_council.py --mode panel \
  --agents gemini-3-pro,codex,claude-opus \
  --synthesize-with claude-opus \
  --topic "Migrate 50M-row table to partitioning: range или hash?" \
  --output ~/discussions/partition.md \
  --timeout 120
```
На выходе — markdown с секциями `## Individual Answers` и `## Synthesis` (Consensus / Divergence / Recommendation).

**Когда использовать:** нужно быстро собрать мнения по варианту, без настоящего спора. Типичный вызов — 15-30 сек, каждый агент видит только вопрос и не читает ответы других.

### Multi-agent Council: Debate (последовательно)
Агенты по очереди читают общий `discussion.md` и добавляют свой ход. Каждый агент ведёт собственную `--session last`, чтобы свой thread был дёшев. Стоп по `CONCLUDED`, по двум коротким ходам подряд или по лимиту раундов.
```bash
python agent_council.py --mode debate \
  --agents codex,gemini-3-pro,claude-opus \
  --rounds 4 \
  --topic "Выбор между SQS и Kafka для нашей нагрузки" \
  --output ~/discussions/queue.md \
  --timeout 180
```
На выходе — markdown с журналом всех ходов по раундам, пригоден для коммита в PR / вставки в Linear.

**Когда использовать:** настоящий спор с контраргументами, где важно чтобы модель B видела аргументы модели A и оспорила или развила их. Дороже panel (каждый читает растущий файл), но глубже.

### Нативные code review через Codex
`codex review` — встроенная команда Codex CLI, не самодельный prompt:
```bash
# Ревью uncommitted changes в рабочем каталоге
python cli_caller.py --model codex-review-uncommitted --cwd /path/to/repo

# Ревью с кастомными инструкциями
python cli_caller.py --model codex-review --cwd /path/to/repo \
  --prompt "Фокус на security и race conditions"
```
Требует trusted git директории (первый запуск из неё — Codex запомнит).

## Troubleshooting

### CLI Not Found Error
```bash
# Install the missing CLI tool
brew install <cli-name>

# Or check PATH
echo $PATH
```

### Timeout Errors
```bash
# Increase timeout for long tasks
python cli_caller.py --model gemini --prompt "..." --timeout 120
```

### System Prompt Not Found
```bash
# Check available prompts
ls systemprompts/

# Add custom prompt
echo "Your custom prompt" > systemprompts/custom.txt
```

## Future Enhancements

- [ ] Add more models (DeepSeek, Llama, etc.)
- [ ] Support for streaming responses
- [ ] Token usage tracking
- [ ] Response caching
- [ ] Multi-model consensus

## Credits

Created by svs
Based on Zen MCP Server system prompts
Optimized for Claude Code skills architecture
