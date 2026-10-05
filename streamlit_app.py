import os, re, json, sys, subprocess, textwrap, tempfile, requests
import streamlit as st

GEMINI_MODEL = "gemini-3.8-flash"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
GEMINI_KEY = st.secrets.get("GEMINI_KEY", "")
PIXABAY_KEY = st.secrets.get("PIXABAY_KEY", "")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-800:])
    return r.stdout


def get_transcript(url):
    vid = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url).group(1)
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        data = YouTubeTranscriptApi().fetch(vid)
        return " ".join(s.text for s in data)
    except AttributeError:
        data = YouTubeTranscriptApi.get_transcript(vid)
        return " ".join(s["text"] for s in data)


def stock_clip(query, path):
    for q in (query, query.split()[0], "lifestyle"):
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": PIXABAY_KEY, "q": q, "per_page": 10, "safesearch": "true"},
            timeout=30,
        ).json()
        for hit in r.get("hits", []):
            vids = hit.get("videos", {})
            for size in ("medium", "small", "large", "tiny"):
                link = vids.get(size, {}).get("url")
                if link:
                    with open(path, "wb") as fh:
                        fh.write(requests.get(link, timeout=60).content)
                    return True
    return False


def make_short(text, voice, bar):
    bar.progress(0.1, "Naya script likh raha hoon")
    from google import genai
    client = genai.Client(api_key=GEMINI_KEY)
    prompt = f"""
Below is the transcript of a YouTube video. Understand its topic and flow, then write a
completely NEW, original YouTube Short script (35-45 seconds, about 100-120 words) on the
same topic in your own words. Do not copy sentences from the transcript.

Style: natural human speech like a real creator talking, casual fillers (so, like, honestly,
basically), a bit meandering, personal-opinion framing, mild edgy tone is fine (censor words
like sh*t). NO AI cliches like "isn't just X, it's Y", "The best part?", or forced superlatives.
Start with a strong hook in the first line. Add one personal opinion or angle of your own.

Split the script into 6-9 short lines. For each line give 2-3 simple English keywords for a
stock video search (example: "man doing squats gym").

Return ONLY JSON in this format:
[{{"line": "...", "keywords": "..."}}]

TRANSCRIPT:
{text[:12000]}
"""
    resp = client.models.generate_content(
        model=GEMINI_MODEL, contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    scenes = json.loads(re.sub(r"```json|```", "", resp.text).strip())

    work = tempfile.mkdtemp()
    p = lambda n: os.path.join(work, n)
    parts = []
    for i, sc in enumerate(scenes):
        bar.progress(0.2 + 0.7 * i / len(scenes), f"Scene {i + 1}/{len(scenes)}")
        line = sc["line"].strip()
        with open(p(f"{i}_v.txt"), "w") as fh:
            fh.write(line)
        run([sys.executable, "-m", "edge_tts", "--file", p(f"{i}_v.txt"),
             "--voice", voice, "--write-media", p(f"{i}.mp3")])
        dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", p(f"{i}.mp3")]).strip())
        if not stock_clip(sc["keywords"], p(f"{i}.mp4")):
            raise RuntimeError("Stock clip nahi mili: " + sc["keywords"])
        with open(p(f"{i}.txt"), "w") as fh:
            fh.write(textwrap.fill(line, 22))
        vf = ("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
              f"drawtext=fontfile={FONT}:textfile={p(f'{i}.txt')}:fontsize=60:fontcolor=white:"
              "borderw=6:bordercolor=black:line_spacing=10:x=(w-text_w)/2:y=h*0.62")
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", p(f"{i}.mp4"), "-i", p(f"{i}.mp3"),
             "-t", str(dur), "-vf", vf, "-map", "0:v", "-map", "1:a", "-r", "30",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", p(f"s{i}.mp4")])
        parts.append(f"s{i}.mp4")

    with open(p("list.txt"), "w") as fh:
        for x in parts:
            fh.write(f"file '{x}'\n")
    final = p("final_short.mp4")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", p("list.txt"),
         "-c", "copy", final])
    with open(final, "rb") as fh:
        video = fh.read()
    return video, "\n".join(s["line"] for s in scenes)


st.set_page_config(page_title="Free Shorts Maker")
st.title("Free Shorts Maker")

url = st.text_input("YouTube link (optional)")
transcript = st.text_area("Ya yahan transcript paste karo (zyada reliable)", height=160)
voice = st.selectbox("Voice", ["en-US-GuyNeural", "en-US-JennyNeural", "en-US-AriaNeural"])

if st.button("Short banao", type="primary"):
    if not GEMINI_KEY or not PIXABAY_KEY:
        st.error("App Settings > Secrets me GEMINI_KEY aur PIXABAY_KEY daalo.")
        st.stop()
    text = transcript.strip()
    if not text:
        if not url.strip():
            st.error("Transcript paste karo ya YouTube link daalo.")
            st.stop()
        try:
            text = get_transcript(url)
        except Exception:
            st.error("Link se transcript nahi aaya. YouTube me Show transcript se copy karke paste karo.")
            st.stop()
    bar = st.progress(0.0, "Shuru")
    try:
        video, script = make_short(text, voice, bar)
    except Exception as e:
        st.error(f"Error: {e}")
        st.stop()
    bar.progress(1.0, "Tayyar!")
    st.video(video)
    st.download_button("Video download karo", video, "short.mp4", "video/mp4")
    st.text_area("Script", script, height=220)
