import os
import re
import sys
import time
import uuid
import json
import shutil
import asyncio
import threading
import requests
from typing import Dict, Any

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Auto-configure static ffmpeg binaries on any platform
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

import yt_dlp

from ShopeeExtractor import ShopeeExtractor
from ThreadsExtractor import ThreadsExtractor

app = FastAPI(title="Mini IDM Cloud")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# Mount static files if present
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Task tracking
tasks: Dict[str, Dict[str, Any]] = {}
bot_username = ""

# Model
class DownloadRequest(BaseModel):
    url: str
    format: str = "best"


# Auto-cleanup old files (> 1 hour)
def cleanup_old_files():
    now = time.time()
    try:
        for fname in os.listdir(DOWNLOAD_DIR):
            fpath = os.path.join(DOWNLOAD_DIR, fname)
            if os.path.isfile(fpath) and (now - os.path.getmtime(fpath) > 3600):
                os.remove(fpath)
    except Exception:
        pass


def download_worker(task_id: str, url: str, fmt: str):
    cleanup_old_files()
    tasks[task_id]["status"] = "Downloading"
    tasks[task_id]["percent"] = 5
    tasks[task_id]["status_text"] = "Ina-analyze ang video stream..."

    try:
        # 1. Native Shopee
        if ShopeeExtractor.is_shopee(url):
            info = ShopeeExtractor.extract(url)
            tasks[task_id]["title"] = info["title"]
            tasks[task_id]["status_text"] = "Kinukuha ang unwatermarked master file..."
            out_name = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', info['title'])[:40]}_{task_id}.mp4"
            out_path = os.path.join(DOWNLOAD_DIR, out_name)

            # Download direct stream
            r = requests.get(info["url"], stream=True, timeout=60)
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(99, int((downloaded / total) * 100))
                            tasks[task_id]["percent"] = pct
                            tasks[task_id]["speed"] = f"{round(downloaded / (1024 * 1024), 1)} MB"

            size_mb = round(os.path.getsize(out_path) / (1024 * 1024), 2)
            tasks[task_id]["percent"] = 100
            tasks[task_id]["status"] = "Completed"
            tasks[task_id]["filename"] = out_name
            tasks[task_id]["fileUrl"] = f"/files/{out_name}"
            tasks[task_id]["size"] = f"{size_mb} MB"
            return

        # 2. Native Threads
        if ThreadsExtractor.is_threads(url):
            info = ThreadsExtractor.extract(url)
            tasks[task_id]["title"] = info["title"]
            tasks[task_id]["status_text"] = "Dina-download ang HD Threads video..."
            out_name = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', info['title'])[:40]}_{task_id}.mp4"
            out_path = os.path.join(DOWNLOAD_DIR, out_name)

            r = requests.get(info["url"], stream=True, timeout=60)
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(99, int((downloaded / total) * 100))
                            tasks[task_id]["percent"] = pct
                            tasks[task_id]["speed"] = f"{round(downloaded / (1024 * 1024), 1)} MB"

            size_mb = round(os.path.getsize(out_path) / (1024 * 1024), 2)
            tasks[task_id]["percent"] = 100
            tasks[task_id]["status"] = "Completed"
            tasks[task_id]["filename"] = out_name
            tasks[task_id]["fileUrl"] = f"/files/{out_name}"
            tasks[task_id]["size"] = f"{size_mb} MB"
            return

        # 3. yt-dlp Engine (TikTok, FB Reels, IG, YouTube, X)
        out_template = os.path.join(DOWNLOAD_DIR, f"%(title).40s_{task_id}.%(ext)s")

        def ydl_hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total > 0:
                    pct = min(98, int((downloaded / total) * 100))
                    tasks[task_id]["percent"] = pct
                speed = d.get("speed")
                if speed:
                    tasks[task_id]["speed"] = f"{round(speed / (1024 * 1024), 2)} MB/s"
                tasks[task_id]["status_text"] = "Dina-download ang media..."
            elif d.get("status") == "finished":
                tasks[task_id]["status_text"] = "Pinoproseso ang final file..."
                tasks[task_id]["percent"] = 99

        ydl_opts = {
            "outtmpl": out_template,
            "progress_hooks": [ydl_hook],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        if fmt == "mp3":
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]
            })
        elif fmt == "720p":
            ydl_opts["format"] = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        else:
            ydl_opts["format"] = "bestvideo+bestaudio/best"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            meta = ydl.extract_info(url, download=True)
            tasks[task_id]["title"] = meta.get("title", "Video")

        # Find final file created
        found_file = None
        for f in os.listdir(DOWNLOAD_DIR):
            if task_id in f and not f.endswith(".part") and not f.endswith(".ytdl"):
                found_file = f
                break

        if not found_file:
            raise Exception("Hindi nahanap ang final output video file.")

        final_path = os.path.join(DOWNLOAD_DIR, found_file)
        size_mb = round(os.path.getsize(final_path) / (1024 * 1024), 2)
        tasks[task_id]["percent"] = 100
        tasks[task_id]["status"] = "Completed"
        tasks[task_id]["filename"] = found_file
        tasks[task_id]["fileUrl"] = f"/files/{found_file}"
        tasks[task_id]["size"] = f"{size_mb} MB"

    except Exception as e:
        tasks[task_id]["status"] = "Error"
        tasks[task_id]["error"] = str(e)


