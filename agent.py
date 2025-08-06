#!/usr/bin/env python3
"""
Interactive Agent Console - Main Entry Point
A comprehensive web-based and API-driven agent system
"""

import asyncio
from contextlib import asynccontextmanager
import uvicorn
from uvicorn.config import LOGGING_CONFIG

from core import TaskScheduler
from web import ChatManager, create_app
from models import setup_logging

def main():
    """Main entry point for the agent system"""
    
    # Setup logging
    logger = setup_logging()
    logger.info("Starting Agent Manager...")
    logger.info("Web Interface: http://127.0.0.1:8000")
    logger.info("Press Ctrl+C to stop")
    
    # Initialize components
    scheduler = TaskScheduler()
    chat_manager = ChatManager(scheduler)
    
    # Set up cross-references
    scheduler.chat_manager_ref = chat_manager
    
    # Create lifespan manager
    @asynccontextmanager
    async def lifespan(app):
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
    
    # Create FastAPI app
    app = create_app(scheduler, chat_manager)
    app.router.lifespan_context = lifespan
    
    # Configure uvicorn with custom logging
    log_config = LOGGING_CONFIG.copy()
    log_config["formatters"]["default"]["fmt"] = "[WEB] %(message)s"
    log_config["formatters"]["access"]["fmt"] = "[WEB] %(message)s"
    
    # Start the server
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