import os
import re
import asyncio
import subprocess
import tempfile
import streamlit as st


# ============================================================
# SETTINGS
# ============================================================

FONT_DIR = "/usr/share/fonts/truetype/dejavu"


# ============================================================
# RUN COMMAND
# ============================================================

def run(cmd, cwd=None):
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:])

    return result.stdout


# ============================================================
# YOUTUBE VIDEO ID
# ============================================================

def get_video_id(url):
    patterns = [
        r"(?:v=)([\w-]{11})",
        r"(?:youtu\.be/)([\w-]{11})",
        r"(?:shorts/)([\w-]{11})",
        r"(?:embed/)([\w-]{11})",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)

        if match:
            return match.group(1)

    raise ValueError("Valid YouTube link nahi mili.")


# ============================================================
# YOUTUBE TRANSCRIPT
# ============================================================

def get_transcript(url):

    video_id = get_video_id(url)

    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()

    try:
        data = api.fetch(video_id)

        result = []

        for item in data:

            text = getattr(item, "text", "")
            start = float(getattr(item, "start", 0))
            dur = float(getattr(item, "duration", 0))

            if text.strip():

                result.append({
                    "text": text.strip(),
                    "start": start,
                    "duration": dur
                })

        if result:
            return result

    except Exception:
        pass

    # Older youtube-transcript-api compatibility
    try:

        data = YouTubeTranscriptApi.get_transcript(
            video_id
        )

        result = []

        for item in data:

            text = item.get("text", "")
            start = float(item.get("start", 0))
            dur = float(item.get("duration", 0))

            if text.strip():

                result.append({
                    "text": text.strip(),
                    "start": start,
                    "duration": dur
                })

        return result

    except Exception as e:

        raise RuntimeError(
            "YouTube transcript nahi mil saka. "
            "Transcript manually paste karo."
        ) from e


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_text(text):

    text = re.sub(
        r"[^]+\]",
        "",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# SENTENCES
# ============================================================

def split_sentences(items):

    result = []

    for item in items:

        text = clean_text(
            item["text"]
        )

        pieces = re.split(
            r"(?<=[.!?])\s+",
            text
        )

        for piece in pieces:

            piece = piece.strip()

            if not piece:
                continue

            result.append({
                "text": piece,
                "start": item["start"],
                "duration": item["duration"]
            })

    return result


# ============================================================
# PROFANITY MASK
# ============================================================

CURSE = re.compile(
    r"(shit|fuck|bitch|asshole|bastard|dick|cunt)",
    re.I
)


def mask(text):

    def replace(match):

        word = match.group(1)

        if len(word) <= 2:
            return "*" * len(word)

        return (
            word[0]
            + "*" * (len(word) - 2)
            + word[-1]
        )

    return CURSE.sub(
        replace,
        text
    )


# ============================================================
# TIMESTAMP
# ============================================================

def ts(seconds):

    seconds = max(
        float(seconds),
        0
    )

    hours = int(
        seconds // 3600
    )

    minutes = int(
        (seconds % 3600) // 60
    )

    secs = seconds % 60

    return (
        f"{hours}:"
        f"{minutes:02d}:"
        f"{secs:05.2f}"
    )


# ============================================================
# ASS CAPTIONS
# ============================================================

def build_ass(words):

    header = (
        "[Script Info]\n"
        "ScriptType: v
