import textwrap

import config

MSG_COUNTER = 0   # global message counter for alternating colors

def dprint(*args, **kwargs):
    """Output only when DEBUG_RECORDING is enabled"""
    if config.DEBUG_RECORDING:
        print('[DEBUG_RECORDING]', *args, **kwargs)

def wprint(*args, **kwargs):
    """Output only when DEBUG_WHISPER is enabled"""
    if config.DEBUG_WHISPER:
        print('[DEBUG_WHISPER]', *args, **kwargs)

def pretty_print(prefix: str, msg: str):
    """
    Print message with prefix left‑justified to PREFIX_COL,
    wrap text to LINE_WIDTH, and align continuation lines.
    All lines in the same message share the same color.
    Odd / even messages alternate between LIGHT_GREY & DARK_GREY.
    """
    global MSG_COUNTER
    color = config.LIGHT_GREY if MSG_COUNTER % 2 == 0 else config.DARK_GREY

    prefix = prefix.rjust(config.PREFIX_COL)
    indent = " " * (config.PREFIX_COL + config.PAD)
    wrapped = textwrap.wrap(str(msg), width=config.LINE_WIDTH - len(indent)) or [""]

    # first line
    print(f"{color}{prefix}{' ' * config.PAD}{wrapped[0]}{config.RESET_CLR}")

    # continuation lines
    for line in wrapped[1:]:
        print(f"{color}{indent}{line}{config.RESET_CLR}")

    MSG_COUNTER += 1