#!/usr/bin/env python3
"""
Ollama-Compatible API Server for CLI AI Models
Makes Gemini/Codex/Qwen/Claude CLI available as Ollama-compatible endpoints
Works with n8n Ollama nodes
"""

import json
import subprocess
import sys
import time
import uuid
from typing import Optional, List, Dict, Any

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "fastapi", "uvicorn"])
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn

# ============== Configuration ==============

# Базовые CLI команды
BASE_COMMANDS = {
    "gemini": ["/opt/homebrew/bin/gemini", "--yolo"],
    "codex": ["/opt/homebrew/bin/codex", "exec", "--skip-git-repo-check"],
    "qwen": ["/opt/homebrew/bin/qwen", "--yolo"],
    "claude": ["/opt/homebrew/bin/claude", "--print"],
}

# Маппинг моделей: имя в n8n -> (cli, [доп. аргументы])
# Формат: "model-name": ("base_cli", ["extra", "args"])
MODEL_COMMANDS = {
    # Gemini - основные
    "gemini": ("gemini", []),  # Auto - выберет лучшую (gemini-3-pro-preview → gemini-2.5-pro)
    "gemini-3-pro": ("gemini", ["-m", "gemini-3-pro-preview"]),
    "gemini-2.5-pro": ("gemini", ["-m", "gemini-2.5-pro"]),
    "gemini-2.5-flash": ("gemini", ["-m", "gemini-2.5-flash"]),
    "gemini-2.5-flash-lite": ("gemini", ["-m", "gemini-2.5-flash-lite"]),
    "gemini-2.0-flash": ("gemini", ["-m", "gemini-2.0-flash"]),
    "gemini-1.5-pro": ("gemini", ["-m", "gemini-1.5-pro"]),
    "gemini-1.5-flash": ("gemini", ["-m", "gemini-1.5-flash"]),

    # Claude варианты
    "claude": ("claude", []),
    "claude-sonnet": ("claude", ["-m", "claude-sonnet-4-20250514"]),
    "claude-opus": ("claude", ["-m", "claude-opus-4-20250514"]),
    "claude-haiku": ("claude", ["-m", "claude-haiku-3-5-20241022"]),

    # Codex
    "codex": ("codex", []),

    # Qwen
    "qwen": ("qwen", []),
}

# ============== Core Functions ==============

def call_model(model_name: str, prompt: str, timeout: int = 120, cwd: str = None) -> str:
    """Call AI model via CLI"""
    # Получаем конфиг модели
    if model_name in MODEL_COMMANDS:
        base_cli, extra_args = MODEL_COMMANDS[model_name]
    else:
        # Fallback на gemini
        base_cli, extra_args = "gemini", []

    # Собираем команду
    cmd = BASE_COMMANDS[base_cli].copy()
    cmd.extend(extra_args)
    cmd.append(prompt)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        if result.returncode != 0:
            return f"Error: {result.stderr}"
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return f"Error: timeout after {timeout}s"
    except FileNotFoundError:
        return f"Error: {model_name} CLI not found"
    except Exception as e:
        return f"Error: {str(e)}"

def format_messages(messages: List[Dict]) -> tuple[str, str, bool]:
    """Format chat messages to plain text. Returns (prompt, cwd, has_tool_results)

    Supports both Chat Completions and Responses API formats:
    - Chat Completions: {"role": "...", "content": "..."}
    - Responses API: {"type": "message|function_call|function_call_output", ...}
    """
    import re
    parts = []
    cwd = None
    has_tool_results = False

    for msg in messages:
        msg_type = msg.get("type")

        # Responses API format
        if msg_type == "function_call":
            # Вызов инструмента от ассистента
            tool_name = msg.get("name", "")
            tool_args = msg.get("arguments", "{}")
            parts.append(f"<ASSISTANT>\n[Вызов инструмента {tool_name}({tool_args})]\n</ASSISTANT>\n")
            continue

        elif msg_type == "function_call_output":
            # Результат инструмента
            call_id = msg.get("call_id", "")
            output = msg.get("output", "")
            parts.append(f"<TOOL_RESULT call_id=\"{call_id}\">\n{output}\n</TOOL_RESULT>\n")
            has_tool_results = True
            continue

        # Standard Chat Completions format (or Responses API message type)
        role = msg.get("role", "user").upper()
        content = msg.get("content", "")

        # Извлекаем cwd из system message: "CWD: /path/to/dir" или "[cwd:/path]"
        if role == "SYSTEM":
            # Паттерн: CWD: /path или [cwd:/path]
            match = re.search(r'(?:CWD:\s*|cwd:\s*|\[cwd:)([^\]\n]+)', content)
            if match:
                cwd = match.group(1).strip()
                # Убираем директиву из контента
                content = re.sub(r'(?:CWD:\s*[^\n]+\n?|\[cwd:[^\]]+\]\s*)', '', content).strip()

        parts.append(f"<{role}>\n{content}\n</{role}>\n")

    return "".join(parts), cwd, has_tool_results

