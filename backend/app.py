# app.py (ฉบับอัปเดต)

import os
import io
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Generator
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from fastapi.staticfiles import StaticFiles

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import requests
from gtts import gTTS
import re

BACKEND_DIR = Path(__file__).parent
FRONTEND_DIR = (BACKEND_DIR / ".." / "frontend").resolve()

SAFE_CHARS = re.compile(r"[^A-Za-z0-9._\- ]+")
# ===== Load env =====
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
MODEL_ID = os.getenv("MODEL_ID", "deepseek/deepseek-chat").strip()
API_URL = "https://openrouter.ai/api/v1/chat/completions"

if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY not found in .env")


def _sanitize_session_id(name: str) -> str:
    name = name.strip()
    name = SAFE_CHARS.sub("-", name)
    name = re.sub(r"\s+", " ", name)  # บีบ space ซ้อน
    if not name:
        raise HTTPException(status_code=400, detail="session_id must not be empty")
    return name

# ===== FastAPI App =====
app = FastAPI(title="DeepSeek Thai Voice API", version="1.1")

# CORS: โปรดล็อกโดเมนตอนโปรดักชัน
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # TODO: เปลี่ยนเป็นโดเมนจริงของคุณ
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve static (สำหรับ favicon)
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    ico = STATIC_DIR / "favicon.ico"
    if ico.exists():
        return FileResponse(str(ico))
    return Response(status_code=204)

# ====== Simple persistent session store ======
DATA_DIR = Path(__file__).parent / "data" / "sessions"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _session_path(session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
    return DATA_DIR / f"{safe}.json"

def load_history(session_id: str):
    p = _session_path(session_id)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))  # <-- สำคัญ
    return []

def save_history(session_id: str, history):
    p = _session_path(session_id)
    p.write_text(json.dumps(history, ensure_ascii=False, indent=0), encoding="utf-8")  # <-- สำคัญ

def append_exchange(session_id: str, user_text: str, assistant_text: str) -> None:
    history = load_history(session_id)
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": assistant_text})
    # จำกัดความยาวความจำ (เช่น เก็บ 40 ข้อความล่าสุด)
    history = history[-40:]
    save_history(session_id, history)

# ====== LLM helpers ======
HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://your-app.local",
    "X-Title": "DeepSeek Voice Web",
}

def call_llm(messages: List[Dict[str, str]], stream: bool = False):
    payload = {
        "model": MODEL_ID,
        "messages": messages,
        **({"stream": True} if stream else {}),  # เปิดโหมดสตรีมเมื่อขอ
    }
    r = requests.post(API_URL, headers=HEADERS, json=payload, timeout=60, stream=stream)
    return r

def tts_mp3_bytes(text: str, lang: str = "th") -> bytes:
    tts = gTTS(text=text, lang=lang)
    buf = io.BytesIO()
    tts.write_to_fp(buf)
    buf.seek(0)
    return buf.read()

# ====== Routes ======

@app.get("/")
def root():
    return {"ok": True, "message": "DeepSeek Thai Voice API (stream + memory ready)"}

@app.post("/api/chat")
def chat(prompt: str = Form(...), session_id: str = Form("default")):
    """ไม่สตรีม: ส่ง prompt -> ได้ข้อความทีเดียว และบันทึกประวัติ"""
    history = load_history(session_id)
    # ใส่ system message ให้ตอบไทยเสมอ
    messages = [{"role": "system", "content": "You are a helpful assistant. Reply in Thai."}] + history + [
        {"role": "user", "content": prompt}
    ]
    r = call_llm(messages, stream=False)
    if r.status_code != 200:
        return JSONResponse({"reply": f"❌ API Error {r.status_code}: {r.text[:200]}"},
                            status_code=200)
    data = r.json()
    reply = data["choices"][0]["message"]["content"]
    append_exchange(session_id, prompt, reply)
    return JSONResponse({"reply": reply})

@app.post("/api/chat_and_say")
def chat_and_say(prompt: str = Form(...), session_id: str = Form("default")):
    """ไม่สตรีม: โต้ตอบ + ส่งเสียง MP3 กลับ และบันทึกประวัติ"""
    history = load_history(session_id)
    messages = [{"role": "system", "content": "You are a helpful assistant. Reply in Thai."}] + history + [
        {"role": "user", "content": prompt}
    ]
    r = call_llm(messages, stream=False)
    if r.status_code != 200:
        txt = f"❌ API Error {r.status_code}: {r.text[:200]}"
        audio = tts_mp3_bytes(txt, lang="th")
        return StreamingResponse(io.BytesIO(audio), media_type="audio/mpeg", headers={"X-Text": txt})

    data = r.json()
    reply = data["choices"][0]["message"]["content"]
    append_exchange(session_id, prompt, reply)
    audio = tts_mp3_bytes(reply, lang="th")
    return StreamingResponse(io.BytesIO(audio), media_type="audio/mpeg", headers={"X-Text": reply})

