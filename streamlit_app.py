import os, re, json, sys, math, asyncio, subprocess, tempfile, requests
import streamlit as st

GEMINI_MODEL = "gemini-3.8-flash"
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
GEMINI_KEY = st.secrets.get("GEMINI_KEY", "")
PIXABAY_KEY = st.secrets.get("PIXABAY_KEY", "")


def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
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


# ---------- captions ----------
CURSE = re.compile(r"(shit|fuck|bitch|asshole|bastard|dick|cunt)", re.I)


def mask(text):
    def m(mo):
        w = mo.group(1)
        idx = [k for k, c in enumerate(w) if c.lower() in "aeiou" and k > 0]
        i = idx[0] if idx else 1
        return w[:i] + "*" + w[i + 1:]
    return CURSE.sub(m, text)


def ts(t):
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def build_ass(words):
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,"
        "Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Cap,DejaVu Sans,78,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,"
        "100,100,0,0,1,8,2,2,60,60,560,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    groups, cur = [], []
    for w in words:
        cur.append(w)
        n = len(" ".join(x[2] for x in cur))
        if len(cur) >= 3 or n > 15 or re.search(r"[.?!]$", w[2]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    ev = []
    for g in groups:
        for j, w in enumerate(g):
            end = g[j + 1][0] if j < len(g) - 1 else w[1] + 0.08
            parts = []
            for k, x in enumerate(g):
                t = mask(x[2]).upper()
                if k == j:
                    parts.append("{\\c&H00FFFF&\\fscx112\\fscy112}" + t + "{\\r}")
                else:
                    parts.append(t)
            ev.append(f"Dialogue: 0,{ts(w[0])},{ts(end)},Cap,,0,0,0,,{' '.join(parts)}")
    return head + "\n".join(ev) + "\n"


# ---------- voice ----------
async def _tts(text, voice, rate, mp3):
    import edge_tts
    try:
        comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    except TypeError:
        comm = edge_tts.Communicate(text, voice, rate=rate)
    words = []
    with open(mp3, "wb") as fh:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                fh.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append((ch["offset"] / 1e7, ch["duration"] / 1e7, ch["text"]))
    return words


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", path]).strip())


# ---------- stock ----------
STOP = {"a", "the", "of", "and", "in", "on", "with", "man", "woman", "person", "people"}


def search_pool(q, used):
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": PIXABAY_KEY, "q": q, "per_page": 20, "safesearch": "true"},
        timeout=30,
    ).json()
    out = []
    for hit in r.get("hits", []):
        if hit["id"] in used:
            continue
        vids = hit.get("videos", {})
        link = next((vids[s]["url"] for s in ("medium", "small", "large", "tiny")
                     if vids.get(s, {}).get("url")), None)
        if link:
            out.append((hit["id"], hit.get("tags", ""), link))
    return out


def relevant(q, hit):
    qw = set(re.findall(r"[a-z]+", q.lower())) - STOP
    tw = set(re.findall(r"[a-z]+", hit[1].lower()))
    return bool(qw & tw)


def pick_clips(keywords, topic, n, used, work):
    cands = []
    for q in keywords:
        cands += [h for h in search_pool(q, used) if relevant(q, h)]
    if len(cands) < n:
        cands += search_pool(topic, used)
    seen, uniq = set(), []
    for h in cands:
        if h[0] not in seen:
            seen.add(h[0])
            uniq.append(h)
    uniq = uniq[:n]
    if not uniq:
        raise RuntimeError("Stock clip nahi mili: " + ", ".join(keywords))
    paths = []
    for h in uniq:
        used.add(h[0])
        path = os.path.join(work, f"clip_{h[0]}.mp4")
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(requests.get(h[2], timeout=60).content)
        paths.append(path)
    while len(paths) < n:
        paths.append(paths[len(paths) % len(uniq)])
    return paths


