# cli-agents - Examples

## Basic Model Calls

### Simple Query

```bash
python cli_caller.py --model gemini --prompt "Explain the authentication flow"
```

### With System Prompt

```bash
python cli_caller.py --model codex --prompt "Review auth.py" --systemprompt codex_codereviewer
```

### With Custom Timeout

```bash
python cli_caller.py --model gemini --prompt "Analyze large file" --timeout 60
```

### Model Information

```bash
python cli_caller.py --model gemini --info
```

## Multi-Model Code Review

### Scenario: Review Authentication Module

```bash
# Step 1: Gemini - Broad security analysis
python cli_caller.py --model gemini \
  --prompt "Review src/auth/*.py for security issues" \
  --systemprompt codereviewer

# Step 2: Codex - Implementation details
python cli_caller.py --model codex \
  --prompt "Review src/auth/*.py focusing on code quality" \
  --systemprompt codex_codereviewer
```

## Large Context Analysis

### Scenario: Analyze Entire Codebase

```bash
# Use Gemini for 1M token context window
python cli_caller.py --model gemini \
  --prompt "Analyze architecture patterns across all files in src/ directory. Identify: 1) Main architectural style, 2) Design patterns used, 3) Consistency issues" \
  --systemprompt default \
  --timeout 45
```

## Planning with Consensus

### Scenario: Migration Strategy

```bash
# Gemini - Strategic approach
python cli_caller.py --model gemini \
  --prompt "Plan migration from JavaScript to TypeScript for 50k LOC project" \
  --systemprompt planner

# Codex - Technical implementation
python cli_caller.py --model codex \
  --prompt "Plan migration from JavaScript to TypeScript for 50k LOC project" \
  --systemprompt planner
```

## Combining with Other Tools

### Scenario: Read File + Multi-Model Review

```bash
# 1. Read file content
file_content=$(cat src/complex_logic.py)

# 2. Get multiple opinions
python cli_caller.py --model gemini --prompt "Review this code: $file_content" --systemprompt codereviewer
python cli_caller.py --model codex --prompt "Review this code: $file_content" --systemprompt codex_codereviewer
```

## Error Handling

### Timeout Example

```bash
# For very large tasks, increase timeout
python cli_caller.py --model gemini \
  --prompt "Process 500k token file" \
  --timeout 120
```

### Check CLI Availability

```bash
# Verify all CLIs are installed
which gemini codex claude

# If missing, install (example for Homebrew)
brew install gemini-cli codex claude-code
```

## Integration Patterns

### Pattern 1: Sequential Review

Review code sequentially with increasing detail:

```bash
# 1. Detailed review (comprehensive model)
python cli_caller.py --model gemini --prompt "Detailed review of src/api/" --systemprompt codereviewer --timeout 60

# 2. Implementation specifics (code-focused model)
python cli_caller.py --model codex --prompt "Implementation review of src/api/" --systemprompt codex_codereviewer
```

### Pattern 2: Parallel Consensus

Get multiple opinions simultaneously:

```bash
# Launch both in background
python cli_caller.py --model gemini --prompt "Architecture decision: monolith vs microservices" > gemini.txt &
python cli_caller.py --model codex --prompt "Architecture decision: monolith vs microservices" > codex.txt &
wait

# Compare results
diff gemini.txt codex.txt
```

### Pattern 3: Staged Analysis

Use different models for different stages:

```bash
# Stage 1: Planning (Gemini - large context)
python cli_caller.py --model gemini --prompt "Plan refactoring strategy" --systemprompt planner

# Stage 2: Implementation (Codex - code generation)
python cli_caller.py --model codex --prompt "Generate refactored code based on plan"
```

## Tips

1. **Use Gemini for large files**: Context window of 1M tokens handles entire codebases
2. **Use Codex for code generation**: Best for writing and refactoring code
3. **Combine system prompts**: Different prompts give different perspectives
4. **Increase timeout for complex tasks**: Default 30s may not be enough for large analyses
