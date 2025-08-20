"""
Web interface and API components for the agent system
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from typing import Dict, List
import json
import time
from datetime import datetime
import logging

from core import TaskScheduler
from models import ChatMessage, ScheduleRequest, get_config
from monitor import get_task_monitor

logger = logging.getLogger("agent")

class ChatManager:
    """Manages WebSocket connections and message broadcasting"""
    
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
        
        # Keep only last N messages per session
        max_history = get_config("limits.max_chat_history_per_session")
        if len(self.chat_history[session_id]) > max_history:
            self.chat_history[session_id] = self.chat_history[session_id][-max_history:]
        
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

def create_app(scheduler: TaskScheduler, chat_manager: ChatManager) -> FastAPI:
    """Create and configure FastAPI application"""
    
    app = FastAPI(title="Agent Manager")
    
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
        
        # Check for timestamp-based sessions and reject very old empty ones
        try:
            # Try to parse session_id as timestamp 
            session_timestamp = int(session_id)
            current_time = int(time.time() * 1000)
            session_timeout = get_config("session.session_timeout_ms")
            is_recent_session = (current_time - session_timestamp) < session_timeout
        except ValueError:
            # Not a timestamp-based session ID
            is_recent_session = True  # Allow non-timestamp session IDs
            
        # Only accept sessions that have data or are recent
        if not has_meaningful_data and not is_recent_session:
            logger.info(f"Rejecting connection to old empty session {session_id}")
            await websocket.close(code=4004, reason="Session too old and empty")
            return
        
        # Accept the session
        await chat_manager.connect_session(websocket, session_id)
        
        try:
            while True:
                data = await websocket.receive_text()
                
                # Skip empty or invalid messages
                if not data or not data.strip():
                    continue
                
                try:
                    message_data = json.loads(data)
                except json.JSONDecodeError:
                    truncate_len = get_config("limits.log_data_truncation_length")
                    logger.warning(f"Invalid JSON received from session {session_id}: {data[:truncate_len]}...")
                    continue
                
                # Check message structure
                if not isinstance(message_data, dict) or "type" not in message_data:
                    truncate_len = get_config("limits.log_data_truncation_length")
                    logger.warning(f"Invalid message structure from session {session_id}: {str(message_data)[:truncate_len]}...")
                    continue
                
                if message_data["type"] == "chat":
                    if "message" not in message_data:
                        logger.warning(f"Chat message missing 'message' field from session {session_id}")
                        continue
                        
                    user_message = message_data["message"]
                    truncate_len = get_config("limits.message_truncation_length")
                    logger.info(f"User message from session {session_id}: {user_message[:truncate_len]}...")
                    
                    # Broadcast user message
                    user_msg = ChatMessage(
                        message=user_message,
                        timestamp=datetime.now().isoformat(),
                        sender="user"
                    )
                    await chat_manager.broadcast_to_session(session_id, user_msg)
                    
                    # Get AI response
                    ai_response = await chat_manager.ask_ai(session_id, user_message)
                    truncate_len = get_config("limits.message_truncation_length")
                    logger.info(f"AI response to session {session_id}: {ai_response[:truncate_len]}...")
                    
                    # Broadcast AI response
                    ai_msg = ChatMessage(
                        message=ai_response,
                        timestamp=datetime.now().isoformat(),
                        sender="ai"
                    )
                    await chat_manager.broadcast_to_session(session_id, ai_msg)
                    
        except WebSocketDisconnect:
            chat_manager.disconnect_session(websocket, session_id)
        except Exception as e:
            logger.error(f"WebSocket error for session {session_id}: {e}")
            chat_manager.disconnect_session(websocket, session_id)
            try:
                await websocket.close(code=1011, reason="Internal server error")
            except:
                pass  # Connection may already be closed

    @app.post("/api/sessions/{session_id}/schedule")
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
            logger.info(f"POST /api/sessions/{session_id}/schedule - Task scheduled")
            return {"status": "scheduled", "message": message}
        else:
            truncate_len = get_config("limits.message_truncation_length")
            logger.warning(f"POST /api/sessions/{session_id}/schedule - Failed: {message[:truncate_len]}...")
            raise HTTPException(status_code=400, detail=message)

    @app.get("/api/sessions/{session_id}/tasks")
    async def get_session_tasks(session_id: str):
        """Get scheduled tasks for a specific session"""
        tasks = scheduler.get_scheduled_tasks(session_id)
        logger.info(f"GET /api/sessions/{session_id}/tasks - Returned {len(tasks)} tasks")
        return {"tasks": tasks}

    @app.get("/api/tasks")
    async def get_all_tasks():
        """Get all scheduled tasks across all sessions"""
        tasks = scheduler.get_scheduled_tasks()
        logger.info(f"GET /api/tasks - Returned {len(tasks)} tasks across all sessions")
        return {"tasks": tasks}

    @app.delete("/api/sessions/{session_id}/tasks")
    async def clear_session_tasks(session_id: str):
        """Clear all scheduled tasks for a specific session"""
        count = scheduler.clear_scheduled_tasks(session_id)
        logger.info(f"DELETE /api/sessions/{session_id}/tasks - Cleared {count} tasks")
        return {"cleared": count, "message": f"Cleared {count} tasks for session {session_id}"}

    @app.delete("/api/sessions/{session_id}/tasks/{task_index}")
    async def delete_single_task(session_id: str, task_index: int):
        """Delete a specific scheduled task by index for a session"""
        success, message = scheduler.delete_scheduled_task(session_id, task_index)
        
        if success:
            logger.info(f"DELETE /api/sessions/{session_id}/tasks/{task_index} - {message}")
            return {"success": True, "message": message}
        else:
            logger.warning(f"DELETE /api/sessions/{session_id}/tasks/{task_index} - Failed: {message}")
            raise HTTPException(status_code=400, detail=message)

    @app.get("/api/sessions/{session_id}")
    async def get_session_status(session_id: str):
        """Get status information for a specific session"""
        session_info = chat_manager.get_session_info(session_id)
        
        logger.info(f"GET /api/sessions/{session_id} - Status: {session_info['history_count']} messages, {session_info['task_count']} tasks")
        return {
            "session_id": session_id,
            "status": "active" if session_info['is_connected'] else "available",
            "history_count": session_info['history_count'],
            "has_process": session_info['has_process'],
            "task_count": session_info['task_count'],
            "is_connected": session_info['is_connected']
        }

    @app.get("/api/status")
    async def get_global_status():
        """Get global system status"""
        status = {
            "scheduler_running": scheduler.scheduler_running,
            "task_queue_running": scheduler.running,
            "total_sessions": len(scheduler.chat_sessions),
            "active_websockets": sum(len(conns) for conns in chat_manager.active_connections.values()),
            "available_sessions": chat_manager.get_available_sessions()
        }
        logger.info(f"GET /api/status - {status['total_sessions']} sessions, {status['active_websockets']} websockets")
        return status

    @app.post("/api/sessions/new")
    async def create_new_session():
        """Create a new session with timestamp-based ID"""
        import time
        new_session_id = str(int(time.time() * 1000))  # Timestamp-based ID
        
        # Pre-create the chat session
        success = await scheduler.create_chat_session(new_session_id)
        
        if success:
            logger.info(f"POST /api/sessions/new - Created session {new_session_id}")
            return {
                "session_id": new_session_id,
                "status": "created",
                "websocket_url": f"/ws/{new_session_id}"
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create chat session")

    @app.get("/api/sessions")
    async def get_available_sessions():
        """Get list of all available sessions"""
        available_sessions = chat_manager.get_available_sessions()
        
        # Get detailed info for each session
        session_infos = []
        for session_id in available_sessions:
            info = chat_manager.get_session_info(session_id)
            session_infos.append(info)
        
        logger.info(f"GET /api/sessions - Returned {len(session_infos)} sessions")
        return {"sessions": session_infos}

    @app.get("/api/sessions/{session_id}/info")
    async def get_session_info(session_id: str):
        """Get detailed information about a specific session"""
        
        # Create the session if it doesn't exist (for new session access)
        if session_id not in scheduler.chat_sessions:
            success = await scheduler.create_chat_session(session_id)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to create chat session")
        
        session_info = chat_manager.get_session_info(session_id)
        
        logger.info(f"GET /api/sessions/{session_id}/info - Session info retrieved")
        return {
            "session_id": session_id,
            "created": True,
            **session_info
        }

    @app.delete("/api/sessions/{session_id}")
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
        
        # Disconnect any active websockets
        if session_id in chat_manager.active_connections:
            connections = chat_manager.active_connections[session_id][:]
            for ws in connections:
                try:
                    await ws.close()
                except:
                    pass
            del chat_manager.active_connections[session_id]
        
        logger.info(f"Session {session_id} cleaned up - {cleared_tasks} tasks, {history_count} history entries")
        
        return {
            "session_id": session_id,
            "status": "cleaned",
            "cleared_tasks": cleared_tasks,
            "cleared_history": history_count
        }

    @app.post("/api/debug")
    async def toggle_debug(enable: bool):
        """Toggle debug mode"""
        import models
        models.DEBUG_MODE = enable
        scheduler.debug_mode = enable
        
        # Update all existing chat sessions
        for session in scheduler.chat_sessions.values():
            session.debug_mode = enable
        
        logger.info(f"POST /api/debug - Debug mode {'enabled' if enable else 'disabled'}")
        return {"debug_mode": enable, "message": f"Debug mode {'enabled' if enable else 'disabled'}"}

    @app.post("/api/monitoring")
    async def toggle_monitoring(enable: bool):
        """Toggle task monitoring globally"""
        task_monitor = get_task_monitor()
        task_monitor.set_global_monitoring(enable)
        
        logger.info(f"POST /api/monitoring - Task monitoring {'enabled' if enable else 'disabled'}")
        return {"monitoring_enabled": enable, "message": f"Task monitoring {'enabled' if enable else 'disabled'}"}
    
    @app.get("/api/monitoring")
    async def get_monitoring_status():
        """Get task monitoring status and statistics"""
        task_monitor = get_task_monitor()
        stats = task_monitor.get_monitoring_stats()
        
        logger.info(f"GET /api/monitoring - Retrieved monitoring stats")
        return {
            "monitoring": stats,
            "message": f"Monitoring {'enabled' if stats['monitoring_enabled'] else 'disabled'} for {stats['session_count']} sessions"
        }
    
    @app.post("/api/monitoring/test")
    async def test_monitoring():
        """Test the monitoring system with sample responses"""
        from monitor import test_monitor
        
        try:
            monitor = test_monitor()
            logger.info("POST /api/monitoring/test - Monitoring test completed")
            return {
                "status": "success",
                "message": "Monitoring test completed - check server logs for results",
                "monitoring_enabled": monitor.monitoring_enabled,
                "monitored_sessions": list(monitor.monitored_sessions)
            }
        except Exception as e:
            logger.error(f"POST /api/monitoring/test - Test failed: {e}")
            return {
                "status": "error", 
                "message": f"Test failed: {e}"
            }

    @app.post("/api/task-plans/save")
    async def save_task_plan(plan_name: str = None, session_id: str = None):
        """Save scheduled tasks as a plan - from specific session if provided"""
        success, message = scheduler.save_task_plan(plan_name, session_id)
        
        if success:
            logger.info(f"POST /api/task-plans/save - {message}")
            return {"success": True, "message": message}
        else:
            logger.warning(f"POST /api/task-plans/save - Failed: {message}")
            raise HTTPException(status_code=500, detail=message)

    @app.post("/api/task-plans/{plan_name}/load")
    async def load_task_plan(plan_name: str, session_id: str = None):
        """Load a saved task plan and apply it to target session"""
        success, message = scheduler.load_task_plan(plan_name, session_id)
        
        if success:
            # Start scheduler if not running
            if not scheduler.scheduler_running:
                scheduler.scheduler_running = True
                import asyncio
                asyncio.create_task(scheduler.run_scheduler())
            
            logger.info(f"POST /api/task-plans/{plan_name}/load - {message}")
            return {"success": True, "message": message}
        else:
            logger.warning(f"POST /api/task-plans/{plan_name}/load - Failed: {message}")
            raise HTTPException(status_code=404, detail=message)

    @app.get("/api/task-plans")
    async def get_task_plans():
        """Get list of all saved task plans"""
        plans = scheduler.get_saved_task_plans()
        logger.info(f"GET /api/task-plans - Returned {len(plans)} saved plans")
        return {"plans": plans}

    @app.get("/api/sessions/{session_id}/active-plan")
    async def get_active_plan(session_id: str):
        """Get the active plan for a specific session"""
        active_plan = scheduler.get_active_plan(session_id)
        logger.info(f"GET /api/sessions/{session_id}/active-plan - Active plan: {active_plan}")
        return {"active_plan": active_plan}

    return app