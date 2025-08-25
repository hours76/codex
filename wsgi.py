#!/usr/bin/env python3
"""
WSGI-compatible entry point for running the Agent Manager with uvicorn
Usage: uvicorn wsgi:app --host 0.0.0.0 --port 8000
"""

import os
import sys
from agent import create_app_instance

# Create the FastAPI app instance for production
app = create_app_instance()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)