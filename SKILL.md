---
name: cli-agents
description: Direct CLI access to AI models (Gemini 1M, Codex 400k, Qwen 256k, Claude 200k) for code review, large file processing, and multi-model consensus. Use when multiple model opinions needed or processing files >100k tokens.
version: 2.1.0
---

# AI Models Direct

Provides direct CLI access to four AI model families without MCP server overhead. Optimized for code review workflows, large context processing, and multi-model consensus scenarios. The wrapper resolves CLIs via `$PATH`, so it always uses the latest installed version (fnm/homebrew/~/.local/bin).

## Available Models

| Family | Default Model | Context | Best For |
|--------|---------------|---------|----------|
| Gemini | gemini-3-pro-preview → 2.5-pro fallback | 1M tokens | Large files, full codebase analysis |
| Codex | gpt-5.4 (also gpt-5-codex, gpt-5.1-codex) | 400k tokens | Code generation, refactoring, reasoning |
| Qwen | Qwen3-Coder | 256k tokens | General code tasks |
| Claude | Sonnet 4.6 / Opus 4.7 / Haiku 4.5 | 200k tokens (Opus 1M beta) | General purpose |

## Usage

```bash
python cli_caller.py --model <model> --prompt "<prompt>" [options]
```

**Важно**: Команды выполняются из директории скилла или с полным путем к `cli_caller.py`

### Options

- `--model`: one of
  - Gemini: `gemini`, `gemini-3-pro`, `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`
  - Codex: `codex`, `codex-gpt-5-codex`, `codex-gpt-5.1-codex`
  - Qwen: `qwen`
  - Claude: `claude`, `claude-sonnet`, `claude-opus`, `claude-haiku`
- `--prompt`: Prompt text
- `--systemprompt`: `default`, `default_planner`, `default_codereviewer`, `codex_codereviewer`
- `--timeout`: Seconds (default varies per model, обычно 60-180)
- `--cwd`: Working directory (даёт модели доступ к файлам в указанной директории)
- `--session`: Resume for multi-turn discussion — `new` (default), `last`/`latest`, или конкретный id
- `--info`: Show model info

## Multi-turn Discussion (`--session`)

Скилл умеет продолжать диалог с той же сессией агента — context сохраняется между вызовами, не нужно передавать историю вручную.

| CLI | Механизм | Формат id |
|-----|----------|-----------|
| `claude` | `--continue` / `--resume <id>` | session id из Claude |
| `codex` | `codex exec resume --last` / `resume <uuid>` | UUID из header'а `session id:` |
| `gemini` | `-r latest` / `-r <index>` | числовой индекс сессии |

**Пример дискуссии:**
```bash
# Turn 1: fresh session
python cli_caller.py --model claude --prompt "Давай обсудим архитектуру auth" --cwd /project

# Turn 2+: продолжение последней сессии в том же cwd
python cli_caller.py --model claude --session last --prompt "Что если вынести в отдельный middleware?" --cwd /project
```

**⚠️ Правила ведения дискуссии (не зацикливаться):**

1. **Цель → критерий завершения.** Перед диалогом определи, что считается ответом: конкретное решение, список вариантов, согласие по спорному пункту.
2. **Лимит — по прогрессу, не по числу ходов.** Для серьёзной темы 10-20+ ходов нормально. Стоп-сигналы: агент повторяет прошлый ответ 2 раза подряд, после двух попыток уточнения всё ещё отвечает не по делу, или крутится между 2 позициями без новых аргументов.
3. **Follow-up конкретный.** Каждый следующий prompt должен двигать вперёд: уточнение, контраргумент, выбор из вариантов, запрос пруфов. Не повторять тот же вопрос другими словами.
4. **Чек-поинты каждые 5-7 ходов.** Резюмируй, что уже решено и что осталось. Если прогресса 0 за 2 чек-поинта подряд — меняй подход (другая модель, другая постановка, другой источник данных).
5. **Fork при развилке.** Если нужно исследовать альтернативу — не засоряй основную сессию, начни fresh session (`--session new` по умолчанию).
6. **Жёсткий потолок — 30 ходов.** За его пределами почти всегда проблема не в модели, а в постановке задачи или данных. Остановись и переформулируй.

