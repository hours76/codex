# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Interactive Agent Console - A comprehensive web-based and API-driven agent system that provides interactive access to a chat system with MCP tool integration located in `../rag/`. The agent provides:

- **Web Interface**: Full-featured HTML/CSS/JS chat interface with real-time WebSocket communication
- **REST API**: Complete FastAPI-based REST endpoints for programmatic access
- **Session Management**: Multi-session support with independent chat histories and task scheduling
- **Task Scheduling**: Automated messages at intervals or specific times per session
- **MCP Integration**: Seamless tool integration through chat.py subprocess
- **Subprocess IPC**: Stable binary-safe communication using asyncio.subprocess.PIPE

## Dependencies

### Required Libraries
- `fastapi`: Web framework for REST API endpoints
- `uvicorn`: ASGI server for running FastAPI applications
- `pydantic`: Data validation and settings management
- `asyncio`: Async task management, subprocess control, and scheduling
- `textual`: TUI framework (legacy, maintained for compatibility)

### Chat System Integration
The agent depends on the chat system in `../rag/chat.py` which requires:
- A Python virtual environment at `../rag/venv/`
- MCP tool servers configured with absolute Python interpreter paths in `../rag/config.json`
- The chat system to output a "> " prompt when ready for input
- MCP servers to have their own virtual environments properly configured

## Development Environment

### Virtual Environment
- **Local venv**: `./venv/` (Python 3.13.5 via Homebrew) with fastapi, uvicorn, pydantic, textual
- **Chat system venv**: `../rag/venv/`
- **MCP tool venvs**: Each MCP server has its own venv (e.g., `/Users/hrsung/Documents/work/msg/mcp/venv/`)

### Running the Agent
```bash
# Web interface mode (default)
./venv/bin/python agent.py

# Access web interface
open http://127.0.0.1:8000
```

### VS Code Configuration
Project includes `.vscode/settings.json` with Python interpreter path:
```json
{
    "python.defaultInterpreterPath": "/Users/hrsung/Documents/work/ai/agent/venv/bin/python",
    "python.terminal.activateEnvironment": true
}
```

### Testing
- `test_subprocess.py`: Test script for subprocess communication with chat.py
- Run: `./venv/bin/python test_subprocess.py`

## Code Architecture

### Main Components

1. **TaskScheduler Class** (`agent.py:84-522`): Core business logic
   - Chat session management with subprocess.PIPE
   - Async lock-based conversation serialization per session
   - Scheduled task system with interval/daily support per session
   - Smart task queue processing with session isolation

2. **ChatManager Class** (`agent.py:524-643`): WebSocket and session management
   - Multi-session WebSocket connection handling
   - Chat history management per session
   - Real-time message broadcasting
   - Session recovery and persistence

3. **FastAPI Web Server** (`agent.py:645-927`): Web interface and API
   - REST API endpoints for session management
   - WebSocket endpoints for real-time communication
   - Static file serving for web interface
   - Task scheduling API endpoints

### Key Implementation Details

#### Chat Session Management (Subprocess IPC)
- **Subprocess communication**: `asyncio.create_subprocess_exec()` with PIPE for stdin/stdout/stderr
- **Binary-safe IPC**: Direct byte streams without terminal emulation overhead
- **Startup handling**: Proper consumption of chat.py startup messages before first interaction
- **Echo removal**: Smart filtering of input message echoes from responses
- **Process management**: Graceful termination with timeout and force-kill fallback

#### MCP Integration (Transparent Pass-through)
- **No direct MCP code**: Agent passes `--mcp` flag to chat.py subprocess
- **Tool delegation**: All `/tool` commands handled by chat.py process
- **Configuration**: MCP servers configured in `../rag/config.json` with absolute venv paths
- **Isolation**: MCP issues don't affect agent functionality

#### Session Management
- **Multi-session support**: Independent chat processes and histories per session
- **WebSocket per session**: Real-time bidirectional communication
- **Session recovery**: Persistent chat history and process management
- **Session cleanup**: Automatic cleanup of inactive sessions

#### Scheduling System  
- **Per-session scheduling**: Tasks isolated by session ID
- **Task queue**: `asyncio.Queue()` for ordered execution per session
- **Smart execution**: Immediate when free, queued when busy
- **Prevent duplicates**: `is_running` flag blocks overlapping tasks per session
- **Strict intervals**: Maintains original schedule regardless of execution time

#### Web Interface
- **Modern HTML5**: Clean responsive design with WebSocket integration
- **Real-time updates**: Live chat updates without page refresh
- **Session switching**: Easy navigation between multiple chat sessions
- **Task management**: Web-based scheduling interface

### Integration Points
- **Chat System Path**: `../rag/venv/bin/python -u ../rag/chat.py --mcp`
- **Expected Interface**: Chat system outputs "> " prompt when ready
- **Response Format**: Expects standard text responses from chat system
- **MCP Tool Integration**: Chat.py handles all MCP tool calls transparently

## API Endpoints

### Web Interface
- `GET /` - Main chat web interface
- `GET /static/*` - Static assets (CSS, JS)

### WebSocket
- `WS /ws/{session_id}` - Real-time chat communication per session

### REST API
- `POST /api/sessions/{session_id}/schedule` - Schedule tasks for session
- `GET /api/sessions/{session_id}/tasks` - Get scheduled tasks for session
- `GET /api/tasks` - Get all scheduled tasks across sessions
- `DELETE /api/sessions/{session_id}/tasks` - Clear tasks for session
- `POST /api/sessions/new` - Create new session
- `GET /api/sessions` - List available sessions
- `GET /api/sessions/{session_id}` - Get session information
- `DELETE /api/sessions/{session_id}` - Cleanup session
- `GET /api/status` - Global system status
- `POST /api/debug` - Toggle debug mode

## Available Commands (via Chat Interface)

### Core Commands
- Send any message for AI conversation
- Automatic MCP tool integration (handled by chat.py)

### Web Interface Features
- Real-time chat with WebSocket
- Multiple session management
- Task scheduling via web UI
- Session history persistence

## Modification Guidelines

When modifying this agent:
- **Subprocess integration**: Maintain proper process lifecycle management
- **Async safety**: All chat operations must use per-session lock mechanism
- **Session isolation**: Ensure tasks and chat history remain per-session
- **Error handling**: Gracefully handle chat.py process failures and restarts
- **Memory management**: Consider implementing message limits for long sessions
- **MCP transparency**: Don't break the pass-through architecture to chat.py
- **WebSocket stability**: Handle connection drops and reconnections gracefully

### Key Files
- `agent.py`: Main application (927 lines)
- `web/index.html`: Web interface
- `web/static/`: CSS and JavaScript assets
- `test_subprocess.py`: Communication testing
- `.vscode/settings.json`: VS Code Python interpreter configuration

### No Build Process
This is a single-file FastAPI application with no build process. Dependencies are managed via pip in the virtual environment.