# ============== FastAPI App ==============

app = FastAPI(title="Ollama-Compatible CLI Models")

# CORS для Obsidian и других браузерных клиентов
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"[{request.method}] {request.url.path}")
    response = await call_next(request)
    return response

# Ollama API endpoints

@app.get("/api/tags")
@app.get("/api/models")
async def list_models():
    """List available models (Ollama format)"""
    models = []
    for name, (base_cli, _) in MODEL_COMMANDS.items():
        models.append({
            "name": name,
            "model": name,
            "modified_at": "2024-01-01T00:00:00Z",
            "size": 0,
            "digest": f"sha256:{name}",
            "details": {
                "parent_model": "",
                "format": "cli",
                "family": base_cli,
                "families": [base_cli],
                "parameter_size": "unknown",
                "quantization_level": "none"
            }
        })
    return {"models": models}

@app.post("/api/generate")
async def generate(request: Request):
    """Generate completion (Ollama format)"""
    data = await request.json()
    model = data.get("model", "gemini")
    prompt = data.get("prompt", "")
    stream = data.get("stream", False)
    cwd = data.get("cwd") or data.get("options", {}).get("cwd")

    response_text = call_model(model, prompt, cwd=cwd)

    if stream:
        async def stream_response():
            # Send response in chunks
            yield json.dumps({
                "model": model,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "response": response_text,
                "done": False
            }) + "\n"
            yield json.dumps({
                "model": model,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "response": "",
                "done": True,
                "total_duration": 1000000000,
                "load_duration": 100000000,
                "prompt_eval_count": len(prompt) // 4,
                "eval_count": len(response_text) // 4
            }) + "\n"
        return StreamingResponse(stream_response(), media_type="application/x-ndjson")

    return JSONResponse({
        "model": model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "response": response_text,
        "done": True,
        "total_duration": 1000000000,
        "load_duration": 100000000,
        "prompt_eval_count": len(prompt) // 4,
        "eval_count": len(response_text) // 4
    })

@app.post("/api/chat")
async def chat(request: Request):
    """Chat completion (Ollama format)

    Supports cwd parameter:
    - "cwd": "/path/to/dir" - in request body
    - "options": {"cwd": "/path/to/dir"} - alternative
    - System message: "CWD: /path/to/dir" or "[cwd:/path/to/dir]"
    """
    data = await request.json()
    model = data.get("model", "gemini")
    messages = data.get("messages", [])
    stream = data.get("stream", False)

    prompt, cwd_from_msg, _ = format_messages(messages)
    # Приоритет: явный параметр > из system message
    cwd = data.get("cwd") or data.get("options", {}).get("cwd") or cwd_from_msg

    response_text = call_model(model, prompt, cwd=cwd)

    response_message = {"role": "assistant", "content": response_text}

    if stream:
        async def stream_response():
            yield json.dumps({
                "model": model,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "message": response_message,
                "done": False
            }) + "\n"
            yield json.dumps({
                "model": model,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "total_duration": 1000000000,
                "eval_count": len(response_text) // 4
            }) + "\n"
        return StreamingResponse(stream_response(), media_type="application/x-ndjson")

    return JSONResponse({
        "model": model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "message": response_message,
        "done": True,
        "total_duration": 1000000000,
        "eval_count": len(response_text) // 4
    })

@app.post("/api/embeddings")
async def embeddings(request: Request):
    """Dummy embeddings"""
    data = await request.json()
    return {"embedding": [0.0] * 384}

@app.get("/api/version")
async def version():
    return {"version": "0.1.0"}

@app.get("/")
@app.get("/health")
async def health():
    return {"status": "ok", "models": list(MODEL_COMMANDS.keys())}