# Web Routes
@app.get("/")
def serve_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file, media_type="text/html")
    return HTMLResponse("<h1>Mini IDM Cloud Ready</h1>")

@app.get("/manifest.json")
def serve_manifest():
    mf = os.path.join(STATIC_DIR, "manifest.json")
    if os.path.exists(mf):
        return FileResponse(mf, media_type="application/json")
    return JSONResponse({})

@app.get("/icon.png")
def serve_icon():
    ic = os.path.join(STATIC_DIR, "icon.png")
    if os.path.exists(ic):
        return FileResponse(ic, media_type="image/png")
    raise HTTPException(status_code=404)

@app.get("/qr.png")
def serve_qr():
    qr = os.path.join(STATIC_DIR, "qr.png")
    if os.path.exists(qr):
        return FileResponse(qr, media_type="image/png")
    raise HTTPException(status_code=404)

@app.get("/api/ping")
def ping():
    return {"status": "ok", "bot": bot_username}

@app.post("/api/download")
def start_download(req: DownloadRequest, bg: BackgroundTasks):
    if not req.url:
        raise HTTPException(status_code=400, detail="Invalid URL")
    task_id = uuid.uuid4().hex[:8]
    tasks[task_id] = {
        "id": task_id,
        "title": "Inihahanda...",
        "status": "Queued",
        "percent": 0,
        "speed": "-- MB/s",
        "size": "--",
        "filename": "",
        "fileUrl": "",
        "error": "",
        "created_at": time.time()
    }
    bg.add_task(download_worker, task_id, req.url, req.format)
    return {"id": task_id}

@app.get("/api/status")
def task_status(id: str):
    t = tasks.get(id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t

@app.get("/files/{filename}")
def download_file(filename: str):
    fpath = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(fpath, filename=filename, media_type="application/octet-stream")


# 24/7 Telegram Bot Thread
def telegram_bot_worker():
    global bot_username
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        token = "8935479317:AAGLvPWrVaJ9w7_GQaueRMVWi5OMLTkN5is"

    if not token:
        print("[TELEGRAM] No bot token provided. Bot disabled.")
        return

    print("[TELEGRAM] Starting Bot Worker...")
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
        if r.get("ok"):
            bot_username = r["result"].get("username", "")
            print(f"[TELEGRAM] Connected as @{bot_username}")
        else:
            print("[TELEGRAM] Token error:", r)
            return
    except Exception as e:
        print("[TELEGRAM] Cannot connect to API:", e)
        return

    offset = 0
    while True:
        try:
            res = requests.get(f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=20", timeout=25).json()
            if not res.get("ok"):
                time.sleep(3)
                continue

            for update in res.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("channel_post")
                if not msg:
                    continue

                chat_id = msg["chat"]["id"]
                text = (msg.get("text") or "").strip()

                if not text:
                    continue

                if text == "/start":
                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"👋 Kumusta! Ako si @{bot_username} — ang iyong 24/7 Mini IDM Downloader Bot!\n\nI-send lang sa akin ang link mula sa:\n• TikTok\n• FB Reels\n• Instagram\n• Threads\n• Shopee Video (No Watermark!)\n• YouTube\n• X (Twitter)\n\nAwtomatiko kong i-re-reply ang video sa iyo!"
                    })
                    continue

                m = re.search(r'https?://[^\s]+', text)
                if not m:
                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": "Pakisend ng valid video link (hal. TikTok, FB Reels, Threads, Shopee, YouTube, o X)."
                    })
                    continue

                url = m.group(0)
                rep = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "⏳ Inihahanda ang video... Sandali lang!"
                }).json()
                status_msg_id = rep.get("result", {}).get("message_id")

                threading.Thread(target=process_telegram_download, args=(token, chat_id, status_msg_id, url), daemon=True).start()

        except Exception as ex:
            time.sleep(3)