**Не поддерживается:** `codex-review` и `codex-review-uncommitted` — это разовые review, resume не применяется.

## Multi-agent Council (`agent_council.py`)

Два режима совместной работы нескольких моделей:

### Panel (параллельно)
Все агенты отвечают на один вопрос одновременно → синтезатор собирает consensus / divergence / recommendation.

```bash
python agent_council.py --mode panel \
  --agents gemini-3-pro,codex,claude-opus \
  --synthesize-with claude-opus \
  --topic "Migrate 50M-row table to partitioning: by range or hash?" \
  --output ~/discussions/partition.md --timeout 120
```

Быстро и дёшево — каждый агент видит только вопрос, не других. Хорошо для разведки вариантов, плохо для настоящего спора.

### Debate (последовательно)
Общий `discussion.md` — каждый агент читает файл, добавляет ход. Повторяется `--rounds` раз. Собственная `--session last` у каждого агента → свой thread дешёвый.

```bash
python agent_council.py --mode debate \
  --agents codex,gemini-3-pro,claude-opus \
  --rounds 4 \
  --topic "Выбор между SQS и Kafka для наших объёмов" \
  --output ~/discussions/queue.md --timeout 180
```

**Стоп-сигналы в debate:**
- Агент отвечает `CONCLUDED` (явный отказ от хода)
- Ответ короче `--min-len` (по умолчанию 40 символов) 2 раза подряд
- Раунд прошёл без прогресса — никто не добавил контент
- Достигнут `--rounds`

Весь транскрипт — markdown, пригоден для коммита в PR / приклеивания в Linear.

## File Access

| Model | Доступ к файлам | Требуется --cwd |
|-------|-----------------|-----------------|
| Gemini | Ограничен рабочей директорией | ✅ Да |
| Codex | Полный доступ к FS | ❌ Нет |
| Qwen | Ограничен рабочей директорией | ✅ Да |
| Claude | Зависит от настроек | Опционально |

**Пример с --cwd для доступа к проекту:**
```bash
python cli_caller.py --model gemini \
  --cwd "/path/to/project" \
  --prompt "Прочитай файл src/main.py и сделай ревью" \
  --timeout 60
```

## System Prompts

Имена файлов в `systemprompts/`:
- `default.txt` - CLI agent for general tasks
- `default_planner.txt` - Structured planning (JSON output)
- `default_codereviewer.txt` - Code review (Critical/High/Medium/Low)
- `codex_codereviewer.txt` - Codex-optimized code review

## When to Use

### Code Review Workflows

Invoke when task requires:
- Multiple expert opinions on architecture decisions
- Security review from different model perspectives
- Consensus on best practices or patterns

Example scenario: "Review authentication implementation and get opinions from Gemini, Codex, and Qwen"

### Large Context Processing

Invoke Gemini when:
- Processing files >100k tokens
- Analyzing entire codebase structure
- Reviewing multiple related files simultaneously

Example scenario: "Analyze all files in src/ directory for architectural patterns"

### Planning with Consensus

Invoke multiple models with `planner` systemprompt when:
- Migration planning requires multiple approaches
- Architectural decisions need validation
- Risk assessment needs diverse perspectives

Example scenario: "Plan migration to TypeScript - compare strategies from Gemini and Codex"

## Typical Workflows

### Multi-Model Code Review

```bash
# Gemini/Qwen требуют --cwd для доступа к файлам проекта
PROJECT="/path/to/project"

python cli_caller.py --model gemini --cwd "$PROJECT" \
  --prompt "Ревью auth.py" --systemprompt default_codereviewer --timeout 60

python cli_caller.py --model codex \
  --prompt "Ревью $PROJECT/auth.py" --systemprompt codex_codereviewer

python cli_caller.py --model qwen --cwd "$PROJECT" \
  --prompt "Ревью auth.py" --systemprompt default_codereviewer
```

### Large File Analysis (>100k tokens)

```bash
python cli_caller.py --model gemini --prompt "Анализ large_file.py" --timeout 90
```