# Also support OpenAI format for flexibility
@app.post("/v1/chat/completions")
async def openai_chat(request: Request):
    import re
    data = await request.json()
    model = data.get("model", "gemini")
    messages = data.get("messages", [])
    tools = data.get("tools", [])

    # Добавляем информацию о tools в prompt если они есть
    tools_prompt = ""
    if tools:
        tools_prompt = "\n\n<AVAILABLE_TOOLS>\n"
        tools_prompt += "У тебя есть доступ к следующим инструментам. Чтобы вызвать инструмент, ответь СТРОГО в формате:\n"
        tools_prompt += "TOOL_CALL: {\"name\": \"tool_name\", \"arguments\": {\"arg1\": \"value1\"}}\n\n"
        for tool in tools:
            if tool.get("type") == "function":
                # Поддержка обоих форматов: плоский (n8n) и вложенный (OpenAI)
                tool_name = tool.get("name") or tool.get("function", {}).get("name", "")
                tool_desc = tool.get("description") or tool.get("function", {}).get("description", "")
                tool_params = tool.get("parameters") or tool.get("function", {}).get("parameters")

                tools_prompt += f"- {tool_name}: {tool_desc}\n"
                if tool_params:
                    tools_prompt += f"  Параметры: {json.dumps(tool_params, ensure_ascii=False)}\n"
        tools_prompt += "\nЕсли тебе нужны данные из инструмента - ОБЯЗАТЕЛЬНО вызови его через TOOL_CALL.\n"
        tools_prompt += "Не выдумывай данные, используй только результаты инструментов.\n"
        tools_prompt += "</AVAILABLE_TOOLS>\n"

    prompt, cwd_from_msg, _ = format_messages(messages)
    if tools_prompt:
        prompt = tools_prompt + prompt
    cwd = data.get("cwd") or cwd_from_msg

    response_text = call_model(model, prompt, cwd=cwd)

    # Проверяем есть ли tool_call в ответе
    tool_call_match = re.search(r'TOOL_CALL:\s*(\{[^}]+\})', response_text, re.DOTALL)

    if tool_call_match and tools:
        try:
            tool_call_data = json.loads(tool_call_match.group(1))
            tool_name = tool_call_data.get("name", "")
            tool_args = tool_call_data.get("arguments", {})

            # Убираем TOOL_CALL из текста ответа
            clean_response = re.sub(r'TOOL_CALL:\s*\{[^}]+\}', '', response_text).strip()

            return JSONResponse({
                "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": clean_response if clean_response else None,
                        "tool_calls": [{
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args, ensure_ascii=False)
                            }
                        }]
                    },
                    "finish_reason": "tool_calls"
                }],
                "usage": {
                    "prompt_tokens": len(prompt) // 4,
                    "completion_tokens": len(response_text) // 4,
                    "total_tokens": (len(prompt) + len(response_text)) // 4
                }
            })
        except json.JSONDecodeError:
            pass  # Не удалось распарсить - возвращаем как обычный текст

    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": response_text},
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": len(response_text) // 4,
            "total_tokens": (len(prompt) + len(response_text)) // 4
        }
    })

@app.get("/v1/models")
async def openai_models():
    return {"object": "list", "data": [{"id": m, "object": "model"} for m in MODEL_COMMANDS.keys()]}

