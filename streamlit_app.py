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
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        output_file,
        url
    ])


def extract_audio(video_file, audio_file):
    run([
        "ffmpeg",
        "-y",
        "-i",
        video_file,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-c:a",
        "mp3",
        audio_file
    ])


def burn_subtitles(video_file, subtitle_file, output_file):
    escaped_subtitle = subtitle_file.replace("\\", "/")
    escaped_subtitle = escaped_subtitle.replace(":", r"\:")

    run([
        "ffmpeg",
        "-y",
        "-i",
        video_file,
        "-vf",
        f"ass='{escaped_subtitle}'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        output_file
    ])


def resize_vertical(video_file, output_file):
    run([
        "ffmpeg",
        "-y",
        "-i",
        video_file,
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        output_file
    ])


def cut_video(video_file, start, end, output_file):
    duration = max(0.1, float(end) - float(start))

    run([
        "ffmpeg",
        "-y",
            "-i",
        video_file,
        "-ss",
        str(start),
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        output_file
    ])


def add_audio(video_file, audio_file, output_file):
    run([
        "ffmpeg",
        "-y",
        "-i",
        video_file,
        "-i",
        audio_file,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        output_file
    ])


# =========================================================
# TRANSCRIPT HELPERS
# =========================================================

def transcript_to_text(transcript):
    parts = []

    for item in transcript:
        text = clean_text(item["text"])

        if text:
            parts.append(text)

    return " ".join(parts)


def transcript_to_words(transcript):
    words = []

    for item in transcript:
        text = clean_text(item["text"])

        if not text:
            continue

        start = float(item["start"])
        duration = float(item["duration"])

        pieces = text.split()

        if not pieces:
            continue

        word_duration = duration / len(pieces)

        for index, word in enumerate(pieces):
            word_start = start + index * word_duration
            word_end = word_start + word_duration

            words.append({
                "text": word,
                "start": word_start,
                "end": word_end
            })

    return words


def save_transcript(transcript, output_file):
    text = transcript_to_text(transcript)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(text)


# =========================================================
# WORK DIRECTORY
# =========================================================

def make_workdir():
    return tempfile.mkdtemp(prefix="shorts_maker_")


# =========================================================
# STREAMLIT UI
# =========================================================

