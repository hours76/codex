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

### Installation
```bash
pip install -r requirements.txt
```

### Required Libraries
- `fastapi>=0.104.0`: Web framework for REST API endpoints
- `uvicorn[standard]>=0.24.0`: ASGI server for running FastAPI applications
- `pydantic>=2.0.0`: Data validation and settings management
- `textual>=0.60.0`: TUI framework (legacy, maintained for compatibility)

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

## Code Architecture

### Modular Structure

The application is organized into separate modules for better maintainability:

1. **`agent.py`** (69 lines): Main entry point
   - Application startup and configuration
   - FastAPI app creation with lifespan management
   - Component initialization and cross-references

2. **`core.py`**: Core business logic
   - **ChatSession Class**: Individual chat subprocess management
   - **TaskScheduler Class**: Multi-session task scheduling and chat session management
   - Subprocess IPC with asyncio.PIPE
   - Automatic process restart on timeout

3. **`web.py`**: Web interface and API endpoints
   - **ChatManager Class**: WebSocket and session management
   - FastAPI route definitions with comprehensive logging
   - Session recovery and cleanup
   - Real-time message broadcasting

4. **`models.py`**: Data models and utilities
   - Pydantic models for API requests/responses
   - Custom logging formatter with message truncation
   - Configuration management with JSON loading and caching
   - Dot-notation config access (e.g., `get_config("server.host")`)

### Key Implementation Details

#### Chat Session Management (Subprocess IPC)
- **Subprocess communication**: `asyncio.create_subprocess_exec()` with PIPE for stdin/stdout/stderr
- **Binary-safe IPC**: Direct byte streams without terminal emulation overhead
- **Startup handling**: Proper consumption of chat.py startup messages before first interaction
- **Echo removal**: Smart filtering of input message echoes from responses
- **Process management**: Graceful termination with timeout and force-kill fallback
- **Auto-restart**: Automatic process restart on timeout with comprehensive logging

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
- `agent.py`: Main entry point (69 lines)
- `core.py`: Core business logic and chat session management
- `web.py`: Web interface, API endpoints, and WebSocket handling  
- `models.py`: Data models, logging configuration, and config management
- `monitor.py`: Task monitoring and auto-prompting system
- `config.json`: All application configuration settings
- `requirements.txt`: Python dependencies
- `web/index.html`: Web interface
- `web/static/`: CSS and JavaScript assets
- `.vscode/settings.json`: VS Code Python interpreter configuration

## Logging

All API endpoints and chat operations are logged with:
- **Comprehensive coverage**: Every API call, WebSocket message, and scheduled task
- **Message truncation**: All user input truncated to 16 characters for clean logs
- **Custom formatting**: Structured logging with prefixes ([API], [WEB], [TASK], etc.)
- **Debug support**: Configurable debug mode via `/api/debug` endpoint

## Configuration

The application uses `config.json` for all configuration settings, eliminating hardcoded values throughout the codebase.

### Configuration Sections

- **server**: Host, port, logging levels
- **chat_system**: Python executable paths, script locations, environment variables
- **timeouts**: All timeout values (prompt, message, process termination, etc.)
- **limits**: Message history, buffer sizes, truncation lengths for logging
- **session**: Session timeout, WebSocket close codes
- **ui**: Refresh intervals, notification settings
- **monitoring**: Task monitoring, auto-prompting settings

### Example Configuration

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000,
    "log_level": "warning",
    "access_log": false
  },
  "chat_system": {
    "python_executable": "../chat/venv/bin/python",
    "script_path": "../chat/chat.py",
    "working_directory": "../chat",
    "startup_args": ["--mcp"]
  },
  "monitoring": {
    "enabled": true,
    "auto_proceed_prompt": "please proceed",
    "min_response_length": 10,
    "broadcast_delay_ms": 100,
    "max_auto_prompts_per_task": 3
  },
  "timeouts": {
    "initial_prompt_timeout": 15,
    "message_response_timeout": 30
  }
}
```

## Task Monitoring System

The agent includes an intelligent monitoring system that automatically detects when AI responses don't contain tool calls and prompts the AI to continue working.

### Monitoring Features

- **Auto-Detection**: Automatically identifies responses without `/tool` commands
- **Smart Prompting**: Sends "[AUTO] please proceed" to nudge AI to use tools
- **Rate Limiting**: Maximum 3 auto-prompts per scheduled task (configurable)
- **Session Isolation**: Monitoring and counters are per-session
- **Clean UI Integration**: System messages appear in purple, right-aligned
- **Counter Reset**: Each new scheduled task execution gets fresh retry attempts

### Monitoring Workflow

1. **Scheduled task executes** → AI responds
2. **Monitor detects** → No `/tool` commands in response
3. **Auto-prompt sent** → "[AUTO] please proceed (1/3)" appears in web UI
4. **AI processes prompt** → Usually responds with tool calls
5. **If still no tools** → Repeat up to maximum attempts
6. **After max attempts** → Stop auto-prompting for this task execution

### Key Components

- **TaskMonitor Class** (`monitor.py`): Core monitoring logic and auto-prompting
- **Detection Logic**: Scans responses for `/tool` commands on clean lines
- **Counter Management**: Tracks retry attempts per unique task
- **WebSocket Integration**: Broadcasts system messages to web interface

## Installation & Setup

```bash
# Clone/navigate to project directory
cd /Users/hrsung/Documents/work/ai/agent

# Create virtual environment
python -m venv venv

# Activate virtual environment
source venv/bin/activate  # On macOS/Linux
# or
venv\Scripts\activate     # On Windows

# Install dependencies
pip install -r requirements.txt

# Configure the application (edit config.json as needed)
# Default config.json is included

# Run the application
python agent.py
```

### Build Process
This is a modular FastAPI application with no build process required. Dependencies are managed via pip and requirements.txt.