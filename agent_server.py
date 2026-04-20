#!/usr/bin/env python3
"""
AI Agent Server for n8n
Custom AI Agent with chat memory and tool calls
Supports: Gemini, Codex, Qwen, Claude
"""

import json
import sqlite3
import hashlib
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
    import uvicorn
except ImportError:
    print("Installing required packages...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "fastapi", "uvicorn", "pydantic"])
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
    import uvicorn

# ============== Configuration ==============

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "agent_memory.db"

MODEL_COMMANDS = {
    "gemini": {
        "cmd": ["gemini"],
        "timeout": 120,
        "context_window": 1_000_000,
        "supports_system": True
    },
    "codex": {
        "cmd": ["codex", "exec", "--skip-git-repo-check"],
        "timeout": 120,
        "context_window": 128_000,
        "supports_system": False
    },
    "qwen": {
        "cmd": ["qwen"],
        "timeout": 120,
        "context_window": 32_000,
        "supports_system": True
    },
    "claude": {
        "cmd": ["claude", "--print"],
        "timeout": 120,
        "context_window": 200_000,
        "supports_system": True
    }
}

# ============== Database ==============

def init_db():
    """Initialize SQLite database for chat memory"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Conversations table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            name TEXT,
            model TEXT,
            system_prompt TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            role TEXT,
            content TEXT,
            tool_calls TEXT,
            tool_results TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
    """)

    # Tools registry
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            description TEXT,
            parameters TEXT,
            endpoint TEXT,
            enabled INTEGER DEFAULT 1
        )
    """)

    conn.commit()
    conn.close()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# ============== Models ==============

class Message(BaseModel):
    role: str = Field(..., description="Role: user, assistant, system, tool")
    content: str
    tool_calls: Optional[List[Dict]] = None
    tool_results: Optional[List[Dict]] = None

class Tool(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    endpoint: Optional[str] = None  # HTTP endpoint for tool execution

class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str
    model: str = "gemini"
    system_prompt: Optional[str] = None
    tools: Optional[List[Tool]] = None
    max_history: int = 20
    timeout: int = 120

class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    tool_calls: Optional[List[Dict]] = None
    model: str
    tokens_used: Optional[int] = None

class ConversationInfo(BaseModel):
    id: str
    name: Optional[str]
    model: str
    message_count: int
    created_at: str
    updated_at: str

# ============== Core Functions ==============

def generate_conversation_id() -> str:
    """Generate unique conversation ID"""
    return hashlib.md5(f"{datetime.now().isoformat()}-{os.urandom(8).hex()}".encode()).hexdigest()[:16]

def get_or_create_conversation(conv_id: Optional[str], model: str, system_prompt: Optional[str]) -> str:
    """Get existing or create new conversation"""
    with get_db() as conn:
        cursor = conn.cursor()

        if conv_id:
            cursor.execute("SELECT id FROM conversations WHERE id = ?", (conv_id,))
            if cursor.fetchone():
                # Update timestamp
                cursor.execute(
                    "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (conv_id,)
                )
                conn.commit()
                return conv_id

        # Create new conversation
        new_id = conv_id or generate_conversation_id()
        cursor.execute(
            "INSERT INTO conversations (id, model, system_prompt) VALUES (?, ?, ?)",
            (new_id, model, system_prompt)
        )
        conn.commit()
        return new_id

def add_message(conv_id: str, role: str, content: str, tool_calls: List[Dict] = None, tool_results: List[Dict] = None):
    """Add message to conversation history"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO messages (conversation_id, role, content, tool_calls, tool_results)
               VALUES (?, ?, ?, ?, ?)""",
            (conv_id, role, content,
             json.dumps(tool_calls) if tool_calls else None,
             json.dumps(tool_results) if tool_results else None)
        )
        conn.commit()

def get_conversation_history(conv_id: str, max_messages: int = 20) -> List[Dict]:
    """Get conversation history"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT role, content, tool_calls, tool_results
               FROM messages
               WHERE conversation_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (conv_id, max_messages)
        )
        rows = cursor.fetchall()

        messages = []
        for row in reversed(rows):
            msg = {"role": row["role"], "content": row["content"]}
            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            if row["tool_results"]:
                msg["tool_results"] = json.loads(row["tool_results"])
            messages.append(msg)

        return messages