st.set_page_config(
    page_title="AI Video Creator",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 AI Video Creator")
st.caption("Free YouTube video downloader, transcript, captions and vertical video maker")


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:
    st.header("⚙️ Settings")

    voice = st.selectbox(
        "Voice",
        [
            "en-US-AriaNeural",
            "en-US-GuyNeural",
            "en-US-JennyNeural",
            "en-US-ChristopherNeural",
            "en-US-SoniaNeural",
            "en-US-RyanNeural"
        ]
    )

    speed = st.slider(
        "Voice Speed",
        0.75,
        1.50,
        1.00,
        0.05
    )

    make_vertical = st.checkbox(
        "Make Vertical 9:16",
        value=True
    )

    make_captions = st.checkbox(
        "Add Captions",
        value=True
    )


# =========================================================
# INPUT
# =========================================================

url = st.text_input(
    "🔗 YouTube URL",
    placeholder="Paste YouTube video URL here..."
)


# =========================================================
# PROCESS
# =========================================================

if st.button("🚀 Process Video", type="primary"):

    if not url.strip():
        st.error("Please enter a YouTube URL.")
        st.stop()

    workdir = make_workdir()

    source_video = os.path.join(
        workdir,
        "source.mp4"
    )

    transcript_file = os.path.join(
        workdir,
        "transcript.txt"
    )

    subtitle_file = os.path.join(
        workdir,
        "captions.ass"
    )

    vertical_video = os.path.join(
        workdir,
        "vertical.mp4"
    )

    final_file = os.path.join(
        workdir,
        "final.mp4"
    )

    audio_file = os.path.join(
        workdir,
        "voice.mp3"
    )

    try:

        # -------------------------------------------------
        # DOWNLOAD
        # -------------------------------------------------

        with st.status(
            "Downloading YouTube video...",
            expanded=True
        ) as status:

            download_youtube(
                url,
                source_video
            )

            status.update(
                label="Video downloaded.",
                state="complete"
            )


        # -------------------------------------------------
        # DURATION
        # -------------------------------------------------

        duration = get_duration(
            source_video
        )

        st.success(
            f"Video duration: {duration:.1f} seconds"
        )


        # -------------------------------------------------
        # TRANSCRIPT
        # -------------------------------------------------

        with st.status(
            "Getting YouTube transcript...",
            expanded=True
        ) as status:

            transcript = get_transcript(
                url
            )

            save_transcript(
                transcript,
                transcript_file
            )

            status.update(
                label="Transcript ready.",
                state="complete"
            )


        transcript_text = transcript_to_text(
            transcript
        )

        with st.expander(
            "📄 View Transcript"
        ):
            st.write(
                transcript_text
            )


        # -------------------------------------------------
        # CAPTIONS
        # -------------------------------------------------

        if make_captions:

            with st.status(
                "Generating captions...",
                expanded=True
            ) as status:

                words = transcript_to_words(
                    transcript
                )

                make_ass(
                    words,
                    subtitle_file
                )

                status.update(
                    label="Captions generated.",
                    state="complete"
                )


        # -------------------------------------------------
        # VERTICAL VIDEO
        # -------------------------------------------------

        working_video = source_video

        if make_vertical:

            with st.status(
                "Converting video to vertical 9:16...",
                expanded=True
            ) as status:

                resize_vertical(
                    source_video,
                    vertical_video
                )

                working_video = vertical_video

                status.update(
                    label="Vertical video ready.",
                    state="complete"
                )


        # -------------------------------------------------
        # BURN CAPTIONS
        # -------------------------------------------------

        if make_captions:

            with st.status(
                "Burning captions...",
                expanded=True
            ) as status:

                burn_subtitles(
                    working_video,
                    subtitle_file,
                    final_file
                )

                working_video = final_file

                status.update(
                    label="Captions burned.",
                    state="complete"
                )


        # -------------------------------------------------
        # FINAL VIDEO
        # -------------------------------------------------

        if os.path.exists(
            working_video
        ):

            st.subheader(
                "🎥 Final Video"
            )

            st.video(
                working_video
            )

            with open(
                working_video,
                "rb"
            ) as f:

                st.download_button(
                    "⬇️ Download Final Video",
                    data=f,
                    file_name="final_video.mp4",
                    mime="video/mp4"
                )


        # -------------------------------------------------
        # TRANSCRIPT DOWNLOAD
        # -------------------------------------------------

        if os.path.exists(
            transcript_file
        ):

            with open(
                transcript_file,
                "rb"
            ) as f:

                st.download_button(
                    "📄 Download Transcript",
                    data=f,
                    file_name="transcript.txt",
                    mime="text/plain"
                )


        # -------------------------------------------------
        # TEXT TO SPEECH
        # -------------------------------------------------

        st.divider()

        st.subheader(
            "🔊 Text to Speech"
        )

        tts_text = st.text_area(
            "Text for voice",
            value=transcript_text,
            height=180
        )

        if st.button(
            "🔊 Generate Voice"
        ):

            with st.spinner(
                "Generating voice..."
            ):

                make_tts(
                    tts_text,
                    voice,
                    speed,
                    audio_file
                )

            st.success(
                "Voice generated."
            )

            with open(
                audio_file,
                "rb"
            ) as f:

                st.download_button(
                    "⬇️ Download Voice",
                    data=f,
                    file_name="voice.mp3",
                    mime="audio/mpeg"
                )


    except Exception as e:

        st.error(
            "Processing failed."
        )

        st.exception(
            e
        )


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "Shorts Maker • Free processing"
)
