import os
import sys
import warnings

# ---- Suppress pkg_resources deprecation warning (Python 3.12+) ----
if sys.version_info >= (3, 12):
    warnings.filterwarnings(
        "ignore",
        message="pkg_resources.*deprecated",
        category=UserWarning,
        module="webrtcvad",
    )
# ------------------------------------------------------------

# Parameter Settings
SAMPLE_RATE     = 16000
FRAME_DURATION  = 30  # ms
FRAME_SIZE      = int(SAMPLE_RATE * FRAME_DURATION / 1000)
CHANNELS        = 1
VAD_MODE        = 2     # 0: Most conservative, 3: Most sensitive
SILENCE_TIMEOUT = 1.5   # seconds
MAX_SEG_SECS    = 1200.0  # Maximum recording length (seconds)
MIN_DURATION    = 2.0   # Do not save if shorter than this
DEVICE_INDEX    = None  # Default device
OUTFILE         = "chatbot.wav"
OLLAMA_MODEL    = "llama3"
LANG_CODE       = ""   # language code passed to whisper, set via --lang

# ANSI grayscale colors for pretty printing
PREFIX_COL  = 12    # Message start column (including left and right brackets)
LINE_WIDTH  = 80    # Existing setting: total width
PAD         = 1     # Space between prefix and message
LIGHT_GREY = "\033[38;5;250m"   # odd lines (brighter)
DARK_GREY  = "\033[38;5;245m"   # even lines (darker)
RESET_CLR  = "\033[0m"

# Debug flags
DEBUG_RECORDING = False  # Recording debug message switch
DEBUG_WHISPER   = False  # Whisper debug message switch

# Path of this script directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WHISPER_MODEL = os.path.join(SCRIPT_DIR, "..", "models", "ggml-large-v3.bin")