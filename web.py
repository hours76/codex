"""
Web interface and API components for the agent system
"""

from fastapi import FastAPI, HTTPException, Request, Cookie
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from typing import Dict, List, Set
import json
import time
import os
import uuid
from datetime import datetime
from typing import Optional
import logging
import asyncio

from core import TaskScheduler
from models import ChatMessage, ScheduleRequest, get_config
from monitor import get_task_monitor

logger = logging.getLogger("agent")

class ChatManager:
    """Manages SSE connections and message broadcasting"""
    
    def __init__(self, scheduler: TaskScheduler):
        self.scheduler = scheduler
        self.chat_history: Dict[str, List[ChatMessage]] = {}  # agent_session_id -> [messages]
        self.web_session_agents: Dict[str, List[str]] = {}  # web_session_id -> [agent_session_ids]
        self.sse_queues: Dict[str, List[asyncio.Queue]] = {}  # agent_session_id -> [queues for SSE]
        
    def ensure_session(self, agent_session_id: str, web_session_id: str = None):
        """Ensure session exists and is properly initialized"""
        # Initialize chat history if it doesn't exist
        if agent_session_id not in self.chat_history:
            self.chat_history[agent_session_id] = []
        
        # If web_session_id provided, ensure this agent session is assigned to it
        if web_session_id:
            self.assign_agent_to_web_session(web_session_id, agent_session_id)
    
    async def broadcast_to_session(self, session_id: str, message: ChatMessage):
        """Broadcast message to specific session"""
        # Store message in session history
        if session_id not in self.chat_history:
            self.chat_history[session_id] = []
        
        self.chat_history[session_id].append(message)
        
        # Keep only last N messages per session
        max_history = get_config("limits.max_chat_history_per_session")
        if len(self.chat_history[session_id]) > max_history:
            self.chat_history[session_id] = self.chat_history[session_id][-max_history:]
        
        # Send to all SSE queues for this session
        await self.broadcast_to_sse(session_id, message)
    

    async def ask_ai(self, session_id: str, question: str) -> str:
        """Send question to AI and get response for specific session"""
        return await self.scheduler.agent_ask_async(session_id, question, "user")
    
    async def broadcast_scheduled_question(self, session_id: str, question: str):
        """Broadcast scheduled question to specific session"""
        scheduled_message = ChatMessage(
            message=f"[SCHEDULED] {question}",
            timestamp=datetime.now().isoformat(),
            sender="scheduled"
        )
        await self.broadcast_to_session(session_id, scheduled_message)
    
    async def broadcast_ai_response(self, session_id: str, response: str):
        """Broadcast AI response to specific session"""
        if response:
            ai_message = ChatMessage(
                message=response,
                timestamp=datetime.now().isoformat(),
                sender="ai"
            )
            await self.broadcast_to_session(session_id, ai_message)
    
    async def broadcast_scheduled_message(self, session_id: str, question: str, response: str):
        """Broadcast scheduled message and response to specific session (legacy method)"""
        await self.broadcast_scheduled_question(session_id, question)
        await self.broadcast_ai_response(session_id, response)
    
    def get_active_sessions(self):
        """Get list of active session IDs"""
        return list(self.active_connections.keys())
    
    def get_available_sessions(self, web_session_id: str = None):
        """Get list of available sessions, optionally filtered by web session"""
        chat_sessions = set(self.chat_history.keys())
        scheduler_sessions = set(self.scheduler.scheduled_tasks.keys())
        process_sessions = set(self.scheduler.chat_sessions.keys())
        all_sessions = list(chat_sessions.union(scheduler_sessions).union(process_sessions))
        
        # Filter by web session if provided
        if web_session_id:
            web_owned_sessions = set(self.get_agent_sessions_for_web_session(web_session_id))
            filtered_sessions = [s for s in all_sessions if s in web_owned_sessions]
            # Remove duplicates while preserving order
            seen = set()
            unique_sessions = []
            for s in filtered_sessions:
                # Ensure session ID is a string to avoid type mismatches
                s_str = str(s)
                if s_str not in seen:
                    seen.add(s_str)
                    unique_sessions.append(s_str)
            return unique_sessions
        
        return all_sessions
    
    def get_session_info(self, session_id: str):
        """Get session information for recovery"""
        return {
            'session_id': session_id,
            'has_history': session_id in self.chat_history,
            'history_count': len(self.chat_history.get(session_id, [])),
            'has_process': session_id in self.scheduler.chat_sessions,
            'has_tasks': session_id in self.scheduler.scheduled_tasks,
            'task_count': len(self.scheduler.scheduled_tasks.get(session_id, [])),
            'is_connected': session_id in self.sse_queues
        }
    
    def add_sse_queue(self, session_id: str) -> asyncio.Queue:
        """Add a new SSE queue for a session"""
        if session_id not in self.sse_queues:
            self.sse_queues[session_id] = []
        queue = asyncio.Queue()
        self.sse_queues[session_id].append(queue)
        return queue
    
    def remove_sse_queue(self, session_id: str, queue: asyncio.Queue):
        """Remove an SSE queue for a session"""
        if session_id in self.sse_queues:
            if queue in self.sse_queues[session_id]:
                self.sse_queues[session_id].remove(queue)
            if not self.sse_queues[session_id]:
                del self.sse_queues[session_id]
    
    async def broadcast_to_sse(self, session_id: str, message: ChatMessage):
        """Broadcast message to all SSE connections for a session"""
        if session_id in self.sse_queues:
            for queue in self.sse_queues[session_id]:
                await queue.put(message.model_dump_json())
    
    def get_web_session_id(self, request: Request) -> str:
        """Get or create web session ID from cookies (user identification)"""
        # Check if we already determined the session ID for this request
        if hasattr(request.state, 'web_session_id'):
            return request.state.web_session_id
            
        web_session_id = request.cookies.get('web_session')
        
        if web_session_id:
            # Validate existing session ID
            try:
                uuid.UUID(web_session_id)
                # Cache valid session ID for this request
                request.state.web_session_id = web_session_id
                logger.info(f"Reusing existing web session: {web_session_id[:8]}...")
                return web_session_id
            except ValueError:
                pass  # Invalid session ID, create new one
        
        # Generate new web session ID if none exists or invalid
        web_session_id = str(uuid.uuid4())
        # Cache new session ID for this request
        request.state.web_session_id = web_session_id
        logger.info(f"Created NEW web session: {web_session_id[:8]}...")
        return web_session_id
    
    def assign_agent_to_web_session(self, web_session_id: str, agent_session_id: str):
        """Assign an agent session to a web session"""
        if web_session_id not in self.web_session_agents:
            self.web_session_agents[web_session_id] = []
        if agent_session_id not in self.web_session_agents[web_session_id]:
            self.web_session_agents[web_session_id].append(agent_session_id)
    
    def get_agent_sessions_for_web_session(self, web_session_id: str) -> List[str]:
        """Get all agent sessions owned by a web session"""
        return self.web_session_agents.get(web_session_id, [])
    
    def remove_agent_from_web_session(self, web_session_id: str, agent_session_id: str):
        """Remove an agent session from a web session"""
        if web_session_id in self.web_session_agents:
            if agent_session_id in self.web_session_agents[web_session_id]:
                self.web_session_agents[web_session_id].remove(agent_session_id)
            # Clean up empty web sessions
            if not self.web_session_agents[web_session_id]:
                del self.web_session_agents[web_session_id]
    
    def make_response_with_session(self, data: dict, web_session_id: str, request: Request, status: int = 200) -> JSONResponse:
        """Create response with web session cookie if needed"""
        response = JSONResponse(content=data, status_code=status)
        
        # Set cookie if it doesn't exist or differs from current session
        current_cookie = request.cookies.get('web_session')
        if current_cookie != web_session_id:
            response.set_cookie(
                'web_session',
                web_session_id,
                max_age=86400,  # 24 hours
                httponly=True,
                secure=False,  # Set to True in production with HTTPS
                samesite='lax',
                domain=None  # Allow cookie for both localhost and 127.0.0.1
            )
        
        return response