### Consensus Planning

```bash
python cli_caller.py --model gemini --prompt "План миграции на PostgreSQL" --systemprompt default_planner
python cli_caller.py --model codex --prompt "План миграции на PostgreSQL" --systemprompt default_planner
python cli_caller.py --model qwen --prompt "План миграции на PostgreSQL" --systemprompt default_planner
```

## Requirements

The following CLI tools must be installed and accessible:
- `gemini` (Google Gemini CLI)
- `codex` (OpenAI Codex CLI)
- `qwen` (Qwen CLI)
- `claude` (Claude CLI)

Verify installation: `which gemini codex qwen claude`

## Performance Characteristics

- Direct CLI invocation: ~2-3 seconds per call
- No MCP initialization overhead
- Timeout handling: configurable per call

## Common Errors

- ❌ `can't open file cli_caller.py` → используй полный путь или cd в директорию скилла
- ❌ `System prompt 'codereviewer' not found` → используй `default_codereviewer`, не `codereviewer`
- ❌ `timeout after 30s` → добавь `--timeout 60` для code review, `--timeout 90` для больших файлов

Проверка: `python cli_caller.py --model gemini --info`

## Best Practices

### Do's

✅ **Use appropriate models for tasks:**
- Gemini for large context (>100k tokens)
- Codex for code generation and refactoring
- Multiple models for consensus and validation

✅ **Set adequate timeouts:**
- Default: 30s for simple queries
- Code review: 60s
- Large files: 90s+

✅ **Choose correct system prompts:**
- `default_codereviewer` for general code review
- `codex_codereviewer` specifically for Codex
- `default_planner` for structured planning

✅ **Run from skill directory:**
```bash
cd ~/.claude/skills/ai-models-direct
python cli_caller.py --model gemini --prompt "..."
```

### Don'ts

❌ **Don't use wrong system prompt names:**
- Use `default_codereviewer`, not `codereviewer`
- Use `default_planner`, not `planner`

❌ **Don't forget timeouts for complex tasks:**
- Large file analysis will timeout with default 30s
- Code review needs 60s minimum

❌ **Don't use Gemini for simple tasks:**
- Reserve 1M context window for truly large files
- Use Codex or Claude for regular code tasks

❌ **Don't skip model verification:**
- Always verify CLIs installed: `which gemini codex qwen claude`
- Test with `--info` flag before production use

## Complete Example

**Scenario:** Review authentication module with multi-model consensus

### Step 1: Verify Setup

```bash
cd ~/.claude/skills/ai-models-direct

# Check models available
which gemini codex qwen claude

# Test model connection
python cli_caller.py --model gemini --info
```

### Step 2: Run Multi-Model Review

```bash
# Gemini review (large context, architectural perspective)
python cli_caller.py --model gemini \
  --prompt "Ревью src/auth/*.py на предмет безопасности и архитектуры" \
  --systemprompt default_codereviewer \
  --timeout 60

# Codex review (code quality, best practices)
python cli_caller.py --model codex \
  --prompt "Ревью src/auth/*.py с фокусом на code quality" \
  --systemprompt codex_codereviewer \
  --timeout 60

# Qwen review (alternative perspective)
python cli_caller.py --model qwen \
  --prompt "Ревью src/auth/*.py и предложи улучшения" \
  --systemprompt default_codereviewer \
  --timeout 60
```

### Step 3: Analyze Results

Compare outputs from all three models:
- Gemini: Architectural patterns, security concerns
- Codex: Code quality, refactoring suggestions
- Qwen: Alternative approaches, edge cases

### Step 4: Consensus Decision

Identify common findings across models for high-confidence issues.

## Technical Details

**Executor:** Direct CLI invocation via Python subprocess

**System Prompts Location:** `~/.claude/skills/ai-models-direct/systemprompts/`

**Supported Models:**
- Gemini: 1M token context window
- Codex: 128k token context window
- Qwen: Variable context
- Claude: 200k token context window

**Tool Inspection:**
```bash
python cli_caller.py --model gemini --info  # Model capabilities
ls systemprompts/                           # Available prompts
```
