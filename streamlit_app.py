import os
import re
import json
import math
import asyncio
import subprocess
import tempfile
import streamlit as st

# =========================================================
# CONFIG
# =========================================================

FONT_DIR = "/usr/share/fonts/truetype/dejavu"

# =========================================================
# HELPERS
# =========================================================

def run(cmd):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])

    return result.stdout.strip()


def get_video_id(url):
    patterns = [
        r"youtu\.be/([^?&]+)",
        r"youtube\.com/watch\?v=([^?&]+)",
        r"youtube\.com/shorts/([^?&]+)",
        r"youtube\.com/embed/([^?&]+)"
    ]

    for pattern in patterns:
        match = re.search(pattern, url)

        if match:
            return match.group(1)

    return None


def get_transcript(url):
    from youtube_transcript_api import YouTubeTranscriptApi

    video_id = get_video_id(url)

    if not video_id:
        raise ValueError("Invalid YouTube URL.")

    api = YouTubeTranscriptApi()

    transcript = api.fetch(video_id)

    items = []

    for item in transcript:
        if hasattr(item, "text"):
            text = item.text
            start = float(item.start)
            duration = float(item.duration)
        else:
            text = item["text"]
            start = float(item["start"])
            duration = float(item["duration"])

        text = str(text).replace("\n", " ").strip()

        if text:
            items.append({
                "text": text,
                "start": start,
                "duration": duration
            })

    return items


def clean_text(text):
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_sentences(text):
    text = clean_text(text)

    parts = re.split(
        r"(?<=[.!?])\s+",
        text
    )

    return [
        x.strip()
        for x in parts
        if len(x.strip()) > 8
    ]


def mask_profanity(text):
    bad_words = [
        "fuck",
        "fucking",
        "shit",
        "bitch",
        "asshole",
        "motherfucker",
        "damn"
    ]

    for word in bad_words:
        pattern = re.compile(
            r"\b" + re.escape(word) + r"\b",
            re.IGNORECASE
        )

        text = pattern.sub(
            lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1),
            text
        )

    return text


# =========================================================
# ASS CAPTIONS
# =========================================================

def ass_time(seconds):
    seconds = max(0, float(seconds))

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60

    whole = int(secs)
    centis = int(round((secs - whole) * 100))

    if centis >= 100:
        whole += 1
        centis = 0

    return f"{hours}:{minutes:02d}:{whole:02d}.{centis:02d}"


def make_ass(words, output_file):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,1,2,70,70,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]

    for item in words:
        start = ass_time(item["start"])
        end = ass_time(item["end"])

        text = mask_profanity(item["text"])
        text = text.replace("{", r"\{").replace("}", r"\}")

        lines.append(
            f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"
        )

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# =========================================================
# EDGE TTS
# =========================================================

async def edge_tts_generate(text, voice, rate, output_file):
    import edge_tts

    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=rate
    )

    await communicate.save(output_file)


def make_tts(text, voice, speed, output_file):
    rate = int((speed - 1) * 100)

    if rate >= 0:
        rate_string = f"+{rate}%"
    else:
        rate_string = f"{rate}%"

    asyncio.run(
        edge_tts_generate(
            text,
            voice,
            rate_string,
            output_file
        )
    )


# =========================================================
# VIDEO HELPERS
# =========================================================

def get_duration(video_file):
    output = run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_file
    ])

    return float(output)


def download_youtube(url, output_file):
    run([
        "yt-dlp",
        "--no-playlist",
        "-f",
        "best