def create_app(scheduler: TaskScheduler, chat_manager: ChatManager) -> FastAPI:
    """Create and configure FastAPI application"""
    
    # Get base path from environment variable, fallback to config
    base_path = os.environ.get('BASE_PATH', get_config("server.base_path", "")).rstrip('/')
    if base_path and not base_path.startswith('/'):
        base_path = '/' + base_path
    
    # Log the base path configuration
    logger.info(f"FastAPI app initialized with BASE_PATH: '{base_path or '/'}'")
    
    # Create app with root_path for reverse proxy support
    app = FastAPI(
        title="Agent Manager",
        root_path=base_path
    )
    
    # Serve static files (CSS, JS) - mount at root since root_path handles the prefix
    app.mount("/static", StaticFiles(directory="web"), name="static")

    @app.get("/")
    async def get_chat_page():
        """Serve the main chat page"""
        with open("web/index.html", "r") as f:
            html_content = f.read()
        
        # Make HTML content path-aware
        # Fix static file links and inject base path
        html_content = html_content.replace('href="/static/', f'href="{base_path}/static/')
        html_content = html_content.replace('src="/static/', f'src="{base_path}/static/')
        
        # Add base path script for JavaScript
        base_path_script = f"""<script>
            window.BASE_PATH = '{base_path}';
        </script>"""
        
        # Insert before closing </head> tag
        html_content = html_content.replace('</head>', f'{base_path_script}\n</head>')
        
        return HTMLResponse(content=html_content)

    # WebSocket endpoint removed - using SSE only

    @app.post("/web/chat")
    async def chat_endpoint(request: Request):
        """Single POST endpoint that returns SSE stream like chat-server"""
        # Get web session ID for validation
        web_session_id = chat_manager.get_web_session_id(request)
        
        # Parse message from request body
        data = await request.json()
        message = data.get("message", "").strip()
        session_id = data.get("session_id", "")
        
        if not message:
            raise HTTPException(status_code=400, detail="Message cannot be empty")
        
        if not session_id:
            raise HTTPException(status_code=400, detail="Session ID required")
        
        # Verify this agent session belongs to this web session
        owned_sessions = chat_manager.get_agent_sessions_for_web_session(web_session_id)
        if session_id not in owned_sessions:
            # Auto-assign if not assigned yet
            chat_manager.assign_agent_to_web_session(web_session_id, session_id)
        
        # Create the session if it doesn't exist
        if session_id not in scheduler.chat_sessions:
            success = await scheduler.create_chat_session(session_id)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to create chat session")
        
        async def event_generator():
            try:
                # Send user message first
                user_msg = ChatMessage(
                    message=message,
                    sender="user",
                    type="user",
                    timestamp=datetime.now().isoformat()
                )
                yield f"data: {user_msg.model_dump_json()}\n\n"
                
                # Store in history
                if session_id not in chat_manager.chat_history:
                    chat_manager.chat_history[session_id] = []
                chat_manager.chat_history[session_id].append(user_msg)
                
                # Get AI response
                response = await chat_manager.ask_ai(session_id, message)
                
                if response and response.strip():
                    # Send AI response
                    ai_msg = ChatMessage(
                        message=response,
                        sender="assistant", 
                        type="assistant",
                        timestamp=datetime.now().isoformat()
                    )
                    yield f"data: {ai_msg.model_dump_json()}\n\n"
                    
                    # Store in history
                    chat_manager.chat_history[session_id].append(ai_msg)
                    
                    # Send done signal
                    yield f"data: {{\"done\": true}}\n\n"
                else:
                    yield f"data: {{\"error\": \"Empty response from AI\"}}\n\n"
                    
            except Exception as e:
                logger.error(f"Error in chat for session {session_id}: {e}")
                yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
        
        response = StreamingResponse(event_generator(), media_type="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        # Add session cookie
        response.set_cookie(
            key="web_session",
            value=web_session_id,
            max_age=24 * 3600,
            httponly=True,
            samesite="lax"
        )
        return response
    
    @app.get("/web/sessions/{session_id}/stream")
    async def stream_messages(session_id: str, request: Request):
        """SSE endpoint for streaming messages"""
        # Get web session ID for validation
        web_session_id = chat_manager.get_web_session_id(request)
        
        # Verify this agent session belongs to this web session
        owned_sessions = chat_manager.get_agent_sessions_for_web_session(web_session_id)
        if session_id not in owned_sessions:
            # Auto-assign if not assigned yet
            chat_manager.assign_agent_to_web_session(web_session_id, session_id)
        
        # Create an SSE queue for this connection
        queue = chat_manager.add_sse_queue(session_id)
        
        async def event_generator():
            try:
                # Send existing chat history first
                if session_id in chat_manager.chat_history:
                    for message in chat_manager.chat_history[session_id]:
                        yield f"data: {message.model_dump_json()}\n\n"
                
                # Then send new messages as they arrive
                while True:
                    message = await queue.get()
                    yield f"data: {message}\n\n"
            except asyncio.CancelledError:
                chat_manager.remove_sse_queue(session_id, queue)
                raise
            except Exception as e:
                chat_manager.remove_sse_queue(session_id, queue)
                logger.error(f"SSE error for session {session_id}: {e}")
                raise
        
        response = StreamingResponse(event_generator(), media_type="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        # Add session cookie directly to StreamingResponse
        response.set_cookie(
            key="web_session",
            value=web_session_id,
            max_age=24 * 3600,  # 24 hours
            httponly=True,
            samesite="lax"
        )
        return response
    
    @app.post("/web/sessions/{session_id}/messages")
    async def send_message(session_id: str, request: Request):
        """HTTP POST endpoint to send messages"""
        # Get web session ID for validation
        web_session_id = chat_manager.get_web_session_id(request)
        
        # Verify this agent session belongs to this web session
        owned_sessions = chat_manager.get_agent_sessions_for_web_session(web_session_id)
        if session_id not in owned_sessions:
            raise HTTPException(status_code=403, detail="Session not owned by this user")
        
        # Parse message from request body
        data = await request.json()
        message = data.get("message", "").strip()
        
        if not message:
            raise HTTPException(status_code=400, detail="Message cannot be empty")
        
        # Create the session if it doesn't exist
        if session_id not in scheduler.chat_sessions:
            success = await scheduler.create_chat_session(session_id)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to create chat session")
        
        # Add user message to history and broadcast
        user_msg = ChatMessage(
            message=message,
            sender="user",
            type="user",
            timestamp=datetime.now().isoformat()
        )
        await chat_manager.broadcast_to_session(session_id, user_msg)
        
        truncate_len = get_config("limits.message_truncation_length")
        logger.info(f"User message from session {session_id}: {message[:truncate_len]}...")
        
        # Get AI response
        try:
            response = await chat_manager.ask_ai(session_id, message)
            
            if response and response.strip():
                # Add AI response to history and broadcast
                ai_msg = ChatMessage(
                    message=response,
                    sender="assistant",
                    type="assistant",
                    timestamp=datetime.now().isoformat()
                )
                await chat_manager.broadcast_to_session(session_id, ai_msg)
                
                logger.info(f"AI response to session {session_id}: {response[:truncate_len]}...")
                
                return chat_manager.make_response_with_session({
                    "status": "success",
                    "response": response
                }, web_session_id, request)
            else:
                return chat_manager.make_response_with_session({
                    "status": "error",
                    "message": "Empty response from AI"
                }, web_session_id, request)
                
        except Exception as e:
            logger.error(f"Error processing message for session {session_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/web/sessions/{session_id}/schedule")
    async def create_task(session_id: str, request: ScheduleRequest):
        """Schedule a task for a specific session"""
        success, message = scheduler.schedule_task(session_id, request.message, request.schedule_spec)
        
        if success:
            # Start scheduler if not running
            if not scheduler.scheduler_running:
                scheduler.scheduler_running = True
                import asyncio
                asyncio.create_task(scheduler.run_scheduler())
            
            truncate_len = get_config("limits.message_truncation_length")
            logger.info(f"POST /web/sessions/{session_id}/schedule - Task scheduled")
            return {"status": "scheduled", "message": message}
        else:
            truncate_len = get_config("limits.message_truncation_length")
            logger.warning(f"POST /web/sessions/{session_id}/schedule - Failed: {message[:truncate_len]}...")
            raise HTTPException(status_code=400, detail=message)

    @app.get("/web/sessions/{session_id}/tasks")
    async def get_session_tasks(session_id: str):
        """Get scheduled tasks for a specific session"""
        tasks = scheduler.get_scheduled_tasks(session_id)
        logger.info(f"GET /web/sessions/{session_id}/tasks - Returned {len(tasks)} tasks")
        return {"tasks": tasks}

    @app.get("/web/tasks")
    async def get_all_tasks():
        """Get all scheduled tasks across all sessions"""
        tasks = scheduler.get_scheduled_tasks()
        logger.info(f"GET /web/tasks - Returned {len(tasks)} tasks across all sessions")
        return {"tasks": tasks}

    @app.delete("/web/sessions/{session_id}/tasks")
    async def clear_session_tasks(session_id: str):
        """Clear all scheduled tasks for a specific session"""
        count = scheduler.clear_scheduled_tasks(session_id)
        logger.info(f"DELETE /web/sessions/{session_id}/tasks - Cleared {count} tasks")
        return {"cleared": count, "message": f"Cleared {count} tasks for session {session_id}"}

    @app.delete("/web/sessions/{session_id}/tasks/{task_index}")
    async def delete_single_task(session_id: str, task_index: int):
        """Delete a specific scheduled task by index for a session"""
        success, message = scheduler.delete_scheduled_task(session_id, task_index)
        
        if success:
            logger.info(f"DELETE /web/sessions/{session_id}/tasks/{task_index} - {message}")
            return {"success": True, "message": message}
        else:
            logger.warning(f"DELETE /web/sessions/{session_id}/tasks/{task_index} - Failed: {message}")
            raise HTTPException(status_code=400, detail=message)

    @app.get("/web/sessions/{session_id}")
    async def get_session_status(session_id: str):
        """Get status information for a specific session"""
        session_info = chat_manager.get_session_info(session_id)
        
        logger.info(f"GET /web/sessions/{session_id} - Status: {session_info['history_count']} messages, {session_info['task_count']} tasks")
        return {
            "session_id": session_id,
            "status": "active" if session_info['is_connected'] else "available",
            "history_count": session_info['history_count'],
            "has_process": session_info['has_process'],
            "task_count": session_info['task_count'],
            "is_connected": session_info['is_connected']
        }

    @app.get("/web/status")
    async def get_global_status(request: Request):
        """Get global system status"""
        # Get web session ID for cookie setting
        web_session_id = chat_manager.get_web_session_id(request)
        
        status = {
            "scheduler_running": scheduler.scheduler_running,
            "task_queue_running": scheduler.running,
            "total_sessions": len(scheduler.chat_sessions),
            "active_sse_queues": len(chat_manager.sse_queues),
            "available_sessions": chat_manager.get_available_sessions(web_session_id),
            "web_session_id": web_session_id
        }
        logger.info(f"GET /web/status - {status['total_sessions']} sessions, {status['active_sse_queues']} SSE queues")
        return chat_manager.make_response_with_session(status, web_session_id, request)

    @app.post("/web/sessions/new")
    async def create_new_session(request: Request):
        """Create a new session with timestamp-based ID"""
        import time
        
        # Get web session ID
        web_session_id = chat_manager.get_web_session_id(request)
        
        # Create new agent session ID
        new_agent_session_id = str(int(time.time() * 1000))  # Timestamp-based ID
        
        # Pre-create the chat session
        success = await scheduler.create_chat_session(new_agent_session_id)
        
        if success:
            # Assign this agent session to the web session
            chat_manager.assign_agent_to_web_session(web_session_id, new_agent_session_id)
            
            logger.info(f"POST /web/sessions/new - Created session {new_agent_session_id} for web session {web_session_id[:8]}...")
            
            response_data = {
                "session_id": new_agent_session_id,
                "status": "created"
            }
            return chat_manager.make_response_with_session(response_data, web_session_id, request)
        else:
            raise HTTPException(status_code=500, detail="Failed to create chat session")

    @app.get("/web/sessions")
    async def get_available_sessions(request: Request):
        """Get list of available sessions for current web session"""
        # Get web session ID for filtering
        web_session_id = chat_manager.get_web_session_id(request)
        
        # Get sessions filtered by web session
        available_sessions = chat_manager.get_available_sessions(web_session_id)
        
        # Get detailed info for each session using local history only
        session_infos = []
        for session_id in available_sessions:
            info = chat_manager.get_session_info(session_id)
            session_infos.append(info)
        
        logger.info(f"GET /web/sessions - Web session {web_session_id[:8]} owns sessions: {available_sessions}")
        logger.info(f"GET /web/sessions - All web_session_agents: {dict(chat_manager.web_session_agents)}")
        logger.info(f"GET /web/sessions - All scheduler sessions: {list(scheduler.chat_sessions.keys())}")
        logger.info(f"GET /web/sessions - Returned {len(session_infos)} sessions for web session {web_session_id[:8]}...")
        
        response_data = {"sessions": session_infos}
        return chat_manager.make_response_with_session(response_data, web_session_id, request)

    @app.get("/web/sessions/{session_id}/info")
    async def get_session_info(session_id: str):
        """Get detailed information about a specific session"""
        
        # Create the session if it doesn't exist (for new session access)
        if session_id not in scheduler.chat_sessions:
            success = await scheduler.create_chat_session(session_id)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to create chat session")
        
        session_info = chat_manager.get_session_info(session_id)
        
        logger.info(f"GET /web/sessions/{session_id}/info - Session info retrieved")
        return {
            "session_id": session_id,
            "created": True,
            **session_info
        }

    @app.get("/web/sessions/{session_id}/history")
    async def get_session_history(session_id: str, request: Request):
        """Get chat history for a specific session with cookie validation"""
        # Get web session ID for access control
        web_session_id = chat_manager.get_web_session_id(request)
        
        # Verify this agent session belongs to this web session
        owned_sessions = chat_manager.get_agent_sessions_for_web_session(web_session_id)
        if session_id not in owned_sessions:
            # Auto-assign if not assigned yet
            chat_manager.assign_agent_to_web_session(web_session_id, session_id)
            owned_sessions = chat_manager.get_agent_sessions_for_web_session(web_session_id)
            if session_id not in owned_sessions:
                logger.warning(f"GET /web/sessions/{session_id}/history - Access denied for web session {web_session_id[:8]}...")
                raise HTTPException(status_code=403, detail="Access denied to this session")
        
        # Get chat history from server memory
        chat_history = chat_manager.chat_history.get(int(session_id), [])
        history_data = [msg.model_dump() for msg in chat_history]
        
        logger.info(f"GET /web/sessions/{session_id}/history - Returned {len(history_data)} messages")
        
        response_data = {
            "session_id": session_id,
            "history": history_data,
            "count": len(history_data)
        }
        return chat_manager.make_response_with_session(response_data, web_session_id, request)

    @app.delete("/web/sessions/{session_id}")
    async def cleanup_session(session_id: str):
        """Manually cleanup a specific session (remove from server memory)"""
        info = chat_manager.get_session_info(session_id)
        _ = info  # Retrieved for potential future use
        
        # Allow deletion of any session, even if it appears empty
        # (it might have just been created or have minimal data)
        
        # Clear tasks for this session
        cleared_tasks = scheduler.clear_scheduled_tasks(session_id)
        
        # Close chat session
        await scheduler.close_chat_session(session_id)
        
        # Clear chat history
        if session_id in chat_manager.chat_history:
            history_count = len(chat_manager.chat_history[session_id])
            del chat_manager.chat_history[session_id]
        else:
            history_count = 0
        
        # Clear any SSE queues for this session
        if session_id in chat_manager.sse_queues:
            del chat_manager.sse_queues[session_id]
        
        logger.info(f"Session {session_id} cleaned up - {cleared_tasks} tasks, {history_count} history entries")
        
        return {
            "session_id": session_id,
            "status": "cleaned",
            "cleared_tasks": cleared_tasks,
            "cleared_history": history_count
        }

    @app.post("/web/debug")
    async def toggle_debug(enable: bool):
        """Toggle debug mode"""
        import models
        models.DEBUG_MODE = enable
        scheduler.debug_mode = enable
        
        # Update all existing chat sessions
        for session in scheduler.chat_sessions.values():
            session.debug_mode = enable
        
        logger.info(f"POST /web/debug - Debug mode {'enabled' if enable else 'disabled'}")
        return {"debug_mode": enable, "message": f"Debug mode {'enabled' if enable else 'disabled'}"}

    @app.post("/web/monitoring")
    async def toggle_monitoring(enable: bool):
        """Toggle task monitoring globally"""
        task_monitor = get_task_monitor()
        task_monitor.set_global_monitoring(enable)
        
        logger.info(f"POST /web/monitoring - Task monitoring {'enabled' if enable else 'disabled'}")
        return {"monitoring_enabled": enable, "message": f"Task monitoring {'enabled' if enable else 'disabled'}"}
    
    @app.get("/web/monitoring")
    async def get_monitoring_status():
        """Get task monitoring status and statistics"""
        task_monitor = get_task_monitor()
        stats = task_monitor.get_monitoring_stats()
        
        logger.info(f"GET /web/monitoring - Retrieved monitoring stats")
        return {
            "monitoring": stats,
            "message": f"Monitoring {'enabled' if stats['monitoring_enabled'] else 'disabled'} for {stats['session_count']} sessions"
        }
    
    @app.post("/web/monitoring/test")
    async def test_monitoring():
        """Test the monitoring system with sample responses"""
        from monitor import test_monitor
        
        try:
            monitor = test_monitor()
            logger.info("POST /web/monitoring/test - Monitoring test completed")
            return {
                "status": "success",
                "message": "Monitoring test completed - check server logs for results",
                "monitoring_enabled": monitor.monitoring_enabled,
                "monitored_sessions": list(monitor.monitored_sessions)
            }
        except Exception as e:
            logger.error(f"POST /web/monitoring/test - Test failed: {e}")
            return {
                "status": "error", 
                "message": f"Test failed: {e}"
            }

    @app.post("/web/task-plans/save")
    async def save_task_plan(plan_name: str = None, session_id: str = None):
        """Save scheduled tasks as a plan - from specific session if provided"""
        success, message = scheduler.save_task_plan(plan_name, session_id)
        
        if success:
            logger.info(f"POST /web/task-plans/save - {message}")
            return {"success": True, "message": message}
        else:
            logger.warning(f"POST /web/task-plans/save - Failed: {message}")
            raise HTTPException(status_code=500, detail=message)

    @app.post("/web/task-plans/{plan_name}/load")
    async def load_task_plan(plan_name: str, session_id: str = None):
        """Load a saved task plan and apply it to target session"""
        success, message = scheduler.load_task_plan(plan_name, session_id)
        
        if success:
            # Start scheduler if not running
            if not scheduler.scheduler_running:
                scheduler.scheduler_running = True
                import asyncio
                asyncio.create_task(scheduler.run_scheduler())
            
            logger.info(f"POST /web/task-plans/{plan_name}/load - {message}")
            return {"success": True, "message": message}
        else:
            logger.warning(f"POST /web/task-plans/{plan_name}/load - Failed: {message}")
            raise HTTPException(status_code=404, detail=message)

    @app.get("/web/task-plans")
    async def get_task_plans():
        """Get list of all saved task plans"""
        plans = scheduler.get_saved_task_plans()
        logger.info(f"GET /web/task-plans - Returned {len(plans)} saved plans")
        return {"plans": plans}

    @app.get("/web/sessions/{session_id}/active-plan")
    async def get_active_plan(session_id: str):
        """Get the active plan for a specific session"""
        active_plan = scheduler.get_active_plan(session_id)
        logger.info(f"GET /web/sessions/{session_id}/active-plan - Active plan: {active_plan}")
        return {"active_plan": active_plan}

    return app