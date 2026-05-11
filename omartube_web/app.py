"""
╔══════════════════════════════════════════════════════════════╗
║              OMARTUBE WEB — v1.0                             ║
║   Download Videos & Audio from YouTube, TikTok, Twitter/X    ║
║              Flask + yt-dlp Backend                          ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import json
import uuid
import threading
import time
import glob
# pyrefly: ignore [missing-import]
from flask import Flask, render_template, request, jsonify, send_from_directory

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

app = Flask(__name__)

# ── Download storage ──
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── Track active downloads ──
downloads = {}  # task_id -> { status, progress, speed, eta, filename, error, title, platform }

# ═══════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/download", methods=["POST"])
def start_download():
    """Start a download task and return a task_id for polling."""
    if yt_dlp is None:
        return jsonify({"error": "yt-dlp is not installed. Run: pip install yt-dlp"}), 500

    data = request.json
    url = data.get("url", "").strip()
    dl_type = data.get("type", "video")  # "video" or "audio"
    quality = data.get("quality", "720p")
    audio_format = data.get("audioFormat", "mp3")
    bitrate = data.get("bitrate", "192")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    task_id = str(uuid.uuid4())[:8]
    downloads[task_id] = {
        "status": "starting",
        "progress": 0,
        "speed": "",
        "eta": "",
        "filename": None,
        "error": None,
        "title": "",
        "platform": detect_platform(url),
    }

    if dl_type == "video":
        t = threading.Thread(target=download_video, args=(task_id, url, quality), daemon=True)
    else:
        t = threading.Thread(target=download_audio, args=(task_id, url, audio_format, bitrate), daemon=True)
    t.start()

    return jsonify({"taskId": task_id})


@app.route("/api/status/<task_id>")
def get_status(task_id):
    """Poll the status of a download task."""
    task = downloads.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@app.route("/api/file/<task_id>")
def serve_file(task_id):
    """Serve the downloaded file to the user."""
    task = downloads.get(task_id)
    if not task or not task.get("filename"):
        return jsonify({"error": "File not found"}), 404

    filename = task["filename"]
    # Strip the task_id prefix (e.g. "3667c4ae_Title.mp4" -> "Title.mp4")
    clean_name = filename
    if filename.startswith(task_id + "_"):
        clean_name = filename[len(task_id) + 1:]
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True, download_name=clean_name)


@app.route("/api/info", methods=["POST"])
def get_info():
    """Extract video metadata (title, thumbnail, duration) without downloading."""
    if yt_dlp is None:
        return jsonify({"error": "yt-dlp is not installed."}), 500

    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return jsonify({"error": "Could not extract info from this URL."}), 400

        # Format duration
        duration_sec = info.get("duration", 0)
        if duration_sec:
            mins, secs = divmod(int(duration_sec), 60)
            hours, mins = divmod(mins, 60)
            if hours:
                dur_str = f"{hours}:{mins:02d}:{secs:02d}"
            else:
                dur_str = f"{mins}:{secs:02d}"
        else:
            dur_str = ""

        return jsonify({
            "title": info.get("title", "Unknown"),
            "thumbnail": info.get("thumbnail", ""),
            "duration": dur_str,
            "uploader": info.get("uploader", ""),
            "platform": detect_platform(url),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ═══════════════════════════════════════════════════════════════
# DOWNLOAD LOGIC
# ═══════════════════════════════════════════════════════════════

def detect_platform(url):
    url_lower = url.lower()
    if any(x in url_lower for x in ["youtube.com", "youtu.be"]):
        return "youtube"
    elif "tiktok.com" in url_lower:
        return "tiktok"
    elif any(x in url_lower for x in ["twitter.com", "x.com"]):
        return "twitter"
    return "other"


def make_progress_hook(task_id):
    def hook(d):
        task = downloads[task_id]
        if d["status"] == "downloading":
            downloaded = d.get("downloaded_bytes", 0)
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            if total and total > 0:
                pct = min(downloaded / total, 1.0)
            else:
                pct_str = d.get("_percent_str", "0%").strip().replace("%", "")
                try:
                    pct = float(pct_str) / 100.0
                except ValueError:
                    pct = 0

            speed = d.get("speed")
            speed_str = ""
            if speed:
                if speed > 1_048_576:
                    speed_str = f"{speed / 1_048_576:.1f} MB/s"
                elif speed > 1024:
                    speed_str = f"{speed / 1024:.1f} KB/s"
                else:
                    speed_str = f"{speed:.0f} B/s"

            eta = d.get("eta")
            eta_str = ""
            if eta:
                mins, secs = divmod(int(eta), 60)
                eta_str = f"{mins}m {secs}s" if mins else f"{secs}s"

            task["status"] = "downloading"
            task["progress"] = round(pct * 100, 1)
            task["speed"] = speed_str
            task["eta"] = eta_str

        elif d["status"] == "finished":
            task["status"] = "processing"
            task["progress"] = 100
            task["speed"] = ""
            task["eta"] = ""
    return hook


def download_video(task_id, url, quality):
    task = downloads[task_id]
    try:
        height_map = {"1080p": 1080, "720p": 720, "480p": 480, "360p": 360 ,"240p": 240}
        max_height = height_map.get(quality, 720)

        format_str = (
            f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/"
            f"bestvideo+bestaudio/best"
        )

        # Use task_id prefix so filenames are unique
        outtmpl = os.path.join(DOWNLOAD_DIR, f"{task_id}_%(title)s.%(ext)s")

        ydl_opts = {
            "outtmpl": outtmpl,
            "format": format_str,
            "merge_output_format": "mp4",
            "progress_hooks": [make_progress_hook(task_id)],
            "noplaylist": True,
            "retries": 3,
            "fragment_retries": 3,
            "ignoreerrors": False,
            "quiet": True,
            "no_warnings": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if info is None:
            task["status"] = "error"
            task["error"] = "Could not extract video information."
            return

        task["title"] = info.get("title", "Unknown")

        # Find the actual downloaded file
        found = find_downloaded_file(task_id)
        if found:
            task["filename"] = os.path.basename(found)
            task["status"] = "done"
        else:
            task["status"] = "error"
            task["error"] = "Download finished but file not found. Is FFmpeg installed?"

    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)


def download_audio(task_id, url, audio_fmt, bitrate):
    task = downloads[task_id]
    try:
        outtmpl = os.path.join(DOWNLOAD_DIR, f"{task_id}_%(title)s.%(ext)s")

        ydl_opts = {
            "outtmpl": outtmpl,
            "format": "bestaudio/best",
            "progress_hooks": [make_progress_hook(task_id)],
            "noplaylist": True,
            "retries": 3,
            "fragment_retries": 3,
            "ignoreerrors": False,
            "quiet": True,
            "no_warnings": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_fmt,
                "preferredquality": bitrate,
            }],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if info is None:
            task["status"] = "error"
            task["error"] = "Could not extract audio information."
            return

        task["title"] = info.get("title", "Unknown")

        found = find_downloaded_file(task_id)
        if found:
            task["filename"] = os.path.basename(found)
            task["status"] = "done"
        else:
            task["status"] = "error"
            task["error"] = "Download finished but file not found. Is FFmpeg installed?"

    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)


def find_downloaded_file(task_id):
    """Find the most recently modified file matching the task_id prefix."""
    pattern = os.path.join(DOWNLOAD_DIR, f"{task_id}_*")
    files = glob.glob(pattern)
    if not files:
        return None
    # Return the most recently modified
    return max(files, key=os.path.getmtime)


# ═══════════════════════════════════════════════════════════════
# CLEANUP — delete files older than 30 minutes
# ═══════════════════════════════════════════════════════════════

def cleanup_old_downloads():
    while True:
        time.sleep(300)  # Check every 5 minutes
        cutoff = time.time() - 1800  # 30 minutes
        try:
            for f in os.listdir(DOWNLOAD_DIR):
                fp = os.path.join(DOWNLOAD_DIR, f)
                if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Start cleanup thread
    threading.Thread(target=cleanup_old_downloads, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  +=========================================+")
    print(f"  |   OmarTube Web -- http://localhost:{port}  |")
    print(f"  +=========================================+\n")
    app.run(host="0.0.0.0", port=port, debug=(port == 5000), use_reloader=False)
