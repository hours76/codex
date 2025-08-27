"""
Task monitoring module for the agent system

Monitors scheduled task responses and automatically prompts the model
to proceed when no tool calls are detected in the response.
"""

import re
import logging
from typing import Set
from models import get_config

logger = logging.getLogger("agent")

class TaskMonitor:
    """Monitors scheduled task processing and provides automatic prompts"""
    
    def __init__(self):
        self.monitored_sessions: Set[str] = set()
        self.monitoring_enabled = get_config("monitoring.enabled", True)
        self.tool_pattern = re.compile(r'^/tool\s+', re.MULTILINE | re.IGNORECASE)
        self.auto_prompt_counts = {}  # Track auto-prompt count per task execution
        
    def enable_monitoring(self, session_id: str):
        """Enable monitoring for a specific session"""
        self.monitored_sessions.add(session_id)
        logger.info(f"Monitoring enabled for session {session_id}")
    
    def disable_monitoring(self, session_id: str):
        """Disable monitoring for a specific session"""
        self.monitored_sessions.discard(session_id)
        logger.info(f"Monitoring disabled for session {session_id}")
    
    def is_monitoring_enabled(self, session_id: str) -> bool:
        """Check if monitoring is enabled for a session"""
        return self.monitoring_enabled and session_id in self.monitored_sessions
    
    def has_tool_calls(self, response: str) -> bool:
        """Check if response contains successfully executed /tool commands"""
        if not response or not isinstance(response, str):
            return False
        
        # Split response into lines and check each line
        lines = response.split('\n')
        
        for i, line in enumerate(lines):
            # Check if line starts with /tool (after stripping whitespace)
            clean_line = line.strip()
            if clean_line.startswith('/tool'):
                # Check if this line or next line contains error indicators
                # that suggest the tool call failed
                error_indicators = ['skipping', 'unknown tool', 'error:', 'failed']
                
                # Check current line for errors
                line_lower = line.lower()
                if any(indicator in line_lower for indicator in error_indicators):
                    logger.info(f"Found failed tool call: {clean_line[:50]}...")
                    continue
                
                # Check next line for error messages (if exists)
                if i + 1 < len(lines):
                    next_line_lower = lines[i + 1].lower()
                    if any(indicator in next_line_lower for indicator in error_indicators):
                        logger.info(f"Found failed tool call (error on next line): {clean_line[:50]}...")
                        continue
                
                logger.info(f"Found successful tool call: {clean_line[:50]}...")
                return True
        
        return False
    
    def needs_prompting(self, response: str) -> bool:
        """Determine if response needs 'please proceed' prompting"""
        if not self.monitoring_enabled:
            return False
        
        # Don't prompt if response is empty or too short
        min_length = get_config("monitoring.min_response_length", 10)
        if not response or len(response.strip()) < min_length:
            return False
        
        # Don't prompt if response contains tool calls
        if self.has_tool_calls(response):
            return False
        
        # Response seems complete but has no tool calls - needs prompting
        logger.info("Response needs auto-prompting - no tool calls detected")
        return True
    
    def get_task_key(self, session_id: str, task_message: str) -> str:
        """Generate unique key for task execution"""
        import hashlib
        return f"{session_id}:{hashlib.md5(task_message.encode()).hexdigest()[:8]}"
    
    def reset_task_counter(self, session_id: str, task_message: str):
        """Reset auto-prompt counter for a new scheduled task execution"""
        task_key = self.get_task_key(session_id, task_message)
        if task_key in self.auto_prompt_counts:
            del self.auto_prompt_counts[task_key]
    
    async def monitor_scheduled_response(self, session_id: str, task_message: str, response: str, scheduler_ref=None) -> bool:
        """
        Monitor a scheduled task response and inject 'please proceed' if needed
        
        Args:
            session_id: The session ID
            task_message: The original scheduled task message
            response: The AI response to monitor
            scheduler_ref: Reference to TaskScheduler for sending follow-up
            
        Returns:
            bool: True if follow-up was sent, False otherwise
        """
        if not self.is_monitoring_enabled(session_id):
            return False
        
        # Reset counter for new scheduled task execution
        self.reset_task_counter(session_id, task_message)
        
        truncate_len = get_config("limits.message_truncation_length", 16)
        
        if self.needs_prompting(response):
            # Check if we've exceeded max auto-prompts for this task
            task_key = self.get_task_key(session_id, task_message)
            current_count = self.auto_prompt_counts.get(task_key, 0)
            max_prompts = get_config("monitoring.max_auto_prompts_per_task", 3)
            
            if current_count >= max_prompts:
                logger.info(f"Max auto-prompts ({max_prompts}) reached for task {task_key}")
                return False
            
            logger.info(f"Injecting 'please proceed' for session {session_id} (attempt {current_count + 1}/{max_prompts})")
            
            if scheduler_ref and hasattr(scheduler_ref, 'send_message_to_session'):
                try:
                    proceed_prompt = get_config("monitoring.auto_proceed_prompt", "please proceed")
                    
                    # Increment counter
                    self.auto_prompt_counts[task_key] = current_count + 1
                    
                    # Show auto prompt on web immediately
                    if hasattr(scheduler_ref, 'chat_manager_ref') and scheduler_ref.chat_manager_ref:
                        from models import ChatMessage
                        from datetime import datetime
                        
                        auto_prompt_msg = ChatMessage(
                            message=f"[AUTO] {proceed_prompt} ({self.auto_prompt_counts[task_key]}/{max_prompts})",
                            timestamp=datetime.now().isoformat(),
                            sender="system"
                        )
                        # Store auto-prompt message directly in chat history
                        if session_id not in scheduler_ref.chat_manager_ref.chat_history:
                            scheduler_ref.chat_manager_ref.chat_history[session_id] = []
                        scheduler_ref.chat_manager_ref.chat_history[session_id].append(auto_prompt_msg)
                    
                    # Send prompt to AI and get response
                    follow_up_response = await scheduler_ref.send_message_to_session(session_id, proceed_prompt)
                    
                    # Broadcast AI's follow-up response
                    if hasattr(scheduler_ref, 'chat_manager_ref') and scheduler_ref.chat_manager_ref:
                        ai_response_msg = ChatMessage(
                            message=follow_up_response,
                            timestamp=datetime.now().isoformat(),
                            sender="assistant"
                        )
                        # Store AI response directly in chat history
                        scheduler_ref.chat_manager_ref.chat_history[session_id].append(ai_response_msg)
                    
                    logger.info(f"Follow-up sent for session {session_id}: {follow_up_response[:truncate_len]}...")
                    
                    # Monitor the follow-up response for additional auto-prompts (without re-broadcasting original task)
                    await self._monitor_follow_up_response(session_id, task_message, follow_up_response, scheduler_ref)
                    
                    return True
                    
                except Exception as e:
                    logger.error(f"Failed to send follow-up for session {session_id}: {e}")
                    return False
        
        return False
    
    async def _monitor_follow_up_response(self, session_id: str, task_message: str, response: str, scheduler_ref=None) -> bool:
        """Monitor a follow-up response without re-broadcasting the original scheduled task"""
        truncate_len = get_config("limits.message_truncation_length", 16)
        
        if self.needs_prompting(response):
            # Check if we've exceeded max auto-prompts for this task
            task_key = self.get_task_key(session_id, task_message)
            current_count = self.auto_prompt_counts.get(task_key, 0)
            max_prompts = get_config("monitoring.max_auto_prompts_per_task", 3)
            
            if current_count >= max_prompts:
                logger.info(f"Max auto-prompts ({max_prompts}) reached for task {task_key}")
                return False
            
            logger.info(f"Injecting 'please proceed' for session {session_id} (attempt {current_count + 1}/{max_prompts})")
            
            if scheduler_ref and hasattr(scheduler_ref, 'send_message_to_session'):
                try:
                    proceed_prompt = get_config("monitoring.auto_proceed_prompt", "please proceed")
                    
                    # Increment counter
                    self.auto_prompt_counts[task_key] = current_count + 1
                    
                    # Show auto prompt on web immediately
                    if hasattr(scheduler_ref, 'chat_manager_ref') and scheduler_ref.chat_manager_ref:
                        from models import ChatMessage
                        from datetime import datetime
                        
                        auto_prompt_msg = ChatMessage(
                            message=f"[AUTO] {proceed_prompt} ({self.auto_prompt_counts[task_key]}/{max_prompts})",
                            timestamp=datetime.now().isoformat(),
                            sender="system"
                        )
                        # Store auto-prompt message directly in chat history
                        if session_id not in scheduler_ref.chat_manager_ref.chat_history:
                            scheduler_ref.chat_manager_ref.chat_history[session_id] = []
                        scheduler_ref.chat_manager_ref.chat_history[session_id].append(auto_prompt_msg)
                    
                    # Send prompt to AI and get response
                    follow_up_response = await scheduler_ref.send_message_to_session(session_id, proceed_prompt)
                    
                    # Broadcast AI's follow-up response
                    if hasattr(scheduler_ref, 'chat_manager_ref') and scheduler_ref.chat_manager_ref:
                        ai_response_msg = ChatMessage(
                            message=follow_up_response,
                            timestamp=datetime.now().isoformat(),
                            sender="assistant"
                        )
                        # Store AI response directly in chat history
                        scheduler_ref.chat_manager_ref.chat_history[session_id].append(ai_response_msg)
                    
                    logger.info(f"Follow-up sent for session {session_id}: {follow_up_response[:truncate_len]}...")
                    
                    # Continue monitoring follow-up responses recursively
                    await self._monitor_follow_up_response(session_id, task_message, follow_up_response, scheduler_ref)
                    
                    return True
                    
                except Exception as e:
                    logger.error(f"Failed to send follow-up for session {session_id}: {e}")
                    return False
        
        return False
    
    def get_monitoring_stats(self) -> dict:
        """Get monitoring statistics"""
        return {
            "monitoring_enabled": self.monitoring_enabled,
            "monitored_sessions": list(self.monitored_sessions),
            "session_count": len(self.monitored_sessions)
        }
    
    def set_global_monitoring(self, enabled: bool):
        """Enable or disable monitoring globally"""
        self.monitoring_enabled = enabled
        logger.info(f"Global monitoring {'enabled' if enabled else 'disabled'}")

# Global monitor instance
_task_monitor = None

def get_task_monitor() -> TaskMonitor:
    """Get the global task monitor instance"""
    global _task_monitor
    if _task_monitor is None:
        _task_monitor = TaskMonitor()
    return _task_monitor

# Test function for debugging
def test_monitor():
    """Test the monitoring functionality"""
    monitor = get_task_monitor()
    
    # Test responses
    test_cases = [
        ("Hello, how can I help?", False),  # Should NOT need prompting (question)
        ("I'll analyze the data for you.", True),   # Should need prompting (no tools)
        ("Let me check that.\n/tool search data", False),  # Should NOT need prompting (has tool)
        ("Error: Cannot process", False),  # Should NOT need prompting (error)
        ("Short", False),  # Should NOT need prompting (too short)
    ]
    
    print("\n=== MONITOR TEST RESULTS ===")
    for response, expected in test_cases:
        result = monitor.needs_prompting(response)
        status = "✓" if result == expected else "✗"
        print(f"{status} '{response[:30]}...' -> needs_prompting: {result} (expected: {expected})")
    
    return monitor