def format_history_for_prompt(history: List[Dict], system_prompt: Optional[str] = None) -> str:
    """Format conversation history as prompt text"""
    parts = []

    if system_prompt:
        parts.append(f"<system>\n{system_prompt}\n</system>\n")

    parts.append("<conversation>")
    for msg in history:
        role = msg["role"].upper()
        content = msg["content"]
        parts.append(f"\n<{role}>\n{content}\n</{role}>")

        if msg.get("tool_calls"):
            parts.append(f"\n<TOOL_CALLS>\n{json.dumps(msg['tool_calls'], indent=2)}\n</TOOL_CALLS>")

        if msg.get("tool_results"):
            parts.append(f"\n<TOOL_RESULTS>\n{json.dumps(msg['tool_results'], indent=2)}\n</TOOL_RESULTS>")

    parts.append("\n</conversation>")

    return "".join(parts)

def format_tools_for_prompt(tools: List[Tool]) -> str:
    """Format tools as prompt text"""
    if not tools:
        return ""

    parts = ["\n<available_tools>"]
    for tool in tools:
        parts.append(f"""
<tool name="{tool.name}">
  <description>{tool.description}</description>
  <parameters>{json.dumps(tool.parameters, indent=2)}</parameters>
</tool>""")

    parts.append("""
</available_tools>

To use a tool, respond with:
<tool_call>
{"name": "tool_name", "parameters": {"param1": "value1"}}
</tool_call>

You can make multiple tool calls. After tool results are returned, continue the conversation.
""")

    return "".join(parts)

def parse_tool_calls(response: str) -> tuple[str, List[Dict]]:
    """Parse tool calls from model response"""
    import re

    tool_calls = []
    clean_response = response

    # Find all tool_call blocks
    pattern = r'<tool_call>\s*({.*?})\s*</tool_call>'
    matches = re.findall(pattern, response, re.DOTALL)

    for match in matches:
        try:
            tool_call = json.loads(match)
            tool_calls.append(tool_call)
        except json.JSONDecodeError:
            pass

    # Remove tool_call blocks from response
    clean_response = re.sub(pattern, '', response, flags=re.DOTALL).strip()

    return clean_response, tool_calls

