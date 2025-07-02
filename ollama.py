import json

import requests

import config
import utils

def ask_ollama(prompt: str) -> str:
    """Send prompt to Ollama model and return the response."""
    utils.pretty_print("[OLLAMA]", "Sending prompt to Ollama model...")
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": config.OLLAMA_MODEL, "prompt": prompt},
            stream=True
        )
        full_reply = ""
        for line in response.iter_lines(decode_unicode=True):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if "response" in data:
                    full_reply += data["response"]
                if data.get("done"):
                    break
            except json.JSONDecodeError as e:
                print("JSON decode error:", e)
                continue
        return full_reply
    except Exception as e:
        print("Ollama call failed:", e)
        return ""