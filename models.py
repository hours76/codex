"""
Data models and utility functions for the agent system
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Optional
import logging
import sys

# Pydantic Models
class ChatMessage(BaseModel):
    message: str
    timestamp: str
    sender: str  # 'user' or 'ai' or 'scheduled'

class ScheduleRequest(BaseModel):
    message: str
    schedule_spec: str

class ScheduledTask(BaseModel):
    message: str
    schedule_spec: str
    next_run: datetime
    last_run: Optional[datetime] = None
    is_running: bool = False

# Logging Utilities
class CustomFormatter(logging.Formatter):
    def format(self, record):
        # Ensure we have the message attribute
        if not hasattr(record, 'message'):
            record.message = record.getMessage()
            
        # Check if message has a custom prefix
        msg = record.message
        if msg.startswith('[USER]') or msg.startswith('[AI]') or msg.startswith('[API]') or \
           msg.startswith('[TASK]') or msg.startswith('[AGENT]') or msg.startswith('[DEBUG]') or \
           msg.startswith('[WEB]') or msg.startswith('[WARN]') or msg.startswith('[ERROR]'):
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

def setup_logging():
    """Configure logging for the agent system"""
    logger = logging.getLogger("agent")
    logger.setLevel(logging.INFO)
    
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
    
    return logger

# Global debug mode flag
DEBUG_MODE = False