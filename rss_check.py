import feedparser
import requests
import subprocess
from datetime import datetime
import textwrap

# ---------------- Config ---------------- #
RSS_URL       = "http://feeds.bbci.co.uk/news/rss.xml"
NUM_HEADLINES = 5
OLLAMA_URL    = "http://localhost:11434/api/generate"
OLLAMA_MODEL  = "llama3"  # or other non-chat models
ENABLE_VOICE  = True      # set to False if you don't want voice output
# ---------------------------------------- #

def get_headlines(rss_url: str, limit: int):
    feed = feedparser.parse(rss_url)
    headlines = []

    for entry in feed.entries[:limit]:
        title = entry.get("title", "No Title")
        link  = entry.get("link",  "No Link")
        pub   = ""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt  = datetime(*entry.published_parsed[:6])
            pub = dt.strftime("%Y-%m-%d %H:%M")
        headlines.append({"title": title, "link": link, "published": pub})
    return headlines

def build_prompt(headlines):
    lines = [f"{i+1}. {h['title']} ({h['published']})" for i, h in enumerate(headlines)]
    prompt = "Here are the latest BBC headlines:\n" + "\n".join(lines) + \
             "\n\nPlease summarize these headlines in one sentence and also in chinese."
    return prompt

def ask_ollama_generate(model: str, prompt: str, endpoint: str = OLLAMA_URL) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }
    resp = requests.post(endpoint, json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json().get("response", "[No response]")

def speak(text: str):
    try:
        subprocess.run(["say", text], check=True)
    except Exception as e:
        print(f"🔇 Error using 'say': {e}")

def main():
    headlines = get_headlines(RSS_URL, NUM_HEADLINES)
    if not headlines:
        print("❌ No headlines found. Check RSS URL.")
        return

    prompt = build_prompt(headlines)
    print("====== Prompt Sent to Ollama ======")
    print(textwrap.indent(prompt, "  "))
    print("===================================\n")

    try:
        result = ask_ollama_generate(OLLAMA_MODEL, prompt)
        print("====== Ollama Response ======")
        print(textwrap.indent(result.strip(), "  "))
        print("================================")

        if ENABLE_VOICE:
            speak(result.strip())
    except requests.RequestException as e:
        print(f"❌ Error communicating with Ollama: {e}")

if __name__ == "__main__":
    main()
