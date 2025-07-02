import os
import time
import subprocess

import numpy as np
import sounddevice as sd
import soundfile as sf
import webrtcvad

import config
import utils

# Initialize Voice Activity Detector
vad = webrtcvad.Vad(config.VAD_MODE)

def record_once() -> float:
    """Record audio from the microphone until silence is detected."""
    is_rec = False
    buf = []
    sil_start = None
    seg_start = None
    done = False
    speech_started = False
    recording_msg_printed = False

    utils.dprint("Listening for speech...")

    def callback(indata, frames, *_):
        nonlocal is_rec, buf, sil_start, seg_start, done, speech_started
        pcm = indata[:, 0].tobytes()
        is_speech = vad.is_speech(pcm, config.SAMPLE_RATE)
        now = time.time()

        if is_speech:
            if not is_rec:
                utils.dprint("Speech detected, start recording...")
                is_rec = True
                buf.clear()
                seg_start = now
                speech_started = True
            buf.append(indata.copy())
            sil_start = None
        elif is_rec:
            if sil_start is None:
                sil_start = now
                if config.DEBUG_RECORDING:
                    print('.', end='', flush=True)
            elif now - sil_start > config.SILENCE_TIMEOUT:
                if config.DEBUG_RECORDING:
                    print('.', end='', flush=True)
                done = True

        if is_rec and seg_start and now - seg_start > config.MAX_SEG_SECS:
            utils.dprint(f"Maximum segment length {config.MAX_SEG_SECS}s reached, stopping recording")
            done = True

    try:
        with sd.InputStream(
            channels=config.CHANNELS,
            samplerate=config.SAMPLE_RATE,
            blocksize=config.FRAME_SIZE,
            dtype='int16',
            device=config.DEVICE_INDEX,
            callback=callback
        ):
            while not done:
                if speech_started and not recording_msg_printed:
                    utils.pretty_print("[RECORDING]", "Recording...")
                    recording_msg_printed = True
                sd.sleep(100)
    except Exception as e:
        utils.pretty_print("[ERROR]", f"Recording error: {e}")
        return 0.0

    if not buf:
        utils.pretty_print("[ERROR]", "No speech detected, nothing recorded")
        return 0.0

    audio = np.concatenate(buf, axis=0)
    dur = len(audio) / config.SAMPLE_RATE
    if dur < config.MIN_DURATION:
        utils.dprint(f"Recording only {dur:.2f}s (< {config.MIN_DURATION}s), not saved")
        return 0.0

    out_path = os.path.join(config.SCRIPT_DIR, config.OUTFILE)
    sf.write(out_path, audio, config.SAMPLE_RATE, subtype='PCM_16')
    utils.pretty_print("[RECORDING]", f"Saved recording as {config.OUTFILE} ({dur:.2f}s)")
    return dur

def speak(text: str):
    """Play text-to-speech using system 'say' command."""
    utils.pretty_print("[TTS]", "Playing TTS response...")
    subprocess.run(["say", text])

def download_youtube_audio(url: str, output_file: str) -> bool:
    """Download YouTube audio via yt-dlp."""
    utils.pretty_print("[YT-DLP]", f"Downloading YouTube audio: {url}")
    try:
        result = subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "wav", "--force-overwrites", "--output", output_file, url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            utils.pretty_print("[YT-DLP]", f"yt-dlp error: {result.stderr}")
            return False
        utils.pretty_print("[YT-DLP]", "Download complete")
        return True
    except Exception as e:
        utils.pretty_print("[YT-DLP]", f"Failed to run yt-dlp: {e}")
        return False