def execute_tool(tool: Tool, parameters: Dict) -> Dict:
    """Execute a tool and return result"""
    if tool.endpoint:
        # HTTP tool
        try:
            import requests
            resp = requests.post(tool.endpoint, json=parameters, timeout=30)
            return {"success": True, "result": resp.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Built-in tools
    if tool.name == "bash":
        try:
            result = subprocess.run(
                parameters.get("command", "echo 'No command'"),
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif tool.name == "read_file":
        try:
            path = parameters.get("path", "")
            with open(path, "r") as f:
                content = f.read()
            return {"success": True, "content": content}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif tool.name == "write_file":
        try:
            path = parameters.get("path", "")
            content = parameters.get("content", "")
            with open(path, "w") as f:
                f.write(content)
            return {"success": True, "message": f"Written to {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif tool.name == "http_request":
        try:
            import requests
            method = parameters.get("method", "GET").upper()
            url = parameters.get("url", "")
            data = parameters.get("data")
            headers = parameters.get("headers", {})

            resp = requests.request(method, url, json=data, headers=headers, timeout=30)
            return {
                "success": True,
                "status_code": resp.status_code,
                "body": resp.text[:5000]  # Limit response size
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": f"Unknown tool: {tool.name}"}

def call_model(model_name: str, prompt: str, timeout: int = 120) -> str:
    """Call AI model via CLI"""
    if model_name not in MODEL_COMMANDS:
        raise ValueError(f"Unknown model: {model_name}")

    config = MODEL_COMMANDS[model_name]
    cmd = config["cmd"].copy()
    cmd.append(prompt)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:
            return f"Error: {result.stderr}"

        return result.stdout

    except subprocess.TimeoutExpired:
        return f"Error: Model timed out after {timeout}s"
    except FileNotFoundError:
        return f"Error: {model_name} CLI not found"
    except Exception as e:
        return f"Error: {str(e)}"

# ============== FastAPI App ==============

app = FastAPI(
    title="AI Agent Server",
    description="Custom AI Agent for n8n with chat memory and tool calls",
    version="1.0.0"
)

@app.on_event("startup")
async def startup():
    init_db()

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Main chat endpoint with memory and tool support

    Example n8n HTTP Request:
    - Method: POST
    - URL: http://localhost:8765/chat
    - Body: {"message": "Hello", "model": "gemini", "conversation_id": "optional-id"}
    """

    # Get or create conversation
    conv_id = get_or_create_conversation(
        request.conversation_id,
        request.model,
        request.system_prompt
    )

    # Get conversation history
    history = get_conversation_history(conv_id, request.max_history)

    # Add current message to history
    history.append({"role": "user", "content": request.message})
    add_message(conv_id, "user", request.message)

    # Build prompt
    prompt_parts = [format_history_for_prompt(history, request.system_prompt)]

    if request.tools:
        prompt_parts.append(format_tools_for_prompt(request.tools))

    prompt_parts.append("\n<ASSISTANT>")
    full_prompt = "".join(prompt_parts)

    # Call model
    response = call_model(request.model, full_prompt, request.timeout)

    # Parse tool calls
    clean_response, tool_calls = parse_tool_calls(response)

    # Execute tools if any
    tool_results = []
    if tool_calls and request.tools:
        tools_by_name = {t.name: t for t in request.tools}

        for tc in tool_calls:
            tool_name = tc.get("name")
            if tool_name in tools_by_name:
                result = execute_tool(tools_by_name[tool_name], tc.get("parameters", {}))
                tool_results.append({
                    "tool": tool_name,
                    "result": result
                })

    # Save assistant message
    add_message(conv_id, "assistant", clean_response, tool_calls, tool_results)

    # If tools were called, make follow-up call with results
    if tool_results:
        history.append({
            "role": "assistant",
            "content": clean_response,
            "tool_calls": tool_calls,
            "tool_results": tool_results
        })

        follow_up_prompt = format_history_for_prompt(history, request.system_prompt)
        follow_up_prompt += "\n\nTool results above. Continue the conversation.\n<ASSISTANT>"

        follow_up_response = call_model(request.model, follow_up_prompt, request.timeout)
        clean_follow_up, _ = parse_tool_calls(follow_up_response)

        add_message(conv_id, "assistant", clean_follow_up)
        clean_response = clean_follow_up

    return ChatResponse(
        conversation_id=conv_id,
        response=clean_response,
        tool_calls=tool_calls if tool_calls else None,
        model=request.model
    )

@app.get("/conversations", response_model=List[ConversationInfo])
async def list_conversations(limit: int = 50):
    """List all conversations"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.name, c.model, c.created_at, c.updated_at,
                   COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ?
        """, (limit,))

        return [
            ConversationInfo(
                id=row["id"],
                name=row["name"],
                model=row["model"],
                message_count=row["message_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"]
            )
            for row in cursor.fetchall()
        ]

@app.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: str, limit: int = 100):
    """Get messages for a conversation"""
    messages = get_conversation_history(conv_id, limit)
    return {"conversation_id": conv_id, "messages": messages}

@app.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a conversation and its messages"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        cursor.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        conn.commit()
    return {"status": "deleted", "conversation_id": conv_id}

@app.post("/conversations/{conv_id}/clear")
async def clear_conversation(conv_id: str):
    """Clear messages but keep conversation"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        conn.commit()
    return {"status": "cleared", "conversation_id": conv_id}

# ============== Tool Management ==============

@app.post("/tools/register")
async def register_tool(tool: Tool):
    """Register a new tool"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT OR REPLACE INTO tools (name, description, parameters, endpoint)
               VALUES (?, ?, ?, ?)""",
            (tool.name, tool.description, json.dumps(tool.parameters), tool.endpoint)
        )
        conn.commit()
    return {"status": "registered", "tool": tool.name}

@app.get("/tools")
async def list_tools():
    """List all registered tools"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, description, parameters, endpoint, enabled FROM tools")
        return [
            {
                "name": row["name"],
                "description": row["description"],
                "parameters": json.loads(row["parameters"]) if row["parameters"] else {},
                "endpoint": row["endpoint"],
                "enabled": bool(row["enabled"])
            }
            for row in cursor.fetchall()
        ]

# ============== Simple Call (без памяти) ==============

class SimpleRequest(BaseModel):
    prompt: str
    model: str = "gemini"
    system_prompt: Optional[str] = None
    timeout: int = 120

@app.post("/simple")
async def simple_call(request: SimpleRequest):
    """
    Simple model call without memory (like original cli_caller.py)

    Example:
    - POST /simple
    - Body: {"prompt": "What is 2+2?", "model": "gemini"}
    """
    prompt = request.prompt
    if request.system_prompt:
        prompt = f"<system>\n{request.system_prompt}\n</system>\n\n{prompt}"

    response = call_model(request.model, prompt, request.timeout)
    return {"response": response, "model": request.model}

# ============== Health ==============

@app.get("/health")
async def health():
    return {"status": "ok", "models": list(MODEL_COMMANDS.keys())}

# ============== Main ==============

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI Agent Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    args = parser.parse_args()

    print(f"Starting AI Agent Server on {args.host}:{args.port}")
    print(f"Available models: {', '.join(MODEL_COMMANDS.keys())}")
    print(f"Database: {DB_PATH}")
    print("\nEndpoints:")
    print("  POST /chat - Chat with memory and tools")
    print("  POST /simple - Simple call without memory")
    print("  GET  /conversations - List conversations")
    print("  GET  /health - Health check")

    uvicorn.run(
        "agent_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload
    )
