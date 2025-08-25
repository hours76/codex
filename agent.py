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
from models import setup_logging, get_config

def create_app_instance():
    """Create FastAPI app instance for production deployment"""
    # Setup logging
    setup_logging()
    
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
    
    return app

def main():
    """Main entry point for the agent system"""
    
    # Setup logging
    logger = setup_logging()
    logger.info("Starting Agent Manager...")
    
    # Load configuration
    host = get_config("server.host")
    port = get_config("server.port")
    logger.info(f"Web Interface: http://{host}:{port}")
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
    
    # Start the server with configuration
    uvicorn.run(
        app, 
        host=host, 
        port=port,
        log_config=log_config,
        access_log=get_config("server.access_log"),
        log_level=get_config("server.log_level")
    )

if __name__ == "__main__":
    main()