# ---------- main ----------
def make_short(text, voice, rate, angle, bar):
    bar.progress(0.05, "Script likh raha hoon")
    from google import genai
    client = genai.Client(api_key=GEMINI_KEY)
    angle_line = f'The creator\'s own angle/opinion to build the script around: "{angle}"' if angle.strip() else \
        "Add one clear personal opinion of your own."
    prompt = f"""
You are a YouTube Shorts scriptwriter. Below is the transcript of a source video.

STEP 1: Work out what the video actually teaches: the real topic, the key facts, products,
numbers and the main takeaway.
STEP 2: Write a NEW original Short script (35-45 seconds, 100-120 words) that delivers the same
useful information in your own words. It must make complete sense on its own and stay 100% on
topic. Structure: strong hook line, then 3-4 concrete points, then a short payoff/closer.
{angle_line}

Voice: a real person talking to a friend. Casual fillers (so, like, honestly, basically),
a bit run-on, personal-opinion framing, mild edgy language is encouraged (damn, hell, shit,
bullshit). Write profanity as the FULL word because a voice reads it aloud; captions get masked
automatically. Use profanity once or twice max, only where it lands naturally. No AI cliches
("isn't just X, it's Y", "The best part?", forced superlatives, dramatic ellipses).

Split into 6-9 short lines. For each line give "keywords": 2 stock-footage search phrases.
Stock libraries only have common things, so use common generic phrases for the topic, never rare
or abstract ones. Example: for a hand gripper line use ["forearm workout", "gym workout"], not
"hand gripper". Never use scenery or unrelated lifestyle terms. Put the most relevant first.

Also give "topic_keywords": one generic 2-word stock phrase for the whole video (e.g. "gym workout"),
a "title" under 70 characters, and "hashtags" (5 hashtags in one string).

Return ONLY JSON:
{{"title":"...","hashtags":"#a #b","topic_keywords":"...","scenes":[{{"line":"...","keywords":["...","..."]}}]}}

TRANSCRIPT:
{text[:12000]}
"""
    resp = client.models.generate_content(
        model=GEMINI_MODEL, contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    data = json.loads(re.sub(r"```json|```", "", resp.text).strip())
    scenes = data["scenes"]
    topic = data.get("topic_keywords", "gym workout")

    work = tempfile.mkdtemp()
    p = lambda n: os.path.join(work, n)
    words_all, chunks, wavs, used, t0 = [], [], [], set(), 0.0

    for i, sc in enumerate(scenes):
        bar.progress(0.15 + 0.6 * i / len(scenes), f"Scene {i + 1}/{len(scenes)}")
        line = sc["line"].strip()
        words = asyncio.run(_tts(line, voice, rate, p(f"{i}.mp3")))
        run(["ffmpeg", "-y", "-i", p(f"{i}.mp3"), "-ar", "44100", "-ac", "1", p(f"{i}.wav")])
        d = duration(p(f"{i}.wav"))
        if not words:
            toks = line.split()
            tot = sum(len(x) for x in toks)
            acc = 0.0
            for x in toks:
                dd = d * len(x) / tot
                words.append((acc, dd, x))
                acc += dd
        for w in words:
            words_all.append((t0 + w[0], t0 + w[0] + w[1], w[2]))
        wavs.append(p(f"{i}.wav"))
        t0 += d

        n = max(1, round(d / 2.3))
        kws = sc.get("keywords", [])
        if isinstance(kws, str):
            kws = [kws]
        clips = pick_clips(kws, topic, n, used, work)
        for k, c in enumerate(clips):
            out = p(f"c{i}_{k}.mp4")
            run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", c, "-t", f"{d / n:.3f}",
                 "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1",
                 "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
                 "-pix_fmt", "yuv420p", out])
            chunks.append(out)

    bar.progress(0.8, "Jod raha hoon")
    for name, items, ext in (("vlist.txt", chunks, "mp4"), ("alist.txt", wavs, "wav")):
        with open(p(name), "w") as fh:
            for x in items:
                fh.write(f"file '{x}'\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", p("vlist.txt"), "-c", "copy", p("v.mp4")])
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", p("alist.txt"), "-c", "copy", p("a.wav")])
    with open(p("subs.ass"), "w") as fh:
        fh.write(build_ass(words_all))

    bar.progress(0.88, "Captions laga raha hoon")
    run(["ffmpeg", "-y", "-i", "v.mp4", "-i", "a.wav",
         "-vf", f"ass=subs.ass:fontsdir={FONT_DIR}",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "25", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "final.mp4"], cwd=work)
    with open(p("final.mp4"), "rb") as fh:
        video = fh.read()
    script = "\n".join(s["line"] for s in scenes)
    return video, script, data.get("title", ""), data.get("hashtags", "")


st.set_page_config(page_title="Free Shorts Maker")
st.title("Free Shorts Maker")

url = st.text_input("YouTube link (optional)")
transcript = st.text_area("Ya yahan transcript paste karo (zyada reliable)", height=140)
angle = st.text_input("Apna angle / opinion (1 line, optional)")
voice = st.selectbox("Voice", ["en-US-AndrewNeural", "en-US-BrianNeural", "en-US-ChristopherNeural",
                               "en-US-GuyNeural", "en-US-AvaNeural", "en-US-JennyNeural"])
rate = st.selectbox("Speed", ["+8%", "+0%", "+15%"])

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
            st.error("Link se transcript nahi aaya. Show transcript se copy karke paste karo.")
            st.stop()
    bar = st.progress(0.0, "Shuru")
    try:
        video, script, title, tags = make_short(text, voice, rate, angle, bar)
    except Exception as e:
        st.error(f"Error: {e}")
        st.stop()
    bar.progress(1.0, "Tayyar!")
    st.video(video)
    st.download_button("Video download karo", video, "short.mp4", "video/mp4")
    st.text_area("Title", title, height=70)
    st.text_area("Hashtags", tags, height=70)
    st.text_area("Script", script, height=220)
