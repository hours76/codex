import subprocess
import json
import re

import config
import utils

def run_whisper(filepath: str) -> str:
    """Run whisper-cpp to transcribe audio file."""
    utils.pretty_print("[WHISPER]", "Running whisper-cpp transcription...")
    try:
        cmd = ["whisper-cpp", "--model", config.WHISPER_MODEL, "--file", filepath]
        if config.LANG_CODE:
            cmd.extend(["-l", config.LANG_CODE])
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Show raw Whisper output (prefix each line)
        utils.wprint("----- Raw Whisper Output -----")
        for ln in result.stdout.splitlines():
            utils.wprint(ln)
        if result.stderr.strip():
            utils.wprint("--- stderr ---")
            for ln in result.stderr.splitlines():
                utils.wprint(ln)
        utils.wprint("----------- END -----------")

        lines = result.stdout.strip().splitlines()
        lines = [re.sub(r"\[.*?\]\s*", "", line) for line in lines if line.strip()]
        transcript = " ".join(lines)
        utils.pretty_print("[WHISPER]", transcript)
        return transcript
    except Exception as e:
        utils.wprint("Whisper error:", e)
        return ""