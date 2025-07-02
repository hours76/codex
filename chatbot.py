import os
import sys
import argparse

import config
import utils
from audio import record_once, download_youtube_audio, speak
from whisper import run_whisper
from ollama import ask_ollama

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, help="Specify audio file")
    parser.add_argument("--url", type=str, help="Specify YouTube video URL")
    parser.add_argument(
        "--lang",
        type=str,
        default="",
        help="Language code to pass to whisper (e.g., zh, en, ja)",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default="Please response in the same language and in 3 sentences",
        help="Default prompt prefix (if not specified, uses 'please response in 3 sentences')",
    )
    args = parser.parse_args()

    config.LANG_CODE = args.lang.strip()

    # Startup banner
    print("\n\n\n", end="")
    utils.pretty_print("[CHATBOT]", "Start...")

    def handle_transcript(transcript: str):
        full_prompt = f"{transcript} {args.prompt}".strip()
        utils.pretty_print("[OLLAMA]", args.prompt)
        reply = ask_ollama(full_prompt)
        utils.pretty_print("[OLLAMA]", reply)
        speak(reply)

    if args.url:
        downloaded = download_youtube_audio(args.url, config.OUTFILE)
        if downloaded and os.path.exists(config.OUTFILE):
            transcript = run_whisper(config.OUTFILE)
            if transcript:
                handle_transcript(transcript)
            else:
                utils.pretty_print("[WHISPER]", "Whisper could not transcribe audio")
        else:
            utils.pretty_print("[YT-DLP]", "Download failed or chatbot.wav not found")

    elif args.file:
        if not os.path.isfile(args.file):
            utils.pretty_print("[ERROR]", f"Audio file not found: {args.file}")
            sys.exit(1)
        utils.pretty_print("[RECORDING]", f"Using provided audio file: {args.file}")
        transcript = run_whisper(args.file)
        if transcript:
            handle_transcript(transcript)
        else:
            utils.pretty_print("[WHISPER]", "Whisper could not transcribe audio")

    else:
        while True:
            duration = record_once()
            if duration >= config.MIN_DURATION:
                transcript = run_whisper(os.path.join(config.SCRIPT_DIR, config.OUTFILE))
                if transcript:
                    handle_transcript(transcript)
                else:
                    utils.pretty_print("[WHISPER]", "Whisper could not transcribe audio")
            else:
                utils.dprint("Recording too short, skipping transcription and response")