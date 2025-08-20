"""
Core business logic for the agent system
"""

import asyncio
import os
import re
import signal
from datetime import datetime, timedelta
from typing import Dict, Any
import logging

from models import DEBUG_MODE, get_config
from monitor import get_task_monitor

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
                env=env,
                preexec_fn=os.setsid  # Create new process group
            )
            
            # Wait for initial prompt
            try:
                initial_timeout = get_config("timeouts.initial_prompt_timeout")
                await self._wait_for_prompt(timeout=initial_timeout)
            except asyncio.TimeoutError:
                logger.error(f"Timeout waiting for initial prompt from chat session {self.session_id}")
                await self.close()
                return False
            
            if self.debug_mode and self.process:
                logger.debug(f"Chat session {self.session_id} spawned, PID: {self.process.pid}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to create chat session {self.session_id}: {e}")
            return False
    
    async def _wait_for_prompt(self, timeout: float | None = None):
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
                    if not self.process or not self.process.stdout:
                        raise Exception(f"Process terminated for session {self.session_id}")
                    chunk_timeout = get_config("timeouts.read_chunk_timeout")
                    chunk = await asyncio.wait_for(self.process.stdout.read(1), timeout=chunk_timeout)
                    if not chunk:
                        if self.process and self.process.returncode is not None:
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
                logger.debug(f"Startup messages from {self.session_id}: {startup_text[:truncate_len]}...")
            else:
                truncate_len = get_config("limits.message_truncation_length")
                logger.debug(f"Startup messages from {self.session_id}: {startup_text[:truncate_len]}...")
        
        return result
    
    async def send_message(self, message: str):
        """Send message to chat process and get response"""
        if not self.process:
            return f"Error: No chat process for session {self.session_id}"
        
        async with self.lock:
            try:
                # Send message
                if not self.process or not self.process.stdin:
                    return f"Error: No active process for session {self.session_id}"
                self.process.stdin.write(f"{message}\n".encode('utf-8'))
                await self.process.stdin.drain()
                
                # Read response until next prompt
                response = b''
                buffer = b''
                
                while True:
                    if not self.process or not self.process.stdout:
                        return f"Error: Process terminated for session {self.session_id}"
                    msg_timeout = get_config("timeouts.message_response_timeout")
                    chunk = await asyncio.wait_for(self.process.stdout.read(1), timeout=msg_timeout)
                    if not chunk:
                        if self.process and self.process.returncode is not None:
                            return f"Error: Process exited with code {self.process.returncode}"
                        continue
                    
                    buffer += chunk
                    response += chunk
                    
                    # Only check for prompt when we have a complete line ending with "> "
                    # and haven't received new data for a brief moment (to ensure we're at a real prompt)
                    if len(buffer) >= 3 and buffer[-3:] == b'\n> ':
                        # Try to read one more byte with a very short timeout to see if more data is coming
                        try:
                            if not self.process or not self.process.stdout:
                                break
                            extra = await asyncio.wait_for(self.process.stdout.read(1), timeout=0.1)
                            if extra:
                                # More data coming, this wasn't the real prompt
                                buffer += extra
                                response += extra
                                continue
                        except asyncio.TimeoutError:
                            # No more data, this is likely the real prompt
                            if response.endswith(b'\n> '):
                                response = response[:-3]
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
                logger.warning(f"Timeout in send_message for session {self.session_id}, attempting to restart process")
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
                env=env,
                preexec_fn=os.setsid  # Create new process group
            )
            
            # Wait for initial prompt
            restart_timeout = get_config("timeouts.initial_prompt_timeout")
            await self._wait_for_prompt(timeout=restart_timeout)
            logger.info(f"Chat session {self.session_id} restarted successfully")
            
        except Exception as e:
            logger.error(f"Failed to restart chat session {self.session_id}: {e}")
            self.process = None

    async def close(self):
        """Close the chat subprocess"""
        if self.process:
            try:
                # Terminate the entire process group
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    # Process group may not exist, fall back to single process
                    self.process.terminate()
                
                try:
                    term_timeout = get_config("timeouts.process_termination_timeout")
                    await asyncio.wait_for(self.process.wait(), timeout=term_timeout)
                except asyncio.TimeoutError:
                    # Force kill the entire process group
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        # Process group may not exist, fall back to single process
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
        self.active_plans = {}  # Dictionary: session_id -> plan_name
        self.plan_usage = {}  # Dictionary: plan_name -> set of session_ids that loaded it
        self.scheduler_running = False
        self.chat_manager_ref: Any = None  # Reference to ChatManager for broadcasting
        self.task_monitor = get_task_monitor()  # Task monitoring instance
        
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
    
    async def agent_ask_async(self, session_id: str, question: str, task_type: str = "user"):
        """Direct AI interaction for specific session"""
        _ = task_type  # Parameter kept for API compatibility
        return await self.send_message_to_session(session_id, question)

    async def send_message_to_session(self, session_id: str, message: str):
        """Send a message directly to a chat session"""
        session = self.get_chat_session(session_id)
        if session:
            return await session.send_message(message)
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
                    # First, broadcast the scheduled message immediately
                    if hasattr(self, 'chat_manager_ref') and self.chat_manager_ref:
                        await self.chat_manager_ref.broadcast_scheduled_question(session_id, message)
                    
                    truncate_len = get_config("limits.message_truncation_length")
                    logger.info(f"Scheduled prompt sent to session {session_id}: {message[:truncate_len]}...")
                    
                    # Then get the AI response (this takes time)
                    response = await self.agent_ask_async(session_id, message, "scheduled")
                    
                    # Finally, broadcast the response when received
                    if hasattr(self, 'chat_manager_ref') and self.chat_manager_ref:
                        await self.chat_manager_ref.broadcast_ai_response(session_id, response)
                    
                    # Monitor the response and potentially inject follow-up
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
            with open("config.json", "r") as f:
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
            with open("config.json", "w") as f:
                json.dump(config, f, indent=2)
            return True, f"Task plan '{plan_name}' saved successfully"
        except Exception as e:
            return False, f"Failed to save task plan: {str(e)}"
    
    def load_task_plan(self, plan_name: str, target_session_id: str = None):
        """Load a saved task plan from config.json and apply it to target session"""
        import json
        
        try:
            with open("config.json", "r") as f:
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
            with open("config.json", "r") as f:
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