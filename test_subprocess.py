#!/usr/bin/env python3
"""Test script for subprocess implementation"""

import asyncio
import subprocess
import os

async def test_chat_session():
    """Test creating and communicating with chat session"""
    print("Creating chat subprocess...")
    
    # Create subprocess with pipes
    process = await asyncio.create_subprocess_exec(
        '../rag/venv/bin/python', '-u', '../rag/chat.py', '--mcp',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd='../rag',
        env={**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8'}
    )
    
    print(f"Process started with PID: {process.pid}")
    
    # Wait for initial prompt (consuming startup messages)
    print("Waiting for initial prompt...")
    buffer = b''
    all_output = b''
    prompt_count = 0
    
    while True:
        chunk = await asyncio.wait_for(process.stdout.read(1), timeout=10)
        if not chunk:
            if process.returncode is not None:
                print(f"Process exited with code {process.returncode}")
                stderr = await process.stderr.read()
                print(f"Stderr: {stderr.decode('utf-8', errors='ignore')}")
                return False
            continue
        
        buffer += chunk
        all_output += chunk
        
        # Check for prompt
        if buffer.endswith(b'> ') or buffer.endswith(b'\n> '):
            prompt_count += 1
            print(f"Found prompt #{prompt_count}")
            
            # Wait for a clean prompt or accept after seeing multiple
            lines = buffer.split(b'\n')
            if len(lines) >= 2 and lines[-2] == b'' and lines[-1] == b'> ':
                print("Got clean prompt!")
                break
            elif buffer.endswith(b'\n> '):
                print("Got newline prompt!")
                break
            elif prompt_count >= 2:
                print("Accepting prompt after multiple occurrences")
                break
        
        # Keep buffer limited
        if len(buffer) > 200:
            buffer = buffer[-200:]
    
    # Show startup messages
    startup = all_output.decode('utf-8', errors='ignore')
    print(f"\nStartup messages:\n{startup}\n")
    
    # Send a test message asking about tools
    test_message = "/system"
    print(f"\nSending: {test_message} (to check available tools)")
    process.stdin.write(f"{test_message}\n".encode('utf-8'))
    await process.stdin.drain()
    
    # Read response
    print("Reading response...")
    response = b''
    buffer = b''
    
    while True:
        chunk = await asyncio.wait_for(process.stdout.read(1), timeout=30)
        if not chunk:
            if process.returncode is not None:
                print(f"Process exited with code {process.returncode}")
                return False
            continue
        
        buffer += chunk
        response += chunk
        
        # Check for prompt at end
        if buffer.endswith(b'\n> ') or buffer.endswith(b'> '):
            # Remove the prompt from response
            if response.endswith(b'\n> '):
                response = response[:-3]
            elif response.endswith(b'> '):
                response = response[:-2]
            break
        
        # Keep buffer size limited
        if len(buffer) > 100:
            buffer = buffer[-100:]
    
    # Decode and display response
    text = response.decode('utf-8', errors='ignore')
    print(f"\nRaw response: {repr(text)}")
    
    # Remove echo if present (might be on its own line)
    lines = text.split('\n')
    clean_lines = []
    message_found = False
    
    for line in lines:
        if line.strip() == test_message.strip() and not message_found:
            message_found = True
            print(f"Found and removed echo: {line}")
            continue
        clean_lines.append(line)
    
    cleaned_text = '\n'.join(clean_lines).strip()
    print(f"\nCleaned response: {cleaned_text}")
    
    # Send another message to test tools
    if "Available MCP tools" in cleaned_text or "tool" in cleaned_text.lower():
        print("\n=== MCP tools are available! ===")
    else:
        print("\n=== WARNING: No MCP tools found in system prompt ===")
    
    # Clean up
    print("\nTerminating process...")
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        print("Force killing process...")
        process.kill()
        await process.wait()
    
    print("Test completed successfully!")
    return True

if __name__ == "__main__":
    success = asyncio.run(test_chat_session())
    exit(0 if success else 1)