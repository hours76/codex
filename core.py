"""
Core business logic for the agent system
"""

import asyncio
import os
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List
import logging

from models import ScheduledTask, DEBUG_MODE, get_config

logger = logging.getLogger("agent")

class ChatSession:
    """Manages individual chat subprocess communication"""
    
    def __init__(self, session_id: str, debug_mode: bool = False):
        self.session_id = session_id
        self.process = None
        self.lock = asyncio.Lock()
        self.debug_mode = debug_mode
    
    async def start(self):
        """Start the chat subprocess"""
        try:
            # Create subprocess with pipes for communication
            # Get configuration
            python_exec = get_config("chat_system.python_executable")
            script_path = get_config("chat_system.script_path")
            working_dir = get_config("chat_system.working_directory")
            startup_args = get_config("chat_system.startup_args")
            env_vars = get_config("chat_system.environment")
            
            # Prepare command arguments
            cmd_args = [python_exec, '-u', script_path] + startup_args
            
            # Prepare environment variables
            env = {**os.environ, **env_vars}
            
            self.process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=env
            )
            
            # Wait for initial prompt
            try:
                initial_timeout = get_config("timeouts.initial_prompt_timeout")
                await self._wait_for_prompt(timeout=initial_timeout)
            except asyncio.TimeoutError:
                logger.error(f"Timeout waiting for initial prompt from chat session {self.session_id}")
                await self.close()
                return False
            
            if self.debug_mode:
                logger.debug(f"[DEBUG] Chat session {self.session_id} spawned, PID: {self.process.pid}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to create chat session {self.session_id}: {e}")
            return False
    
    async def _wait_for_prompt(self, timeout: float = None):
        """Wait for the '> ' prompt from the chat process, consuming startup messages"""
        if timeout is None:
            timeout = get_config("timeouts.prompt_wait_timeout")
        
        if not self.process:
            raise Exception(f"No process for session {self.session_id}")
        
        buffer = b''
        all_output = b''  # Collect all output for debugging
        
        async def read_until_prompt():
            nonlocal buffer, all_output
            prompt_count = 0
            
            while True:
                try:
                    chunk_timeout = get_config("timeouts.read_chunk_timeout")
                    chunk = await asyncio.wait_for(self.process.stdout.read(1), timeout=chunk_timeout)
                    if not chunk:
                        if self.process.returncode is not None:
                            raise Exception(f"Process exited with code {self.process.returncode}")
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
            max_display = get_config("limits.max_startup_text_display")
            if len(startup_text) > max_display:
                truncate_len = get_config("limits.message_truncation_length")
                logger.debug(f"[DEBUG] Startup messages from {self.session_id}: {startup_text[:truncate_len]}...")
            else:
                truncate_len = get_config("limits.message_truncation_length")
                logger.debug(f"[DEBUG] Startup messages from {self.session_id}: {startup_text[:truncate_len]}...")
        
        return result
    
    async def send_message(self, message: str):
        """Send message to chat process and get response"""
        if not self.process:
            return f"Error: No chat process for session {self.session_id}"
        
        async with self.lock:
            try:
                # Send message
                self.process.stdin.write(f"{message}\n".encode('utf-8'))
                await self.process.stdin.drain()
                
                # Read response until next prompt
                response = b''
                buffer = b''
                
                while True:
                    msg_timeout = get_config("timeouts.message_response_timeout")
                    chunk = await asyncio.wait_for(self.process.stdout.read(1), timeout=msg_timeout)
                    if not chunk:
                        if self.process.returncode is not None:
                            return f"Error: Process exited with code {self.process.returncode}"
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
                    max_buffer = get_config("limits.max_buffer_size")
                    if len(buffer) > max_buffer:
                        buffer = buffer[-max_buffer:]
                
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
                logger.warning(f"[DEBUG] Timeout in send_message for session {self.session_id}, attempting to restart process")
                # Try to restart the process
                await self.restart_process()
                return "Error: Timeout waiting for response - process restarted"
            except Exception as e:
                return f"Error: {e}"
    
    async def restart_process(self):
        """Restart the chat process"""
        # Close existing process
        await self.close()
        
        # Create new process
        try:
            import os
            # Get configuration
            python_exec = get_config("chat_system.python_executable")
            script_path = get_config("chat_system.script_path")
            working_dir = get_config("chat_system.working_directory")
            startup_args = get_config("chat_system.startup_args")
            env_vars = get_config("chat_system.environment")
            
            # Prepare command arguments
            cmd_args = [python_exec, '-u', script_path] + startup_args
            
            # Prepare environment variables
            env = {**os.environ, **env_vars}
            
            self.process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=env
            )
            
            # Wait for initial prompt
            restart_timeout = get_config("timeouts.initial_prompt_timeout")
            await self._wait_for_prompt(timeout=restart_timeout)
            logger.info(f"[DEBUG] Chat session {self.session_id} restarted successfully")
            
        except Exception as e:
            logger.error(f"Failed to restart chat session {self.session_id}: {e}")
            self.process = None

    async def close(self):
        """Close the chat subprocess"""
        if self.process:
            try:
                # Terminate the process
                self.process.terminate()
                try:
                    term_timeout = get_config("timeouts.process_termination_timeout")
                    await asyncio.wait_for(self.process.wait(), timeout=term_timeout)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
                
                logger.info(f"Chat session {self.session_id} closed")
            except Exception as e:
                logger.error(f"Error closing chat session {self.session_id}: {e}")
            finally:
                self.process = None

class TaskScheduler:
    """Manages scheduled tasks and chat sessions"""
    
    def __init__(self):
        self.chat_sessions: Dict[str, ChatSession] = {}
        self.running = False
        self.task_queue = None  # Initialize later when event loop is ready
        self.debug_mode = DEBUG_MODE
        self.scheduled_tasks = {}  # Dictionary: session_id -> [tasks]
        self.scheduler_running = False
        self.chat_manager_ref: Any = None  # Reference to ChatManager for broadcasting
        
    async def create_chat_session(self, session_id: str):
        """Create a new chat session for a specific session ID"""
        if session_id in self.chat_sessions:
            return True  # Session already exists
            
        session = ChatSession(session_id, self.debug_mode)
        success = await session.start()
        
        if success:
            self.chat_sessions[session_id] = session
            
            # Initialize task list for this session
            if session_id not in self.scheduled_tasks:
                self.scheduled_tasks[session_id] = []
        
        return success
    
    async def close_chat_session(self, session_id: str):
        """Close a specific chat session"""
        if session_id in self.chat_sessions:
            await self.chat_sessions[session_id].close()
            del self.chat_sessions[session_id]
            
            # Clean up scheduled tasks for this session
            if session_id in self.scheduled_tasks:
                del self.scheduled_tasks[session_id]
    
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
        
        return await session.send_message(message)
    
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
            logger.info(f"[TASK] Task scheduled for session {session_id}: '{message[:truncate_len]}...' at {schedule_spec}")
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
                    response = await self.agent_ask_async(session_id, message, "scheduled")
                    truncate_len = get_config("limits.message_truncation_length")
                    logger.info(f"[TASK] Scheduled prompt sent to session {session_id}: {message[:truncate_len]}...")
                    
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