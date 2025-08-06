#!/usr/bin/env python3
"""
Interactive Agent Console - Combined Web Interface and Scheduler
Provides web interface at '/' and API endpoints at '/api/*'
"""

import asyncio  # Used in create_chat_session
from datetime import datetime, timedelta
import re
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager
import logging
import sys

# Configure custom logging
class CustomFormatter(logging.Formatter):
    def format(self, record):
        # Ensure we have the message attribute
        if not hasattr(record, 'message'):
            record.message = record.getMessage()
            
        # Check if message has a custom prefix
        msg = record.message
        if msg.startswith('[USER]') or msg.startswith('[AI]') or msg.startswith('[API]') or \
           msg.startswith('[TASK]') or msg.startswith('[AGENT]') or msg.startswith('[DEBUG]'):
            # Keep the custom prefix, remove the level name
            return msg
        
        # Default formatting for other logs
        if record.levelname == 'INFO':
            return f'[WEB] {msg}'
        elif record.levelname == 'ERROR':
            return f'[ERROR] {msg}'
        elif record.levelname == 'WARNING':
            return f'[WARN] {msg}'
        elif record.levelname == 'DEBUG':
            return f'[DEBUG] {msg}'
        return msg

# Set up logging
logger = logging.getLogger("agent")
logger.setLevel(logging.INFO)

# Debug mode flag
DEBUG_MODE = False

# Remove default handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Console handler with custom formatter
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(CustomFormatter('%(levelname)s %(message)s'))
logger.addHandler(console_handler)

# Suppress uvicorn access logs and startup/shutdown messages
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)

class ChatMessage(BaseModel):
    message: str
    timestamp: str
    sender: str  # 'user' or 'ai'

class ScheduleRequest(BaseModel):
    message: str
    schedule_spec: str

class ScheduledTask(BaseModel):
    message: str
    schedule_spec: str
    next_run: datetime
    last_run: Optional[datetime] = None
    is_running: bool = False