def process_telegram_download(token: str, chat_id: int, status_msg_id: int, url: str):
    tid = uuid.uuid4().hex[:8]
    try:
        if ShopeeExtractor.is_shopee(url):
            info = ShopeeExtractor.extract(url)
            tmp_file = os.path.join(DOWNLOAD_DIR, f"tg_{tid}.mp4")
            r = requests.get(info["url"], stream=True, timeout=60)
            with open(tmp_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk: f.write(chunk)
            send_tg_video(token, chat_id, status_msg_id, tmp_file, info["title"])
            return

        if ThreadsExtractor.is_threads(url):
            info = ThreadsExtractor.extract(url)
            tmp_file = os.path.join(DOWNLOAD_DIR, f"tg_{tid}.mp4")
            r = requests.get(info["url"], stream=True, timeout=60)
            with open(tmp_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk: f.write(chunk)
            send_tg_video(token, chat_id, status_msg_id, tmp_file, info["title"])
            return

        out_template = os.path.join(DOWNLOAD_DIR, f"tg_{tid}.%(ext)s")
        ydl_opts = {
            "outtmpl": out_template,
            "format": "best[filesize<50M]/bestvideo[height<=720]+bestaudio/best",
            "quiet": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            meta = ydl.extract_info(url, download=True)
            vtitle = meta.get("title", "Video")

        found = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(f"tg_{tid}") and not f.endswith(".part"):
                found = os.path.join(DOWNLOAD_DIR, f)
                break

        if found:
            send_tg_video(token, chat_id, status_msg_id, found, vtitle)
        else:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                "chat_id": chat_id, "text": "❌ Nabigong i-download ang video."
            })

    except Exception as e:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
            "chat_id": chat_id, "text": f"❌ Error: {str(e)[:150]}"
        })


def send_tg_video(token: str, chat_id: int, status_msg_id: int, file_path: str, caption: str):
    try:
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if size_mb > 50:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"⚠️ Mas malaki sa 50 MB ang video ({round(size_mb, 1)} MB). Limitasyon ito ng Telegram bot API."
            })
            return

        with open(file_path, "rb") as vf:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendVideo",
                data={"chat_id": chat_id, "caption": f"✅ {caption[:100]} - Na-download via Mini IDM"},
                files={"video": vf},
                timeout=120
            )

        if status_msg_id:
            requests.post(f"https://api.telegram.org/bot{token}/deleteMessage", json={
                "chat_id": chat_id, "message_id": status_msg_id
            })
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass


# Hugging Face Gradio SDK Compatibility (100% Free SDK!)
demo = None
try:
    import gradio as gr
    with gr.Blocks(title="Mini IDM Mobile", css="footer {visibility: hidden !important;}") as demo:
        gr.HTML("""
        <div style="width:100%; display:flex; justify-content:center; align-items:center; min-height:85vh; background:#090d16; padding:0;">
            <iframe src="/" style="width:100%; max-width:500px; height:85vh; border:none; border-radius:18px;"></iframe>
        </div>
        """)
    app = gr.mount_gradio_app(app, demo, path="/gradio")
except Exception as e:
    demo = None


@app.on_event("startup")
def on_startup():
    threading.Thread(target=telegram_bot_worker, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 7860 if "SPACE_ID" in os.environ else 5050))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