# OpenAI Responses API (newer format used by some n8n versions)
@app.post("/v1/responses")
async def openai_responses(request: Request):
    """OpenAI Responses API - redirect to chat completions"""
    import re
    data = await request.json()
    model = data.get("model", "gemini")


    # Responses API uses "input" instead of "messages"
    input_data = data.get("input", "")
    tools = data.get("tools", [])

    # Convert input to messages format
    if isinstance(input_data, str):
        messages = [{"role": "user", "content": input_data}]
    elif isinstance(input_data, list):
        messages = input_data
    else:
        messages = [{"role": "user", "content": str(input_data)}]

    # Add system instructions if present
    instructions = data.get("instructions", "")
    if instructions:
        messages.insert(0, {"role": "system", "content": instructions})

    # Форматируем сообщения и проверяем есть ли результаты tool call
    prompt, cwd_from_msg, has_tool_results = format_messages(messages)

    # Добавляем информацию о tools в prompt если они есть и нет результатов
    tools_prompt = ""
    if tools and not has_tool_results:
        # Первый запрос - модель должна решить вызывать ли tool
        tools_prompt = "\n\n<AVAILABLE_TOOLS>\n"
        tools_prompt += "У тебя есть доступ к следующим инструментам. Чтобы вызвать инструмент, ответь СТРОГО в формате:\n"
        tools_prompt += "TOOL_CALL: {\"name\": \"tool_name\", \"arguments\": {\"arg1\": \"value1\"}}\n\n"
        for tool in tools:
            if tool.get("type") == "function":
                # Поддержка обоих форматов: плоский (n8n) и вложенный (OpenAI)
                tool_name = tool.get("name") or tool.get("function", {}).get("name", "")
                tool_desc = tool.get("description") or tool.get("function", {}).get("description", "")
                tool_params = tool.get("parameters") or tool.get("function", {}).get("parameters")

                tools_prompt += f"- {tool_name}: {tool_desc}\n"
                if tool_params:
                    tools_prompt += f"  Параметры: {json.dumps(tool_params, ensure_ascii=False)}\n"
        tools_prompt += "\nЕсли тебе нужны данные из инструмента - ОБЯЗАТЕЛЬНО вызови его через TOOL_CALL.\n"
        tools_prompt += "Не выдумывай данные, используй только результаты инструментов.\n"
        tools_prompt += "</AVAILABLE_TOOLS>\n"
    elif has_tool_results:
        # Второй запрос - уже есть результаты tool call, нужно использовать их
        tools_prompt = "\n\n<INSTRUCTIONS>\n"
        tools_prompt += "Выше в истории есть TOOL_RESULT с данными от инструмента.\n"
        tools_prompt += "Используй эти данные для ответа на запрос пользователя.\n"
        tools_prompt += "НЕ вызывай инструменты повторно - данные уже получены.\n"
        tools_prompt += "Дай полный и полезный ответ на основе полученных данных.\n"
        tools_prompt += "</INSTRUCTIONS>\n"

    if tools_prompt:
        prompt = tools_prompt + prompt
    cwd = data.get("cwd") or cwd_from_msg

    response_text = call_model(model, prompt, cwd=cwd)

    # Проверяем есть ли tool_call в ответе (поддержка вложенных {})
    tool_call_match = re.search(r'TOOL_CALL:\s*(\{.*\})', response_text, re.DOTALL)
    if tool_call_match:
        # Пробуем найти валидный JSON
        json_str = tool_call_match.group(1)
        # Ищем первый валидный JSON объект
        brace_count = 0
        json_end = 0
        for i, c in enumerate(json_str):
            if c == '{':
                brace_count += 1
            elif c == '}':
                brace_count -= 1
                if brace_count == 0:
                    json_end = i + 1
                    break
        if json_end > 0:
            json_str = json_str[:json_end]
            tool_call_match = type('obj', (object,), {'group': lambda self, x: json_str})()

    response_id = f"resp_{uuid.uuid4().hex[:12]}"

    if tool_call_match and tools:
        try:
            tool_call_data = json.loads(tool_call_match.group(1))
            tool_name = tool_call_data.get("name", "")
            tool_args = tool_call_data.get("arguments", {})
            if isinstance(tool_args, str):
                tool_args = json.loads(tool_args)

            call_id = f"call_{uuid.uuid4().hex[:8]}"

            # OpenAI Responses API формат
            response_json = {
                "id": response_id,
                "object": "response",
                "created_at": int(time.time()),
                "status": "completed",
                "model": model,
                "output": [
                    {
                        "type": "function_call",
                        "id": call_id,
                        "call_id": call_id,
                        "name": tool_name,
                        "arguments": json.dumps(tool_args, ensure_ascii=False),
                        "status": "completed"
                    }
                ],
                "usage": {
                    "input_tokens": len(prompt) // 4,
                    "output_tokens": len(response_text) // 4,
                    "total_tokens": (len(prompt) + len(response_text)) // 4
                }
            }
            return JSONResponse(response_json)
        except json.JSONDecodeError:
            pass

    # Обычный ответ без tool call - Responses API формат
    response_json = {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:8]}",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": response_text
                    }
                ]
            }
        ],
        "usage": {
            "input_tokens": len(prompt) // 4,
            "output_tokens": len(response_text) // 4,
            "total_tokens": (len(prompt) + len(response_text)) // 4
        }
    }
    return JSONResponse(response_json)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11434)  # Ollama default port
    args = parser.parse_args()

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║       Ollama-Compatible CLI Models Server                 ║
╠═══════════════════════════════════════════════════════════╣
║  Models: gemini, claude, codex, qwen                      ║
║  Port: {args.port} (Ollama default)                            ║
╠═══════════════════════════════════════════════════════════╣
║  n8n Setup:                                               ║
║  1. Add "Ollama Chat Model" or "Ollama" node              ║
║  2. Base URL: http://192.168.2.81:{args.port}                  ║
║  3. Model: gemini / claude / qwen / codex                 ║
╚═══════════════════════════════════════════════════════════╝
""")

    uvicorn.run("ollama_compat_server:app", host=args.host, port=args.port)
