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
    """Manages chat sessions and message storage"""
    
    def __init__(self, scheduler: TaskScheduler):
        self.scheduler = scheduler
        self.chat_history: Dict[str, List[ChatMessage]] = {}  # agent_session_id -> [messages]
        self.web_session_agents: Dict[str, List[str]] = {}  # web_session_id -> [agent_session_ids]
        
    def ensure_session(self, agent_session_id: str, web_session_id: str = None):
        """Ensure session exists and is properly initialized"""
        # Initialize chat history if it doesn't exist
        if agent_session_id not in self.chat_history:
            self.chat_history[agent_session_id] = []
        
        # If web_session_id provided, ensure this agent session is assigned to it
        if web_session_id:
            self.assign_agent_to_web_session(web_session_id, agent_session_id)
    
    def store_message(self, session_id: str, message: ChatMessage):
        """Store message in session history directly"""
        # Ensure session_id is always a string for consistent dictionary keys
        session_key = str(session_id)
        
        # Store message in session history
        if session_key not in self.chat_history:
            self.chat_history[session_key] = []
        
        self.chat_history[session_key].append(message)
        
        # Keep only last N messages per session
        max_history = get_config("limits.max_chat_history_per_session")
        if len(self.chat_history[session_key]) > max_history:
            self.chat_history[session_key] = self.chat_history[session_key][-max_history:]
        
        logger.info(f"Stored message for session '{session_key}'. Total messages: {len(self.chat_history[session_key])}")
    

    async def ask_ai(self, session_id: str, question: str, stream_callback=None) -> str:
        """Send question to AI and get response for specific session with optional streaming"""
        return await self.scheduler.agent_ask_async(session_id, question, "user", stream_callback)
    
    async def process_scheduled_message(self, session_id: str, message: str) -> str:
        """Process scheduled message using the same flow as manual user input"""
        # Create the session if it doesn't exist
        if session_id not in self.scheduler.chat_sessions:
            success = await self.scheduler.create_chat_session(session_id)
            if not success:
                raise Exception("Failed to create chat session")
        
        # Store user message directly in chat history (same as /web/chat endpoint)
        user_msg = ChatMessage(
            message=f"[AGENT] {message}",
            sender="user",
            timestamp=datetime.now().isoformat()
        )
        
        if session_id not in self.chat_history:
            self.chat_history[session_id] = []
        self.chat_history[session_id].append(user_msg)
        
        truncate_len = get_config("limits.message_truncation_length")
        logger.info(f"Scheduled message stored for session {session_id}: {message[:truncate_len]}...")
        
        # Get AI response using the same method as manual input
        try:
            response = await self.ask_ai(session_id, message)
            
            if response and response.strip():
                # Store AI response directly in chat history (same as /web/chat endpoint)
                ai_msg = ChatMessage(
                    message=response,
                    sender="assistant",
                    timestamp=datetime.now().isoformat()
                )
                self.chat_history[session_id].append(ai_msg)
                
                logger.info(f"Scheduled AI response stored for session {session_id}: {response[:truncate_len]}...")
                return response
            else:
                logger.warning(f"Empty response for scheduled message in session {session_id}")
                return "No response received"
                
        except Exception as e:
            logger.error(f"Error processing scheduled message for session {session_id}: {e}")
            # Store error message in chat history
            error_msg = ChatMessage(
                message=f"Error processing scheduled message: {str(e)}",
                timestamp=datetime.now().isoformat(),
                sender="system"
            )
            self.chat_history[session_id].append(error_msg)
            return f"Error: {str(e)}"
    
    def store_scheduled_question(self, session_id: str, question: str):
        """Store scheduled question in session history"""
        scheduled_message = ChatMessage(
            message=f"[SCHEDULED] {question}",
            timestamp=datetime.now().isoformat(),
            sender="user"
        )
        self.store_message(session_id, scheduled_message)
    
    def store_ai_response(self, session_id: str, response: str):
        """Store AI response in session history"""
        if response:
            ai_message = ChatMessage(
                message=response,
                timestamp=datetime.now().isoformat(),
                sender="assistant"
            )
            self.store_message(session_id, ai_message)
    
    def store_scheduled_message(self, session_id: str, question: str, response: str):
        """Store scheduled message and response in session history"""
        self.store_scheduled_question(session_id, question)
        self.store_ai_response(session_id, response)
    
    def get_active_sessions(self):
        """Get list of active session IDs"""
        return list(self.chat_history.keys())
    
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
            'is_connected': False  # HTTP-only, no persistent connections
        }
    
    
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
        # Ensure both IDs are strings to avoid integer/string key mismatches
        web_session_id = str(web_session_id)
        agent_session_id = str(agent_session_id)
        
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
    
    # Base path will be determined dynamically from X-Forwarded-Prefix header
    # No longer using BASE_PATH environment variable
    
    # Create app - FastAPI will handle X-Forwarded headers automatically
    app = FastAPI(
        title="Agent Manager"
    )
    
    # Serve static files (CSS, JS) - mount at root since root_path handles the prefix
    app.mount("/static", StaticFiles(directory="web"), name="static")

    @app.get("/")
    async def get_chat_page(request: Request):
        """Serve the main chat page"""
        # Main page accessed
        with open("web/index.html", "r") as f:
            html_content = f.read()
        
        # Get base path from X-Forwarded-Prefix header
        base_path = request.headers.get('X-Forwarded-Prefix', '')
        
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

    # Using simple HTTP request/response pattern

    @app.post("/web/chat")
    async def chat_endpoint(request: Request):
        """Simple HTTP request/response chat endpoint - direct message storage"""
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
        
        # Ensure session_id is always string for consistency
        session_id = str(session_id)
        
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
        
        # Store user message directly in chat history
        user_msg = ChatMessage(
            message=message,
            sender="user",
            timestamp=datetime.now().isoformat()
        )
        
        if session_id not in chat_manager.chat_history:
            chat_manager.chat_history[session_id] = []
        chat_manager.chat_history[session_id].append(user_msg)
        
        truncate_len = get_config("limits.message_truncation_length")
        logger.info(f"User message stored for session {session_id}: {message[:truncate_len]}...")
        
        # Get AI response synchronously
        try:
            response = await chat_manager.ask_ai(session_id, message)
            
            if response and response.strip():
                # Store AI response directly in chat history
                ai_msg = ChatMessage(
                    message=response,
                    sender="assistant",
                    timestamp=datetime.now().isoformat()
                )
                chat_manager.chat_history[session_id].append(ai_msg)
                
                logger.info(f"AI response stored for session {session_id}: {response[:truncate_len]}...")
                
                # Return acknowledgment only - AI response will be sent via SSE
                return chat_manager.make_response_with_session({
                    "status": "success",
                    "message": "Message processed"
                }, web_session_id, request)
            else:
                return chat_manager.make_response_with_session({
                    "status": "error",
                    "message": "Empty response from AI"
                }, web_session_id, request)
                
        except Exception as e:
            logger.error(f"Error processing message for session {session_id}: {e}")
            error_msg = ChatMessage(
                message=f"Error: {str(e)}",
                timestamp=datetime.now().isoformat(),
                sender="system"
            )
            chat_manager.chat_history[session_id].append(error_msg)
            
            return chat_manager.make_response_with_session({
                "status": "error",
                "message": str(e)
            }, web_session_id, request)

    # Set reference so scheduler can call the same chat processing function
    scheduler.chat_endpoint_func = chat_endpoint
    
    

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
    async def stream_session_tasks(session_id: str):
        """Stream scheduled tasks and new messages for a specific session via SSE"""
        
        async def event_stream():
            # Start with current message count to avoid re-sending existing messages
            current_messages = chat_manager.chat_history.get(session_id, [])
            last_sent_message_count = len(current_messages)
            
            while True:
                try:
                    # Get current tasks
                    tasks = scheduler.get_scheduled_tasks(session_id)
                    
                    # Send task updates
                    tasks_data = {"type": "tasks", "data": tasks}
                    yield f"data: {json.dumps(tasks_data)}\n\n"
                    
                    # Check for new messages
                    current_messages = chat_manager.chat_history.get(session_id, [])
                    current_count = len(current_messages)
                    
                    if current_count > last_sent_message_count:
                        logger.info(f"SSE detected new messages for session {session_id}: {current_count} > {last_sent_message_count}")
                        # Send new messages since last check
                        new_messages = current_messages[last_sent_message_count:]
                        messages_data = {"type": "messages", "data": [msg.__dict__ for msg in new_messages]}
                        yield f"data: {json.dumps(messages_data)}\n\n"
                        last_sent_message_count = current_count
                    
                    # Wait before next update
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"SSE stream error for session {session_id}: {e}")
                    break
        
        logger.info(f"SSE stream started for session {session_id}")
        return StreamingResponse(event_stream(), media_type="text/event-stream", 
                               headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

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
            "chat_history_sessions": len(chat_manager.chat_history),
            "available_sessions": chat_manager.get_available_sessions(web_session_id),
            "web_session_id": web_session_id
        }
        logger.info(f"GET /web/status - {status['total_sessions']} sessions, {status['chat_history_sessions']} chat histories")
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
        
        # Get chat history from web interface - fix the core broadcast issue instead
        chat_history = chat_manager.chat_history.get(session_id, [])
        
        # If no history found with string key, try integer key as fallback  
        if not chat_history:
            try:
                int_session_id = int(session_id)
                chat_history = chat_manager.chat_history.get(int_session_id, [])
                if chat_history:
                    # Move history to string key for consistency
                    chat_manager.chat_history[session_id] = chat_history
                    del chat_manager.chat_history[int_session_id]
                    logger.info(f"Migrated history from integer key {int_session_id} to string key '{session_id}'")
            except ValueError:
                pass  # session_id is not a valid integer
        
        
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
        
        # HTTP-only architecture, no persistent connections to clean
        
        # Remove session from web session mappings
        for web_session_id, agent_sessions in chat_manager.web_session_agents.items():
            if session_id in agent_sessions:
                agent_sessions.remove(session_id)
                logger.info(f"Removed session {session_id} from web session {web_session_id}")
                break
        
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