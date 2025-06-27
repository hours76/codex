import sounddevice as sd
import soundfile as sf
import numpy as np

# ---- 抑制 pkg_resources deprecation 警告 (Python 3.12+) ----
import warnings, sys
if sys.version_info >= (3, 12):
    warnings.filterwarnings(
        "ignore",
        message="pkg_resources.*deprecated",
        category=UserWarning,
        module="webrtcvad",
    )
# ------------------------------------------------------------

import webrtcvad
import time
import subprocess
import os
import requests
import json
import re
import sys
import argparse

# === 參數設定 ===
SAMPLE_RATE     = 16000
FRAME_DURATION  = 30  # ms
FRAME_SIZE      = int(SAMPLE_RATE * FRAME_DURATION / 1000)
CHANNELS        = 1
VAD_MODE        = 2     # 0: 最保守, 3: 最靈敏
SILENCE_TIMEOUT = 1.5   # 秒
MAX_SEG_SECS    = 1200.0  # 最長錄音長度（秒）
MIN_DURATION    = 2.0   # 少於這個長度不存檔
DEVICE_INDEX    = None  # 預設裝置
OUTFILE         = "chatbot.wav"
WHISPER_MODEL   = "models/ggml-large-v3.bin"
OLLAMA_MODEL    = "llama3"

DEBUG_RECORDING = False  # 錄音除錯訊息開關，False 時靜音
def dprint(*args, **kwargs):
    """僅在 DEBUG_RECORDING 開啟時輸出"""
    if DEBUG_RECORDING:
        print('[DEBUG_RECORDING]', *args, **kwargs)

DEBUG_WHISPER = False  # Whisper 除錯訊息開關，False 時靜音
def wprint(*args, **kwargs):
    """僅在 DEBUG_WHISPER 開啟時輸出"""
    if DEBUG_WHISPER:
        print('[DEBUG_WHISPER]', *args, **kwargs)

import textwrap

PREFIX_COL  = 12    # 訊息起始欄位 (含左右中括號)
LINE_WIDTH  = 80    # 既有設定：總欄寬
PAD         = 1     # 前綴與訊息之間的空格

# ---- ANSI grayscale colors ----
LIGHT_GREY = "\033[38;5;250m"   # odd lines (brighter)
DARK_GREY  = "\033[38;5;245m"   # even lines (darker)
RESET_CLR  = "\033[0m"

MSG_COUNTER = 0   # global message counter for alternating colors

def pretty_print(prefix: str, msg: str):
    """
    Print message with prefix left‑justified to PREFIX_COL,
    wrap text to LINE_WIDTH, and align continuation lines.
    All lines in the same message share the same color.
    Odd / even messages alternate between LIGHT_GREY & DARK_GREY.
    """
    global MSG_COUNTER
    color = LIGHT_GREY if MSG_COUNTER % 2 == 0 else DARK_GREY

    prefix = prefix.rjust(PREFIX_COL)
    indent = " " * (PREFIX_COL + PAD)
    wrapped = textwrap.wrap(str(msg), width=LINE_WIDTH - len(indent)) or [""]

    # first line
    print(f"{color}{prefix}{' ' * PAD}{wrapped[0]}{RESET_CLR}")

    # continuation lines
    for line in wrapped[1:]:
        print(f"{color}{indent}{line}{RESET_CLR}")

    MSG_COUNTER += 1

vad = webrtcvad.Vad(VAD_MODE)
script_dir = os.path.dirname(os.path.abspath(__file__))

def record_once() -> float:
    is_rec, buf, sil_start, seg_start, done = False, [], None, None, False
    dprint("Listening for speech...")

    def cb(indata, frames, *_):
        nonlocal is_rec, buf, sil_start, seg_start, done
        pcm = indata[:, 0].tobytes()
        is_speech = vad.is_speech(pcm, SAMPLE_RATE)
        now = time.time()

        if is_speech:
            if not is_rec:
                dprint("Speech detected, start recording...")
                pretty_print("[RECORDING]", "Recording...")
                buf, seg_start = [], now
            is_rec = True
            buf.append(indata.copy())
            sil_start = None
        elif is_rec:
            if sil_start is None:
                sil_start = now
                if DEBUG_RECORDING:
                    print('.', end='', flush=True)
            elif now - sil_start > SILENCE_TIMEOUT:
                if DEBUG_RECORDING:
                    print('.', end='', flush=True)
                done = True

        if is_rec and seg_start and now - seg_start > MAX_SEG_SECS:
            dprint(f"Maximum segment length {MAX_SEG_SECS}s reached, stopping recording")
            done = True

    try:
        with sd.InputStream(channels=CHANNELS, samplerate=SAMPLE_RATE,
                            blocksize=FRAME_SIZE, dtype='int16',
                            device=DEVICE_INDEX, callback=cb):
            while not done:
                sd.sleep(100)
    except Exception as e:
        pretty_print("[ERROR]", f"Recording error: {e}")
        return 0.0

    if not buf:
        pretty_print("[ERROR]", "No speech detected, nothing recorded")
        return 0.0

    audio = np.concatenate(buf, axis=0)
    dur = len(audio) / SAMPLE_RATE
    if dur < MIN_DURATION:
        dprint(f"Recording only {dur:.2f}s (< {MIN_DURATION}s), not saved")
        return 0.0

    sf.write(os.path.join(script_dir, OUTFILE), audio, SAMPLE_RATE, subtype='PCM_16')
    pretty_print("[RECORDING]", f"Saved recording as {OUTFILE} ({dur:.2f}s)")
    return dur