class TaskScheduler:
    def __init__(self):
        self.chat_sessions = {}  # Dictionary to store multiple chat sessions
        self.running = False
        self.task_queue = None  # Initialize later when event loop is ready
        self.debug_mode = DEBUG_MODE
        self.scheduled_tasks = {}  # Dictionary: session_id -> [tasks]
        self.scheduler_running = False
        self.chat_manager_ref: Any = None  # Reference to ChatManager for broadcasting
        
    async def create_chat_session(self, session_id: str):
        """Create a new chat session for a specific session ID"""
        try:
            # Create subprocess with pipes for communication
            process = await asyncio.create_subprocess_exec(
                '../rag/venv/bin/python', '-u', '../rag/chat.py', '--mcp',
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd='../rag',
                env={**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8'}
            )
            
            # Store process with a lock for thread-safe access
            self.chat_sessions[session_id] = {
                'process': process,
                'lock': asyncio.Lock()
            }
            
            # Wait for initial prompt
            try:
                await self._wait_for_prompt(session_id, timeout=15)
            except asyncio.TimeoutError:
                logger.error(f"Timeout waiting for initial prompt from chat session {session_id}")
                await self.close_chat_session(session_id)
                return False
            
            if self.debug_mode:
                logger.debug(f"[DEBUG] Chat session {session_id} spawned, PID: {process.pid}")
            
            # Initialize task list for this session
            if session_id not in self.scheduled_tasks:
                self.scheduled_tasks[session_id] = []
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to create chat session {session_id}: {e}")
            return False
    
    async def _wait_for_prompt(self, session_id: str, timeout: float = 10):
        """Wait for the '> ' prompt from the chat process, consuming startup messages"""
        session = self.chat_sessions.get(session_id)
        if not session:
            raise Exception(f"No session found for {session_id}")
        
        process = session['process']
        buffer = b''
        all_output = b''  # Collect all output for debugging
        
        async def read_until_prompt():
            nonlocal buffer, all_output
            prompt_count = 0
            
            while True:
                try:
                    chunk = await asyncio.wait_for(process.stdout.read(1), timeout=0.1)
                    if not chunk:
                        if process.returncode is not None:
                            raise Exception(f"Process exited with code {process.returncode}")
                        continue
                    
                    buffer += chunk
                    all_output += chunk
                    
                    # Check for prompt at end of buffer
                    if buffer.endswith(b'\n> ') or buffer.endswith(b'> '):
                        prompt_count += 1
                        
                        # The first prompt often comes with startup messages
                        # Wait for a clean prompt (just "\n> " without other text on the same line)
                        # or return after seeing at least one prompt
                        if prompt_count >= 1:
                            # Check if this is a clean prompt (no other text on the same line)
                            lines = buffer.split(b'\n')
                            if len(lines) >= 2 and lines[-2] == b'' and lines[-1] == b'> ':
                                # Clean prompt found
                                return all_output
                            elif buffer.endswith(b'\n> '):
                                # Also accept prompts that end with newline
                                return all_output
                            elif prompt_count >= 2:
                                # Accept any prompt after seeing multiple
                                return all_output
                    
                    # Keep buffer size limited
                    if len(buffer) > 200:
                        buffer = buffer[-200:]
                        
                except asyncio.TimeoutError:
                    continue
        
        result = await asyncio.wait_for(read_until_prompt(), timeout=timeout)
        
        if self.debug_mode:
            # Log startup messages for debugging
            startup_text = result.decode('utf-8', errors='ignore')
            if len(startup_text) > 100:
                logger.debug(f"[DEBUG] Startup messages from {session_id}: {startup_text[:100]}...")
            else:
                logger.debug(f"[DEBUG] Startup messages from {session_id}: {startup_text}")
        
        return result
    
    async def close_chat_session(self, session_id: str):
        """Close a specific chat session"""
        if session_id in self.chat_sessions:
            try:
                session = self.chat_sessions[session_id]
                process = session['process']
                
                # Terminate the process
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                
                del self.chat_sessions[session_id]
                
                # Clean up scheduled tasks for this session
                if session_id in self.scheduled_tasks:
                    del self.scheduled_tasks[session_id]
                
                logger.info(f"Session {session_id} closed")
            except Exception as e:
                logger.error(f"Error closing chat session {session_id}: {e}")
    
    def get_chat_session(self, session_id: str):
        """Get chat session for a specific session ID"""
        return self.chat_sessions.get(session_id)
    
    async def agent_ask_async(self, session_id: str, question: str, task_type: str = "user"):
        """Direct AI interaction for specific session"""
        _ = task_type  # Parameter kept for API compatibility
        return await self.send_message(session_id, question)

    async def send_message(self, session_id: str, message: str):
        """Send message to chat process and get response"""
        session = self.chat_sessions.get(session_id)
        if not session:
            return f"Error: No chat session found for {session_id}"
        
        async with session['lock']:
            process = session['process']
            
            try:
                # Send message
                process.stdin.write(f"{message}\n".encode('utf-8'))
                await process.stdin.drain()
                
                # Read response until next prompt
                response = b''
                buffer = b''
                
                while True:
                    chunk = await asyncio.wait_for(process.stdout.read(1), timeout=30)
                    if not chunk:
                        if process.returncode is not None:
                            return f"Error: Process exited with code {process.returncode}"
                        continue
                    
                    buffer += chunk
                    response += chunk
                    
                    # Check for prompt at end
                    if buffer.endswith(b'\n> ') or (len(buffer) > 2 and buffer[-3:] == b'\n> '):
                        # Remove the prompt from response
                        if response.endswith(b'\n> '):
                            response = response[:-3]
                        elif response.endswith(b'> '):
                            response = response[:-2]
                        break
                    
                    # Keep buffer size limited
                    if len(buffer) > 100:
                        buffer = buffer[-100:]
                
                # Decode and clean response
                text = response.decode('utf-8', errors='ignore')
                
                # Remove echo of the input message if present
                # The echo might appear after some whitespace or newlines
                lines = text.split('\n')
                clean_lines = []
                message_found = False
                
                for line in lines:
                    # Skip the line if it's exactly our input message
                    if line.strip() == message.strip() and not message_found:
                        message_found = True
                        continue
                    # Keep all other lines
                    clean_lines.append(line)
                
                # Rejoin and clean up
                text = '\n'.join(clean_lines).strip()
                
                return text
                
            except asyncio.TimeoutError:
                return "Error: Timeout waiting for response"
            except Exception as e:
                return f"Error: {e}"
    
    def agent_ask(self, session_id: str, question: str):
        """Synchronous wrapper for compatibility"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.send_message(session_id, question))
        finally:
            loop.close()
    
    def parse_schedule_time(self, time_str):
        """Parse time string like '10:30', '2:15pm', 'daily 9:00', 'every 30min'"""
        time_str = time_str.lower().strip()
        
        if time_str.startswith('every ') and ('min' in time_str or 'hour' in time_str):
            match = re.search(r'every\s+(\d+)\s*(min|minutes|hour|hours)', time_str)
            if match:
                value = int(match.group(1))
                unit = match.group(2)
                if unit in ['min', 'minutes']:
                    unit = 'min'
                elif unit in ['hour', 'hours']:
                    unit = 'hour'
                return ('interval', value, unit)
        
        if time_str.startswith('daily '):
            time_part = time_str.replace('daily ', '').strip()
            return ('daily', time_part)
        
        if ':' in time_str:
            return ('daily', time_str)
        
        return None
    
    def parse_time_string(self, time_str):
        """Parse time string to datetime.time object"""
        original_time_str = time_str.strip().lower()
        is_pm = 'pm' in original_time_str
        is_am = 'am' in original_time_str
        
        time_str = original_time_str.replace('pm', '').replace('am', '').strip()
            
        if ':' in time_str:
            hour_str, minute_str = time_str.split(':', 1)
            hour = int(hour_str)
            minute = int(minute_str)
        else:
            hour = int(time_str)
            minute = 0
            
        if is_pm and hour != 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0
            
        return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    def schedule_task(self, session_id: str, message: str, schedule_spec: str):
        """Schedule a message to be sent at specified time for specific session"""
        parsed = self.parse_schedule_time(schedule_spec)
        
        if not parsed:
            return False, f"Invalid time format '{schedule_spec}'. Use: 'daily 10:30', 'every 30min', '2:15pm'"
        
        # Ensure session exists
        if session_id not in self.scheduled_tasks:
            self.scheduled_tasks[session_id] = []
        
        try:
            task_info = {
                'session_id': session_id,
                'message': message,
                'schedule_spec': schedule_spec,
                'parsed': parsed,
                'last_run': None,
                'is_running': False
            }
            
            if parsed[0] == 'interval':
                value, unit = parsed[1], parsed[2]
                if unit == 'min':
                    task_info['interval_seconds'] = value * 60
                else:
                    task_info['interval_seconds'] = value * 3600
                task_info['next_run'] = datetime.now() + timedelta(seconds=task_info['interval_seconds'])
            else:
                time_str = parsed[1]
                target_time = self.parse_time_string(time_str)
                if target_time <= datetime.now():
                    target_time += timedelta(days=1)
                task_info['next_run'] = target_time
            
            self.scheduled_tasks[session_id].append(task_info)
            logger.info(f"[TASK] Task scheduled for session {session_id}: '{message}' at {schedule_spec}")
            return True, f"Scheduled for session {session_id}: '{message}' at {schedule_spec}"
            
        except Exception as e:
            return False, f"Error scheduling: {e}"
    
    async def scheduled_message_for_session(self, session_id, message):
        """Queue scheduled message for execution for specific session"""
        if session_id in self.chat_sessions and self.task_queue:
            await self.task_queue.put(('scheduled', session_id, message))
    
    async def process_task_queue(self):
        """Process queued tasks one by one"""
        # Initialize the task queue in the correct event loop
        if self.task_queue is None:
            self.task_queue = asyncio.Queue()
            
        while self.running:
            try:
                # Wait for a task with timeout to prevent blocking
                task = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)
                task_type, session_id, message = task
                
                if task_type == 'scheduled':
                    response = await self.agent_ask_async(session_id, message, "scheduled")
                    logger.info(f"[TASK] Scheduled prompt sent to session {session_id}: {message[:50]}...")
                    
                    # Broadcast scheduled message and response to chat
                    if hasattr(self, 'chat_manager_ref') and self.chat_manager_ref:
                        await self.chat_manager_ref.broadcast_scheduled_message(session_id, message, response)
                
                self.task_queue.task_done()
                    
            except asyncio.TimeoutError:
                # No task available, continue loop
                continue
            except Exception as e:
                logger.error(f"[TASK] Task queue error: {e}")
                try:
                    if self.task_queue:
                        self.task_queue.task_done()
                except:
                    pass
            
            await asyncio.sleep(0.01)
    
    async def run_scheduler(self):
        """Run the scheduler in background for all sessions"""
        logger.info("[TASK] Scheduler started")
        while self.scheduler_running and self.running:
            now = datetime.now()
            
            # Check tasks for all sessions
            for session_id, tasks in self.scheduled_tasks.items():
                _ = session_id  # Used for iteration only
                for task in tasks[:]:
                    time_diff = (task['next_run'] - now).total_seconds()
                    _ = time_diff  # Calculated but not used in current logic
                    
                    if now >= task['next_run'] and not task['is_running']:
                        task['is_running'] = True
                        asyncio.create_task(self._execute_scheduled_task(task))
                        
                        if task['parsed'][0] == 'interval':
                            while task['next_run'] <= now:
                                task['next_run'] += timedelta(seconds=task['interval_seconds'])
                        else:
                            task['next_run'] += timedelta(days=1)
                        
                        task['last_run'] = now
            
            await asyncio.sleep(1)
        logger.info("[TASK] Scheduler stopped")
    
    async def _execute_scheduled_task(self, task):
        """Execute a scheduled task for specific session and clear the running flag when done"""
        try:
            session_id = task['session_id']
            await self.scheduled_message_for_session(session_id, task['message'])
        except Exception as e:
            logger.error(f"[TASK] Task execution error: {e}")
        finally:
            task['is_running'] = False

    def get_scheduled_tasks(self, session_id=None):
        """Get scheduled tasks for specific session or all sessions"""
        tasks = []
        
        if session_id:
            # Get tasks for specific session
            if session_id in self.scheduled_tasks:
                for task in self.scheduled_tasks[session_id]:
                    tasks.append({
                        'session_id': task['session_id'],
                        'message': task['message'],
                        'schedule_spec': task['schedule_spec'],
                        'next_run': task['next_run'].isoformat(),
                        'last_run': task['last_run'].isoformat() if task['last_run'] else None,
                        'is_running': task['is_running']
                    })
        else:
            # Get tasks for all sessions
            for session_id, session_tasks in self.scheduled_tasks.items():
                for task in session_tasks:
                    tasks.append({
                        'session_id': task['session_id'],
                        'message': task['message'],
                        'schedule_spec': task['schedule_spec'],
                        'next_run': task['next_run'].isoformat(),
                        'last_run': task['last_run'].isoformat() if task['last_run'] else None,
                        'is_running': task['is_running']
                    })
        return tasks

    def clear_scheduled_tasks(self, session_id=None):
        """Clear scheduled tasks for specific session or all sessions"""
        count = 0
        
        if session_id:
            # Clear tasks for specific session
            if session_id in self.scheduled_tasks:
                count = len(self.scheduled_tasks[session_id])
                self.scheduled_tasks[session_id] = []
        else:
            # Clear all tasks for all sessions
            for session_tasks in self.scheduled_tasks.values():
                count += len(session_tasks)
            for session_id in self.scheduled_tasks:
                self.scheduled_tasks[session_id] = []
            
        # Stop scheduler if no tasks remain
        total_tasks = sum(len(tasks) for tasks in self.scheduled_tasks.values())
        if total_tasks == 0:
            self.scheduler_running = False
            
        return count

class ChatManager:
    def __init__(self, scheduler: TaskScheduler):
        self.scheduler = scheduler
        self.active_connections: Dict[str, List[WebSocket]] = {}  # session_id -> [websockets]
        self.chat_history: Dict[str, List[ChatMessage]] = {}  # session_id -> [messages]
        
    async def connect_session(self, websocket: WebSocket, session_id: str):
        """Connect websocket to a specific session"""
        await websocket.accept()
        
        # Initialize active connections for this session
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        
        # Initialize chat history if it doesn't exist
        if session_id not in self.chat_history:
            self.chat_history[session_id] = []
        
        # Create chat process if it doesn't exist
        if session_id not in self.scheduler.chat_sessions:
            await self.scheduler.create_chat_session(session_id)
        
        # Add websocket to connections - ensure the list exists first
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append(websocket)
        
        # Send chat history to new connection (for session recovery)
        for message in self.chat_history[session_id]:
            await websocket.send_text(message.model_dump_json())
            
        # Session connected (no log needed for normal operation)
    
    def disconnect_session(self, websocket: WebSocket, session_id: str):
        """Disconnect websocket from a specific session"""
        if session_id in self.active_connections and websocket in self.active_connections[session_id]:
            self.active_connections[session_id].remove(websocket)
            
            # Keep session data in memory for recovery
            # Only remove from active connections, but keep chat history and chat process
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]
                # Session disconnected - no need to log, will log on cleanup
    
    async def broadcast_to_session(self, session_id: str, message: ChatMessage):
        """Broadcast message to specific session"""
        # Store message in session history
        if session_id not in self.chat_history:
            self.chat_history[session_id] = []
        
        self.chat_history[session_id].append(message)
        
        # Keep only last 100 messages per session
        if len(self.chat_history[session_id]) > 100:
            self.chat_history[session_id] = self.chat_history[session_id][-100:]
        
        # Send to all connections in this session
        if session_id in self.active_connections:
            for connection in self.active_connections[session_id][:]:  # Copy list to avoid modification issues
                try:
                    await connection.send_text(message.model_dump_json())
                except:
                    # Remove failed connection
                    self.active_connections[session_id].remove(connection)
    
    async def ask_ai(self, session_id: str, question: str) -> str:
        """Send question to AI and get response for specific session"""
        return await self.scheduler.agent_ask_async(session_id, question, "user")
    
    async def broadcast_scheduled_message(self, session_id: str, question: str, response: str):
        """Broadcast scheduled message and response to specific session"""
        from datetime import datetime
        
        # Broadcast the scheduled question
        scheduled_message = ChatMessage(
            message=f"[SCHEDULED] {question}",
            timestamp=datetime.now().isoformat(),
            sender="scheduled"
        )
        await self.broadcast_to_session(session_id, scheduled_message)
        
        # Broadcast the AI response
        if response:
            ai_message = ChatMessage(
                message=response,
                timestamp=datetime.now().isoformat(),
                sender="ai"
            )
            await self.broadcast_to_session(session_id, ai_message)
    
    def get_active_sessions(self):
        """Get list of active session IDs"""
        return list(self.active_connections.keys())
    
    def get_available_sessions(self):
        """Get list of all available sessions (including disconnected ones with history)"""
        chat_sessions = set(self.chat_history.keys())
        scheduler_sessions = set(self.scheduler.scheduled_tasks.keys())
        process_sessions = set(self.scheduler.chat_sessions.keys())
        return list(chat_sessions.union(scheduler_sessions).union(process_sessions))
    
    def get_session_info(self, session_id: str):
        """Get session information for recovery"""
        return {
            'session_id': session_id,
            'has_history': session_id in self.chat_history,
            'history_count': len(self.chat_history.get(session_id, [])),
            'has_process': session_id in self.scheduler.chat_sessions,
            'has_tasks': session_id in self.scheduler.scheduled_tasks,
            'task_count': len(self.scheduler.scheduled_tasks.get(session_id, [])),
            'is_connected': session_id in self.active_connections
        }

# Initialize scheduler and chat manager
scheduler = TaskScheduler()
chat_manager = ChatManager(scheduler)

# Set up reference so scheduler can broadcast to chat
scheduler.chat_manager_ref = chat_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    _ = app  # Required FastAPI parameter, marked as unused
    """Handle application startup and shutdown"""
    # Startup
    scheduler.running = True
    asyncio.create_task(scheduler.process_task_queue())
    
    yield
    
    # Shutdown
    scheduler.running = False
    scheduler.scheduler_running = False
    
    # Close all chat sessions
    for session_id in list(scheduler.chat_sessions.keys()):
        await scheduler.close_chat_session(session_id)

# Initialize FastAPI app with lifespan
app = FastAPI(title="Agent Manager", lifespan=lifespan)

# Serve static files (CSS, JS)
app.mount("/static", StaticFiles(directory="web/static"), name="static")

@app.get("/")
async def get_chat_page():
    """Serve the main chat page"""
    with open("web/index.html", "r") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time chat with session support"""
    
    # Check if this session should be accepted
    session_info = chat_manager.get_session_info(session_id)
    has_meaningful_data = (session_info['has_history'] and session_info['history_count'] > 0) or \
                         (session_info['has_tasks'] and session_info['task_count'] > 0)
    has_process = session_info['has_process']
    
    # Accept sessions with meaningful data OR active processes
    # Also accept very recent timestamp-based sessions (within 30 seconds of creation)
    is_recent_session = False
    try:
        session_timestamp = int(session_id)
        import time
        current_time = int(time.time() * 1000)
        is_recent_session = (current_time - session_timestamp) < 30000  # 30 seconds
    except ValueError:
        # Not a timestamp-based session ID
        pass
    
    if not has_meaningful_data and not has_process and not is_recent_session:
        # Reject old empty sessions
        logger.info(f"[WEB] Rejecting connection to old empty session {session_id}")
        await websocket.close(code=4004, reason="Session not found")
        return
    
    await chat_manager.connect_session(websocket, session_id)
    try:
        while True:
            # Receive message from client
            message_text = await websocket.receive_text()
            
            # Parse message (could be JSON with metadata)
            try:
                message_data = json.loads(message_text)
                if isinstance(message_data, dict) and 'message' in message_data:
                    actual_message = message_data['message']
                else:
                    actual_message = message_text
            except:
                actual_message = message_text
            
            # Log user input
            logger.info(f"[USER] Session {session_id}: {actual_message}")
            
            # Create user message
            user_message = ChatMessage(
                message=actual_message,
                timestamp=datetime.now().isoformat(),
                sender="user"
            )
            
            # Broadcast user message to this session
            await chat_manager.broadcast_to_session(session_id, user_message)
            
            # Get AI response for this session
            ai_response = await chat_manager.ask_ai(session_id, actual_message)
            
            # Log AI response
            logger.info(f"[AI] Session {session_id}: {ai_response[:100]}{'...' if len(ai_response) > 100 else ''}")
            
            # Create AI message
            ai_message = ChatMessage(
                message=ai_response,
                timestamp=datetime.now().isoformat(),
                sender="ai"
            )
            
            # Broadcast AI response to this session
            await chat_manager.broadcast_to_session(session_id, ai_message)
            
    except WebSocketDisconnect:
        chat_manager.disconnect_session(websocket, session_id)

# API Routes with /api prefix
@app.post("/api/sessions/{session_id}/tasks")
async def create_task(session_id: str, request: ScheduleRequest):
    """Create a new scheduled task for specific session"""
    success, message = scheduler.schedule_task(session_id, request.message, request.schedule_spec)
    
    if success:
        if not scheduler.scheduler_running:
            scheduler.scheduler_running = True
            asyncio.create_task(scheduler.run_scheduler())
        logger.info(f"[API] POST /api/sessions/{session_id}/tasks - Task scheduled")
        return {"success": True, "message": message}
    else:
        logger.info(f"[API] POST /api/sessions/{session_id}/tasks - Failed: {message}")
        raise HTTPException(status_code=400, detail=message)

@app.get("/api/sessions/{session_id}/tasks")
async def get_session_tasks(session_id: str):
    """Get scheduled tasks for specific session"""
    tasks = scheduler.get_scheduled_tasks(session_id)
    logger.info(f"[API] GET /api/sessions/{session_id}/tasks - Returned {len(tasks)} tasks")
    return {"tasks": tasks}

@app.get("/api/tasks")
async def get_all_tasks():
    """Get all scheduled tasks across all sessions"""
    tasks = scheduler.get_scheduled_tasks()
    logger.info(f"[API] GET /api/tasks - Returned {len(tasks)} total tasks")
    return {"tasks": tasks}

@app.delete("/api/sessions/{session_id}/tasks")
async def clear_session_tasks(session_id: str):
    """Clear all scheduled tasks for specific session"""
    count = scheduler.clear_scheduled_tasks(session_id)
    logger.info(f"[API] DELETE /api/sessions/{session_id}/tasks - Cleared {count} tasks")
    return {"message": f"Cleared {count} scheduled tasks for session {session_id}"}

@app.get("/api/sessions/{session_id}/status")
async def get_session_status(session_id: str):
    """Get status for specific session"""
    task_count = len(scheduler.scheduled_tasks.get(session_id, []))
    logger.info(f"[API] GET /api/sessions/{session_id}/status - Session active: {session_id in scheduler.chat_sessions}")
    return {
        "session_id": session_id,
        "chat_session_active": session_id in scheduler.chat_sessions,
        "scheduler_running": scheduler.scheduler_running,
        "task_count": task_count,
        "running": scheduler.running
    }

@app.get("/api/status")
async def get_global_status():
    """Get global scheduler status"""
    total_tasks = sum(len(tasks) for tasks in scheduler.scheduled_tasks.values())
    active_sessions = list(scheduler.chat_sessions.keys())
    logger.info(f"[API] GET /api/status - {len(active_sessions)} active sessions, {total_tasks} total tasks")
    return {
        "active_sessions": active_sessions,
        "scheduler_running": scheduler.scheduler_running,
        "total_task_count": total_tasks,
        "running": scheduler.running
    }

@app.post("/api/sessions/new")
async def create_new_session():
    """Create a new session with chat process"""
    import time
    new_session_id = str(int(time.time() * 1000))  # Timestamp-based ID
    
    # Create the chat process for this session
    success = await scheduler.create_chat_session(new_session_id)
    
    if success:
        logger.info(f"[API] POST /api/sessions/new - Created session {new_session_id}")
        return {"session_id": new_session_id, "success": True}
    else:
        raise HTTPException(status_code=500, detail="Failed to create new session")

@app.get("/api/sessions")
async def get_available_sessions():
    """Get all available sessions that can be recovered"""
    available_sessions = chat_manager.get_available_sessions()
    session_info = []
    
    for session_id in available_sessions:
        info = chat_manager.get_session_info(session_id)
        session_info.append(info)
    
    logger.info(f"[API] GET /api/sessions - Listed {len(available_sessions)} available sessions")
    return {
        "sessions": session_info,
        "total_sessions": len(available_sessions)
    }

@app.get("/api/sessions/{session_id}")
async def get_session_info(session_id: str):
    """Get detailed information about a specific session"""
    info = chat_manager.get_session_info(session_id)
    
    if not (info['has_history'] or info['has_process'] or info['has_tasks']):
        logger.info(f"[API] GET /api/sessions/{session_id} - Session not found")
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
    # Get chat history
    history = chat_manager.chat_history.get(session_id, [])
    
    # Get scheduled tasks
    tasks = scheduler.get_scheduled_tasks(session_id)
    
    logger.info(f"[API] GET /api/sessions/{session_id} - {len(history)} messages, {len(tasks)} tasks")
    return {
        "session_info": info,
        "chat_history": [msg.model_dump() for msg in history],
        "scheduled_tasks": tasks
    }

@app.delete("/api/sessions/{session_id}")
async def cleanup_session(session_id: str):
    """Manually cleanup a specific session (remove from server memory)"""
    info = chat_manager.get_session_info(session_id)
    _ = info  # Retrieved for potential future use
    
    # Allow deletion of any session, even if it appears empty
    # (it might have just been created or have minimal data)
    
    logger.info(f"[API] DELETE /api/sessions/{session_id} - Cleaning up session")
    
    # Close chat process
    await scheduler.close_chat_session(session_id)
    
    # Remove chat history
    if session_id in chat_manager.chat_history:
        del chat_manager.chat_history[session_id]
    
    # Remove from active connections if present
    if session_id in chat_manager.active_connections:
        del chat_manager.active_connections[session_id]
    
    return {"message": f"Session {session_id} cleaned up from server memory"}

@app.get("/api/debug/{enable}")
async def toggle_debug(enable: bool):
    """Toggle debug mode"""
    global DEBUG_MODE
    DEBUG_MODE = enable
    scheduler.debug_mode = enable
    
    if enable:
        logger.setLevel(logging.DEBUG)
        logger.info("[DEBUG] Debug mode enabled")
    else:
        logger.setLevel(logging.INFO)
        logger.info("[WEB] Debug mode disabled")
    
    logger.info(f"[API] GET /api/debug/{enable} - Debug mode {'enabled' if enable else 'disabled'}")
    return {"debug_mode": DEBUG_MODE}

def main():
    """Main entry point"""
    import uvicorn
    logger.info("Starting Agent Manager...")
    logger.info("Web Interface: http://127.0.0.1:8000")
    logger.info("Press Ctrl+C to stop")
    
    # Configure uvicorn with custom logging
    from uvicorn.config import LOGGING_CONFIG
    log_config = LOGGING_CONFIG.copy()
    log_config["formatters"]["default"]["fmt"] = "[WEB] %(message)s"
    log_config["formatters"]["access"]["fmt"] = "[WEB] %(message)s"
    
    uvicorn.run(
        app, 
        host="127.0.0.1", 
        port=8000,
        log_config=log_config,
        access_log=False,
        log_level="warning"
    )

if __name__ == "__main__":
    main()