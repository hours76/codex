"""
Data models and utility functions for the agent system
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any
import logging
import sys
import json
import os

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
        # Get timestamp with explicit zero-padding
        import datetime
        dt = datetime.datetime.fromtimestamp(record.created)
        timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # Ensure we have the message attribute
        if not hasattr(record, 'message'):
            record.message = record.getMessage()
            
        msg = record.message
        
        # Remove any existing prefixes from the message
        prefixes = ['[USER]', '[AI]', '[API]', '[TASK]', '[AGENT]', '[DEBUG]', '[WEB]', '[WARN]', '[ERROR]', '[MONITOR]']
        for prefix in prefixes:
            if msg.startswith(prefix):
                msg = msg[len(prefix):].strip()
                break
        
        # Return just timestamp and clean message
        return f'[{timestamp}] {msg}'

def setup_logging():
    """Configure logging for the agent system"""
    logger = logging.getLogger("agent")
    logger.setLevel(logging.DEBUG)  # Enable debug logging to see monitor messages
    
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

# Configuration Management
_config_cache = None

def load_config(config_path: str = "config/config.json") -> Dict[str, Any]:
    """Load configuration from JSON file with caching"""
    global _config_cache
    
    if _config_cache is not None:
        return _config_cache
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    try:
        with open(config_path, 'r') as f:
            _config_cache = json.load(f)
        return _config_cache
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in configuration file: {e}")
    except Exception as e:
        raise RuntimeError(f"Error loading configuration: {e}")

def get_config(key_path: str, default: Any = None) -> Any:
    """Get configuration value using dot notation (e.g., 'server.host')"""
    config = load_config()
    
    keys = key_path.split('.')
    value = config
    
    try:
        for key in keys:
            value = value[key]
        return value
    except (KeyError, TypeError):
        if default is not None:
            return default
        raise KeyError(f"Configuration key not found: {key_path}")

# Global debug mode flag
DEBUG_MODE = False