def run_whisper(filepath: str) -> str:
    pretty_print("[WHISPER]", "Running whisper-cpp transcription...")
    try:
        result = subprocess.run(
            ["whisper-cpp", "--model", WHISPER_MODEL, "--file", filepath, "-nt"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # --- 顯示 Whisper 原始輸出（逐行加前綴） ---
        wprint("----- Whisper 原始輸出 -----")
        for ln in result.stdout.splitlines():
            wprint(ln)
        if result.stderr.strip():
            wprint("--- stderr ---")
            for ln in result.stderr.splitlines():
                wprint(ln)
        wprint("----------- END -----------")

        lines = result.stdout.strip().splitlines()
        lines = [re.sub(r"\[.*?\]\s*", "", line) for line in lines if line.strip()]
        transcript = " ".join(lines)
        pretty_print("[WHISPER]", transcript)
        return transcript
    except Exception as e:
        wprint("Whisper 發生錯誤:", e)
        return ""

def ask_ollama(prompt: str) -> str:
    pretty_print("[OLLAMA]", "Sending prompt to Ollama model...")
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt},
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
        print("Ollama 呼叫失敗:", e)
        return ""

def speak(text: str):
    pretty_print("[TTS]", "Playing TTS response...")
    subprocess.run(["say", text])

def download_youtube_audio(url: str, output_file: str) -> bool:
    pretty_print("[YT-DLP]", f"Downloading YouTube audio: {url}")
    try:
        result = subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "wav", "--force-overwrites", "--output", output_file, url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            pretty_print("[YT-DLP]", f"yt-dlp error: {result.stderr}")
            return False
        pretty_print("[YT-DLP]", "Download complete")
        return True
    except Exception as e:
        pretty_print("[YT-DLP]", f"Failed to run yt-dlp: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, help="指定聲音檔案")
    parser.add_argument("--url", type=str, help="指定 YouTube 影片網址")
    parser.add_argument("--prompt", type=str, default="please response in 3 sentences", help="預設 prompt 前給（未指定時自帶 'please response in 3 sentences'）")
    args = parser.parse_args()

    # Startup banner
    print("\n\n\n", end="")
    pretty_print("[CHATBOT]", "Start...")

    def handle_transcript(transcript: str):
        full_prompt = f"{args.prompt} {transcript}".strip()
        reply = ask_ollama(full_prompt)
        pretty_print("[OLLAMA]", reply)
        speak(reply)

    if args.url:
        downloaded = download_youtube_audio(args.url, "chatbot.wav")
        if downloaded and os.path.exists("chatbot.wav"):
            transcript = run_whisper("chatbot.wav")
            if transcript:
                handle_transcript(transcript)
            else:
                pretty_print("[WHISPER]", "Whisper could not transcribe audio")
        else:
            pretty_print("[YT-DLP]", "Download failed or chatbot.wav not found")

    elif args.file:
        if not os.path.isfile(args.file):
            pretty_print("[ERROR]", f"Audio file not found: {args.file}")
            sys.exit(1)
        pretty_print("[RECORDING]", f"Using provided audio file: {args.file}")
        transcript = run_whisper(args.file)
        if transcript:
            handle_transcript(transcript)
        else:
            pretty_print("[WHISPER]", "Whisper could not transcribe audio")

    else:
        while True:
            duration = record_once()
            if duration >= MIN_DURATION:
                transcript = run_whisper(os.path.join(script_dir, OUTFILE))
                if transcript:
                    handle_transcript(transcript)
                else:
                    pretty_print("[WHISPER]", "Whisper could not transcribe audio")
            else:
                dprint("Recording too short, skipping transcription and response")
