import sounddevice as sd
import soundfile as sf
import numpy as np
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

vad = webrtcvad.Vad(VAD_MODE)
script_dir = os.path.dirname(os.path.abspath(__file__))

def record_once() -> float:
    is_rec, buf, sil_start, seg_start, done = False, [], None, None, False
    print("🔍 開始監聽中，等待語音輸入...")

    def cb(indata, frames, *_):
        nonlocal is_rec, buf, sil_start, seg_start, done
        pcm = indata[:, 0].tobytes()
        is_speech = vad.is_speech(pcm, SAMPLE_RATE)
        now = time.time()

        if is_speech:
            if not is_rec:
                print("🎤 偵測到語音，開始錄音...")
                buf, seg_start = [], now
            is_rec = True
            buf.append(indata.copy())
            sil_start = None
        elif is_rec:
            if sil_start is None:
                sil_start = now
                print("🤫 開始偵測靜音...")
            elif now - sil_start > SILENCE_TIMEOUT:
                print(f"🤫 偵測到靜音 {SILENCE_TIMEOUT}s，自動結束錄音")
                done = True

        if is_rec and seg_start and now - seg_start > MAX_SEG_SECS:
            print(f"⏰ 已達最大錄音長度 {MAX_SEG_SECS}s，自動結束")
            done = True

    try:
        with sd.InputStream(channels=CHANNELS, samplerate=SAMPLE_RATE,
                            blocksize=FRAME_SIZE, dtype='int16',
                            device=DEVICE_INDEX, callback=cb):
            while not done:
                sd.sleep(100)
    except Exception as e:
        print(f"❌ 錄音過程錯誤: {e}")
        return 0.0

    if not buf:
        print("❌ 無聲音輸入，未錄到任何內容")
        return 0.0

    audio = np.concatenate(buf, axis=0)
    dur = len(audio) / SAMPLE_RATE
    if dur < MIN_DURATION:
        print(f"⚠️ 錄音僅 {dur:.2f}s，低於最短長度 {MIN_DURATION}s，不儲存")
        return 0.0

    sf.write(os.path.join(script_dir, OUTFILE), audio, SAMPLE_RATE, subtype='PCM_16')
    print(f"✅ 錄音完成並儲存為 {OUTFILE}（{dur:.2f}s）")
    return dur

def run_whisper(filepath: str) -> str:
    print("[1/3] 使用 whisper-cpp 進行語音辨識...")
    try:
        result = subprocess.run(
            ["whisper-cpp", "--model", WHISPER_MODEL, "--file", filepath, "-nt"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # --- 顯示 Whisper 原始輸出 ---
        print("----- Whisper 原始輸出 -----")
        print(result.stdout.strip())
        print("----------- END -----------")

        if result.stderr:
            print(f"Whisper stderr: {result.stderr}")

        lines = result.stdout.strip().splitlines()
        lines = [re.sub(r"\[.*?\]\s*", "", line) for line in lines if line.strip()]
        transcript = " ".join(lines)
        print(f"[辨識結果] {transcript}")
        return transcript
    except Exception as e:
        print("Whisper 發生錯誤:", e)
        return ""

def ask_ollama(prompt: str) -> str:
    print("[2/3] 向 ollama 模型發送 prompt...")
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
    print("[3/3] 播放語音回應...")
    subprocess.run(["say", text])

def download_youtube_audio(url: str, output_file: str) -> bool:
    print(f"🎞️ 下載 YouTube 音訊: {url}")
    try:
        result = subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "wav", "--force-overwrites", "--output", output_file, url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            print("❌ yt-dlp 執行錯誤:", result.stderr)
            return False
        print("✅ 下載完成")
        return True
    except Exception as e:
        print("❌ 無法執行 yt-dlp:", e)
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, help="指定聲音檔案")
    parser.add_argument("--url", type=str, help="指定 YouTube 影片網址")
    parser.add_argument("--prompt", type=str, default="please response in 3 sentences", help="預設 prompt 前給（未指定時自帶 'please response in 3 sentences'）")
    args = parser.parse_args()

    def handle_transcript(transcript: str):
        full_prompt = f"{args.prompt} {transcript}".strip()
        reply = ask_ollama(full_prompt)
        print(f"[模型回應] {reply}")
        speak(reply)

    if args.url:
        downloaded = download_youtube_audio(args.url, "chatbot.wav")
        if downloaded and os.path.exists("chatbot.wav"):
            transcript = run_whisper("chatbot.wav")
            if transcript:
                handle_transcript(transcript)
            else:
                print("❌ Whisper 無法辨識語音內容")
        else:
            print("❌ 無法下載或找不到 chatbot.wav")

    elif args.file:
        if not os.path.isfile(args.file):
            print(f"❌ 指定的聲音檔不存在: {args.file}")
            sys.exit(1)
        print(f"🔁 使用提供的聲音檔案: {args.file}")
        transcript = run_whisper(args.file)
        if transcript:
            handle_transcript(transcript)
        else:
            print("❌ Whisper 無法辨識語音內容")

    else:
        while True:
            duration = record_once()
            if duration >= MIN_DURATION:
                transcript = run_whisper(os.path.join(script_dir, OUTFILE))
                if transcript:
                    handle_transcript(transcript)
                else:
                    print("❌ Whisper 無法辨識語音內容")
            else:
                print("🛑 錄音太短，跳過辨識與回應")