@app.post("/api/say")
def say(text: str = Form(...), lang: Optional[str] = Form("th")):
    """รับข้อความ -> สร้างเสียง MP3 (แยกต่างหาก)"""
    audio = tts_mp3_bytes(text, lang=lang or "th")
    return StreamingResponse(io.BytesIO(audio), media_type="audio/mpeg")

# ============== NEW: Streaming replies ==============

def sse_lines_to_chunks(resp):
    """
    อ่าน SSE จาก OpenRouter แล้วคืน content ทีละชิ้น (UTF-8 แท้)
    """
    for raw in resp.iter_lines(decode_unicode=False):   # ⬅️ อ่านเป็น bytes
        if not raw:
            continue
        line = raw.decode("utf-8", errors="ignore")     # ⬅️ บังคับ UTF-8
        if not line.startswith("data: "):
            continue
        data = line[len("data: "):].strip()
        if data == "[DONE]":
            break

        try:
            obj = json.loads(data)
        except Exception:
            continue

        delta = obj.get("choices", [{}])[0].get("delta", {})
        piece = delta.get("content") or ""
        if piece:
            yield piece     

@app.post("/api/chat_stream")
def chat_stream(prompt: str = Form(...), session_id: str = Form("default")):
    history = load_history(session_id)
    messages = [{"role": "system", "content": "You are a helpful assistant. Reply in Thai."}] + history + [
        {"role": "user", "content": prompt}
    ]
    try:
        resp = call_llm(messages, stream=True)
        if resp.status_code != 200:
            err = f"❌ API Error {resp.status_code}: {resp.text[:200]}"
            return StreamingResponse((chunk for chunk in [err]), media_type="text/plain")

        def generate():
            collected = []
            for chunk in sse_lines_to_chunks(resp):
                collected.append(chunk)
                yield chunk
            full = "".join(collected).strip()
            append_exchange(session_id, prompt, full)

        return StreamingResponse(generate(),
                         media_type="text/plain; charset=utf-8",
                         headers={"Cache-Control": "no-store"})
    except Exception as e:
        return StreamingResponse((c for c in [f"❌ ต่อ API ไม่ได้: {e}"]), media_type="text/plain")

# ============== (Optional) history helpers ==============

@app.get("/api/history")
def get_history(session_id: str = "default"):
    return JSONResponse({"session_id": session_id, "history": load_history(session_id)})

@app.post("/api/clear_history")
def clear_history(session_id: str = Form("default")):
    save_history(session_id, [])
    return {"ok": True}


def _list_session_files():
    return sorted(
        DATA_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

@app.get("/api/sessions")
def list_sessions():
    """คืนรายการห้องแชตทั้งหมด (ล่าสุดอยู่บนสุด) + พรีวิวชื่อ"""
    items = []
    for p in _list_session_files():
        sid = p.stem
        try:
            hist = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            hist = []
        # ตั้งชื่อจากข้อความ user ตัวแรก ถ้าไม่มีให้ใช้เวลา
        title = None
        for m in hist:
            if m.get("role") == "user" and m.get("content"):
                title = m["content"][:60]
                break
        if not title:
            ts = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            title = f"New chat • {ts}"
        items.append({
            "session_id": sid,
            "title": title,
            "updated_at": p.stat().st_mtime,
        })
    return {"sessions": items}

@app.post("/api/sessions")
async def create_session(request: Request,
                         session_id_form: Optional[str] = Form(None),
                         session_id_json: Optional[str] = Body(None)):
    """
    สร้างห้องใหม่:
      - รองรับ JSON: {"session_id": "..."} หรือ body ว่าง
      - รองรับ form: session_id=<...>
      - ถ้าไม่ส่งมาจะ gen id ให้เอง
    """
    session_id = session_id_json or session_id_form
    if not session_id:
        session_id = "chat-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:4]

    p = _session_path(session_id)
    if not p.exists():
        save_history(session_id, [])
    return {"ok": True, "session_id": session_id}

@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    """ลบไฟล์ห้องแชตทั้งห้อง"""
    p = _session_path(session_id)
    if p.exists():
        p.unlink()
    return {"ok": True}

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")