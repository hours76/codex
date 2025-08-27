"""
Core business logic for the agent system
"""

import asyncio
import os
import re
import signal
import uuid
import aiohttp
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import logging
from aiohttp import ClientConnectorError, ClientTimeout, ServerTimeoutError

from models import DEBUG_MODE, get_config
from monitor import get_task_monitor

logger = logging.getLogger("agent")

class ChatSession:
    """Manages individual chat session via HTTP API communication"""
    
    def __init__(self, session_id: str, debug_mode: bool = False, api_session_id: str = None):
        self.session_id = session_id
        self.api_session_id = api_session_id or str(uuid.uuid4())  # Use provided ID or generate new
        self.lock = asyncio.Lock()
        self.debug_mode = debug_mode
        self.http_session = None
        self.connection_pool_connector = None
        self.retry_count = 0
        self.max_retries = get_config("chat_api.max_retries", 3)
    
    async def start(self):
        """Start the HTTP session for API communication"""
        try:
            # Create connection pool connector for better performance
            self.connection_pool_connector = aiohttp.TCPConnector(
                limit=get_config("chat_api.connection_pool_limit", 10),
                keepalive_timeout=get_config("chat_api.keepalive_timeout", 30),
                enable_cleanup_closed=True
            )
            
            # Create HTTP session with connection pooling
            timeout = aiohttp.ClientTimeout(
                total=get_config("timeouts.message_response_timeout"),
                connect=get_config("timeouts.connect_timeout", 10)
            )
            self.http_session = aiohttp.ClientSession(
                timeout=timeout,
                connector=self.connection_pool_connector
            )
            
            # HTTP session created successfully
            if self.debug_mode:
                api_url = os.environ.get("CHAT_API_BASE_URL", get_config("chat_api.base_url"))
                logger.debug(f"Chat session {self.session_id} HTTP session ready for API: {api_url}")
            return True
                
        except Exception as e:
            logger.error(f"Failed to create chat session {self.session_id}: {e}")
            return False
    
    async def _get_api_headers(self):
        """Get headers for API requests"""
        headers = {
            "Content-Type": "application/json",
            "X-Session-ID": self.api_session_id
        }
        
        # Add API key if configured
        api_key = get_config("chat_api.api_key", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            
        return headers
    
    async def send_message(self, message: str, stream_callback=None) -> str:
        """Send message to chat API and get response with retry logic
        
        Args:
            message: The message to send
            stream_callback: Optional async callback for streaming chunks
        """
        async with self.lock:
            if stream_callback:
                return await self._send_message_streaming(message, stream_callback)
            else:
                return await self._send_message_with_retry(message)
    
    async def _send_message_with_retry(self, message: str, attempt: int = 0) -> str:
        """Internal method to send message with retry logic"""
        try:
            if not self.http_session:
                # Attempt to restart session once
                if attempt == 0:
                    success = await self.start()
                    if success:
                        return await self._send_message_with_retry(message, attempt + 1)
                return f"Error: No HTTP session available for {self.session_id}"
            
            # Prepare API request - use environment variable or config
            api_url = os.environ.get("CHAT_API_BASE_URL", get_config("chat_api.base_url"))
            endpoint = f"{api_url}/api/chat"
            
            headers = await self._get_api_headers()
            
            payload = {
                "messages": [{"role": "user", "content": message}],
                "stream": False  # Non-streaming mode
            }
            
            if self.debug_mode:
                logger.debug(f"Session {self.session_id} API request: {endpoint} (attempt {attempt + 1})")
            
            async with self.http_session.post(endpoint, headers=headers, json=payload) as response:
                if response.status == 200:
                    try:
                        result = await response.json()
                        # Extract content from OpenAI-compatible response
                        if "choices" in result and result["choices"]:
                            content = result["choices"][0]["message"]["content"]
                            # Reset retry count on success
                            self.retry_count = 0
                            return content.strip() if content else "(No response content)"
                        else:
                            logger.warning(f"Unexpected API response format: {result}")
                            return "Error: Unexpected API response format - no choices found"
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse JSON response: {e}")
                        return "Error: Invalid JSON response from chat service"
                
                elif response.status == 429:
                    # Rate limit - wait and retry
                    if attempt < self.max_retries:
                        wait_time = min(2 ** attempt, 60)  # Exponential backoff, max 60s
                        logger.warning(f"Rate limited, waiting {wait_time}s before retry (attempt {attempt + 1})")
                        await asyncio.sleep(wait_time)
                        return await self._send_message_with_retry(message, attempt + 1)
                    return "Error: Chat service rate limit exceeded, please try again later"
                
                elif response.status in [500, 502, 503, 504]:
                    # Server errors - retry with backoff
                    if attempt < self.max_retries:
                        wait_time = min(2 ** attempt, 30)  # Exponential backoff, max 30s
                        logger.warning(f"Server error {response.status}, retrying in {wait_time}s (attempt {attempt + 1})")
                        await asyncio.sleep(wait_time)
                        return await self._send_message_with_retry(message, attempt + 1)
                    error_text = await response.text()
                    return f"Error: Chat service unavailable after {self.max_retries} retries ({response.status}): {error_text[:200]}"
                
                elif response.status == 401:
                    return "Error: Authentication failed - check API key configuration"
                elif response.status == 403:
                    return "Error: Access forbidden - check API permissions"
                elif response.status == 404:
                    return "Error: Chat service endpoint not found - check configuration"
                else:
                    error_text = await response.text()
                    logger.error(f"API error {response.status}: {error_text[:200]}")
                    return f"Error: API request failed ({response.status}): {error_text[:200]}"
        
        except (ClientConnectorError, ConnectionRefusedError) as e:
            logger.error(f"Connection error for session {self.session_id}: {e}")
            # Try to restart session once
            if attempt == 0:
                logger.info(f"Attempting to restart HTTP session for {self.session_id}")
                success = await self.restart_process()
                if success:
                    return await self._send_message_with_retry(message, attempt + 1)
            return f"Error: Cannot connect to chat service - {str(e)[:100]}"
        
        except (asyncio.TimeoutError, ServerTimeoutError):
            if attempt < self.max_retries:
                wait_time = min(2 ** attempt, 15)  # Shorter backoff for timeouts
                logger.warning(f"Timeout in API request for session {self.session_id}, retrying in {wait_time}s (attempt {attempt + 1})")
                await asyncio.sleep(wait_time)
                return await self._send_message_with_retry(message, attempt + 1)
            logger.warning(f"Timeout in API request for session {self.session_id} after {self.max_retries} retries")
            return f"Error: Request timeout after {self.max_retries} retries - chat service may be overloaded"
        
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for session {self.session_id}: {e}")
            return "Error: Invalid response format from chat service"
        
        except Exception as e:
            logger.error(f"Unexpected API error for session {self.session_id}: {type(e).__name__}: {e}")
            return f"Error: Unexpected error - {type(e).__name__}: {str(e)[:100]}"
    
    async def _send_message_streaming(self, message: str, stream_callback) -> str:
        """Send message and stream the response from external API"""
        try:
            if not self.http_session:
                success = await self.start()
                if not success:
                    return f"Error: No HTTP session available for {self.session_id}"
            
            # Prepare API request for streaming
            api_url = os.environ.get("CHAT_API_BASE_URL", get_config("chat_api.base_url"))
            endpoint = f"{api_url}/api/chat"
            
            headers = await self._get_api_headers()
            
            payload = {
                "messages": [{"role": "user", "content": message}],
                "stream": True  # Enable streaming
            }
            
            if self.debug_mode:
                logger.debug(f"Session {self.session_id} streaming request: {endpoint}")
            
            full_response = ""
            
            async with self.http_session.post(endpoint, headers=headers, json=payload) as response:
                if response.status == 200:
                    # Process streaming response from external API
                    async for line in response.content:
                        if line:
                            decoded = line.decode('utf-8').strip()
                            if decoded.startswith('data: '):
                                try:
                                    data = json.loads(decoded[6:])  # Skip 'data: ' prefix
                                    
                                    if 'choices' in data and data['choices']:
                                        delta = data['choices'][0].get('delta', {})
                                        content = delta.get('content', '')
                                        
                                        if content:
                                            full_response += content
                                            # Send chunk to callback for real-time display
                                            if stream_callback:
                                                await stream_callback(content)
                                    
                                    # Check if stream is done
                                    if 'choices' in data and data['choices']:
                                        if data['choices'][0].get('finish_reason'):
                                            break
                                            
                                except json.JSONDecodeError:
                                    # Skip non-JSON lines from streaming response
                                    pass
                    
                    return full_response.strip() if full_response else "(No response content)"
                    
                else:
                    error_text = await response.text()
                    logger.error(f"Streaming API error {response.status}: {error_text[:200]}")
                    return f"Error: API request failed ({response.status}): {error_text[:200]}"
                    
        except Exception as e:
            logger.error(f"Streaming error for session {self.session_id}: {e}")
            return f"Error: Streaming failed - {str(e)[:100]}"
    
    async def restart_process(self):
        """Restart the HTTP session"""
        # Close existing session
        await self.close()
        
        # Create new HTTP session
        return await self.start()

    async def close(self):
        """Close the HTTP session and connection pool"""
        if self.http_session:
            try:
                await self.http_session.close()
                logger.info(f"Chat session {self.session_id} HTTP session closed")
            except Exception as e:
                logger.error(f"Error closing HTTP session {self.session_id}: {e}")
            finally:
                self.http_session = None
        
        if self.connection_pool_connector:
            try:
                await self.connection_pool_connector.close()
            except Exception as e:
                logger.error(f"Error closing connection pool for {self.session_id}: {e}")
            finally:
                self.connection_pool_connector = None

class TaskScheduler:
    """Manages scheduled tasks and chat sessions"""
    
    def __init__(self):
        self.chat_sessions: Dict[str, ChatSession] = {}
        self.api_session_ids: Dict[str, str] = {}  # Store API session IDs: session_id -> api_session_id
        self.running = False
        self.task_queue = None  # Initialize later when event loop is ready
        self.debug_mode = DEBUG_MODE
        self.scheduled_tasks = {}  # Dictionary: session_id -> [tasks]
        self.active_plans = {}  # Dictionary: session_id -> plan_name
        self.plan_usage = {}  # Dictionary: plan_name -> set of session_ids that loaded it
        self.scheduler_running = False
        self.chat_manager_ref: Any = None  # Reference to ChatManager for broadcasting
        self.task_monitor = get_task_monitor()  # Task monitoring instance
        
    async def create_chat_session(self, session_id: str):
        """Create a new chat session for a specific session ID"""
        if session_id in self.chat_sessions:
            return True  # Session already exists
            
        # Reuse existing API session ID if available, otherwise a new one will be created
        api_session_id = self.api_session_ids.get(session_id)
        session = ChatSession(session_id, self.debug_mode, api_session_id)
        success = await session.start()
        
        if success:
            self.chat_sessions[session_id] = session
            # Store the API session ID for future use
            self.api_session_ids[session_id] = session.api_session_id
            
            # Initialize task list for this session
            if session_id not in self.scheduled_tasks:
                self.scheduled_tasks[session_id] = []
            
            # Enable task monitoring for this session if globally enabled
            if get_config("monitoring.enabled"):
                self.task_monitor.enable_monitoring(session_id)
        
        return success
    
    async def close_chat_session(self, session_id: str):
        """Close a specific chat session"""
        if session_id in self.chat_sessions:
            await self.chat_sessions[session_id].close()
            del self.chat_sessions[session_id]
            
            # Clean up scheduled tasks for this session
            if session_id in self.scheduled_tasks:
                del self.scheduled_tasks[session_id]
            
            # Clean up active plan for this session
            if session_id in self.active_plans:
                plan_name = self.active_plans[session_id]
                del self.active_plans[session_id]
                
                # Remove this session from plan usage tracking
                if plan_name in self.plan_usage:
                    self.plan_usage[plan_name].discard(session_id)
                    # Clean up empty plan usage entries
                    if not self.plan_usage[plan_name]:
                        del self.plan_usage[plan_name]
            
            # Disable task monitoring for this session
            self.task_monitor.disable_monitoring(session_id)
    
    def get_chat_session(self, session_id: str):
        """Get chat session for a specific session ID"""
        return self.chat_sessions.get(session_id)
    
    async def agent_ask_async(self, session_id: str, question: str, task_type: str = "user", stream_callback=None):
        """Direct AI interaction for specific session with optional streaming"""
        _ = task_type  # Parameter kept for API compatibility
        return await self.send_message_to_session(session_id, question, stream_callback)

    async def send_message_to_session(self, session_id: str, message: str, stream_callback=None):
        """Send a message directly to a chat session with optional streaming"""
        session = self.get_chat_session(session_id)
        if session:
            return await session.send_message(message, stream_callback)
        else:
            return f"Error: No chat session found for {session_id}"

    async def send_message(self, session_id: str, message: str):
        """Send message to chat process and get response (deprecated - use send_message_to_session)"""
        return await self.send_message_to_session(session_id, message)
    
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
            truncate_len = get_config("limits.message_truncation_length")
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
                queue_timeout = get_config("timeouts.task_queue_timeout")
                task = await asyncio.wait_for(self.task_queue.get(), timeout=queue_timeout)
                task_type, session_id, message = task
                
                if task_type == 'scheduled':
                    # Same logic as /web/chat endpoint
                    if hasattr(self, 'chat_manager_ref') and self.chat_manager_ref:
                        from datetime import datetime
                        from models import ChatMessage
                        
                        # 1. Store user message (same as /web/chat)
                        user_msg = ChatMessage(
                            message=f"[AGENT] {message}",
                            sender="user", 
                            timestamp=datetime.now().isoformat()
                        )
                        if session_id not in self.chat_manager_ref.chat_history:
                            self.chat_manager_ref.chat_history[session_id] = []
                        self.chat_manager_ref.chat_history[session_id].append(user_msg)
                        
                        # 2. Get AI response (same as /web/chat)
                        response = await self.chat_manager_ref.ask_ai(session_id, message)
                        
                        # 3. Store AI response (same as /web/chat) 
                        if response and response.strip():
                            ai_msg = ChatMessage(
                                message=response,
                                sender="assistant",
                                timestamp=datetime.now().isoformat()
                            )
                            self.chat_manager_ref.chat_history[session_id].append(ai_msg)
                            
                            # 4. Trigger SSE broadcast (what manual messages do)
                            # The SSE polling will detect these new messages and send them
                            truncate_len = get_config("limits.message_truncation_length")
                            logger.info(f"Scheduled AI response stored for session {session_id}: {response[:truncate_len]}...")
                            
                            await self.task_monitor.monitor_scheduled_response(
                                session_id, message, response, scheduler_ref=self
                            )
                
                self.task_queue.task_done()
                    
            except asyncio.TimeoutError:
                # No task available, continue loop
                continue
            except Exception as e:
                logger.error(f"Task queue error: {e}")
                try:
                    if self.task_queue:
                        self.task_queue.task_done()
                except:
                    pass
            
            await asyncio.sleep(0.01)
    
    async def run_scheduler(self):
        """Run the scheduler in background for all sessions"""
        logger.info("Scheduler started")
        
        while self.scheduler_running and self.running:
            now = datetime.now()
            
            # Check tasks for all sessions
            for session_id, tasks in self.scheduled_tasks.items():
                for task in tasks[:]:
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
        logger.info("Scheduler stopped")
    
    async def _execute_scheduled_task(self, task):
        """Execute a scheduled task for specific session and clear the running flag when done"""
        try:
            session_id = task['session_id']
            await self.scheduled_message_for_session(session_id, task['message'])
        except Exception as e:
            logger.error(f"Task execution error: {e}")
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
    
    def delete_scheduled_task(self, session_id: str, task_index: int):
        """Delete a specific scheduled task by index for a session"""
        if session_id not in self.scheduled_tasks:
            return False, "Session not found"
        
        tasks = self.scheduled_tasks[session_id]
        if task_index < 0 or task_index >= len(tasks):
            return False, "Task index out of range"
        
        # Remove the task at the specified index
        deleted_task = tasks.pop(task_index)
        
        # Stop scheduler if no tasks remain
        total_tasks = sum(len(tasks) for tasks in self.scheduled_tasks.values())
        if total_tasks == 0:
            self.scheduler_running = False
        
        return True, f"Deleted task: {deleted_task['message'][:50]}..."
    
    def save_task_plan(self, plan_name: str = None, session_id: str = None):
        """Save scheduled tasks as a plan to config.json"""
        import json
        from datetime import datetime
        
        # Generate plan name if not provided
        if not plan_name:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            plan_name = f"task_plan_{timestamp}"
        
        # Collect tasks from specified session or all sessions
        all_tasks = []
        seen_tasks = set()  # To avoid duplicates
        
        if session_id:
            # Save tasks from specific session only
            if session_id in self.scheduled_tasks:
                for task in self.scheduled_tasks[session_id]:
                    task_data = {
                        "message": task["message"],
                        "schedule_spec": task["schedule_spec"]
                    }
                    all_tasks.append(task_data)
        else:
            # Save tasks from all sessions (legacy behavior)
            for session_id, tasks in self.scheduled_tasks.items():
                for task in tasks:
                    # Create a unique key for the task to avoid duplicates
                    task_key = (task["message"], task["schedule_spec"])
                    if task_key not in seen_tasks:
                        seen_tasks.add(task_key)
                        task_data = {
                            "message": task["message"],
                            "schedule_spec": task["schedule_spec"]
                        }
                        all_tasks.append(task_data)
        
        # Create plan data without session IDs
        plan_data = {
            "name": plan_name,
            "created_at": datetime.now().isoformat(),
            "tasks": all_tasks
        }
        
        # Load current config
        try:
            with open("config/config.json", "r") as f:
                config = json.load(f)
        except FileNotFoundError:
            config = {}
        
        # Add task_plans section if it doesn't exist
        if "task_plans" not in config:
            config["task_plans"] = {}
        
        # Save the plan
        config["task_plans"][plan_name] = plan_data
        
        # Write back to config.json
        try:
            with open("config/config.json", "w") as f:
                json.dump(config, f, indent=2)
            return True, f"Task plan '{plan_name}' saved successfully"
        except Exception as e:
            return False, f"Failed to save task plan: {str(e)}"
    
    def load_task_plan(self, plan_name: str, target_session_id: str = None):
        """Load a saved task plan from config.json and apply it to target session"""
        import json
        
        try:
            with open("config/config.json", "r") as f:
                config = json.load(f)
        except FileNotFoundError:
            return False, "Config file not found"
        
        if "task_plans" not in config or plan_name not in config["task_plans"]:
            return False, f"Task plan '{plan_name}' not found"
        
        plan_data = config["task_plans"][plan_name]
        loaded_tasks = 0
        
        # Clear existing tasks for the target session first
        if target_session_id:
            if target_session_id in self.scheduled_tasks:
                self.scheduled_tasks[target_session_id] = []
        
        # Handle both old format (with sessions) and new format (just tasks)
        if "tasks" in plan_data:
            # New format - just a list of tasks
            tasks = plan_data["tasks"]
        elif "sessions" in plan_data:
            # Old format - extract tasks from first session
            # Collect all unique tasks from all sessions
            all_tasks = []
            seen_tasks = set()
            for session_id, session_tasks in plan_data["sessions"].items():
                for task in session_tasks:
                    task_key = (task["message"], task["schedule_spec"])
                    if task_key not in seen_tasks:
                        seen_tasks.add(task_key)
                        all_tasks.append(task)
            tasks = all_tasks
        else:
            return False, f"Invalid plan format for '{plan_name}'"
        
        # Load tasks to the target session
        if target_session_id:
            for task in tasks:
                success, message = self.schedule_task(
                    target_session_id, 
                    task["message"], 
                    task["schedule_spec"]
                )
                if success:
                    loaded_tasks += 1
        else:
            return False, "No target session specified"
        
        # Set the active plan for the target session
        if target_session_id:
            self.active_plans[target_session_id] = plan_name
            
            # Track plan usage - add this session to the plan's usage set
            if plan_name not in self.plan_usage:
                self.plan_usage[plan_name] = set()
            self.plan_usage[plan_name].add(target_session_id)
        
        return True, f"Loaded {loaded_tasks} tasks from plan '{plan_name}' to session {target_session_id or 'original sessions'}"
    
    def get_saved_task_plans(self):
        """Get list of all saved task plans from config.json"""
        import json
        
        try:
            with open("config/config.json", "r") as f:
                config = json.load(f)
        except FileNotFoundError:
            return []
        
        if "task_plans" not in config:
            return []
        
        plans = []
        for plan_name, plan_data in config["task_plans"].items():
            # Handle both old format (with sessions) and new format (just tasks)
            if "tasks" in plan_data:
                # New format
                total_tasks = len(plan_data["tasks"])
            elif "sessions" in plan_data:
                # Old format - count unique tasks
                seen_tasks = set()
                for session_tasks in plan_data["sessions"].values():
                    for task in session_tasks:
                        task_key = (task["message"], task["schedule_spec"])
                        seen_tasks.add(task_key)
                total_tasks = len(seen_tasks)
            else:
                total_tasks = 0
            
            # Count how many sessions are currently using this plan
            usage_count = len(self.plan_usage.get(plan_name, set()))
            
            plans.append({
                "name": plan_name,
                "created_at": plan_data.get("created_at", "Unknown"),
                "session_count": usage_count,
                "task_count": total_tasks
            })
        
        return plans
    
    def get_active_plan(self, session_id: str):
        """Get the active plan name for a specific session"""
        return self.active_plans.get(session_id)