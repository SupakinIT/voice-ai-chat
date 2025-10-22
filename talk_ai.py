import os
import time
import json
import tempfile
import requests
import speech_recognition as sr
import pyttsx3
from dotenv import load_dotenv

# ========== CONFIG ==========
MODEL_ID = "deepseek/deepseek-chat"   # โมเดล DeepSeek บน OpenRouter
API_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 30

LANG_STT = "th-TH"   # ฟังภาษาไทย
LANG_TTS = "th"      # พูดภาษาไทย (สำหรับ gTTS)
USE_GTTS_FALLBACK = True
# ===========================

load_dotenv()
API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

r = sr.Recognizer()
engine = pyttsx3.init()

def select_thai_voice_if_available() -> bool:
    """เลือกเสียงไทยบน Windows ถ้ามี (SAPI5 via pyttsx3)"""
    try:
        for v in engine.getProperty('voices'):
            name = (getattr(v, 'name', '') or '').lower()
            id_ = (getattr(v, 'id', '') or '').lower()
            # บางเครื่องไม่มีภาษาในเมตาดาต้า ให้จับจากชื่อ/ไอดีแทน
            if 'thai' in name or 'th' in name or 'thai' in id_ or 'th' in id_:
                engine.setProperty('voice', v.id)
                rate = engine.getProperty('rate')
                engine.setProperty('rate', max(160, rate))  # ปรับความเร็วเล็กน้อยให้อ่านชัด
                return True
    except Exception:
        pass
    return False

HAS_THAI_VOICE = select_thai_voice_if_available()

def ai_reply(prompt: str) -> str:
    """ถาม DeepSeek ผ่าน OpenRouter และให้ตอบเป็นไทย"""
    if not API_KEY:
        return "❌ ยังไม่พบ OPENROUTER_API_KEY ในไฟล์ .env ครับ"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        # header เสริม (บางครั้งช่วยเรื่อง rate-limit/ระบุแอป)
        "HTTP-Referer": "http://localhost",
        "X-Title": "Windows Voice Assistant (TH)"
    }
    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Always reply in Thai."},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return f"❌ API Error (HTTP {resp.status_code}) : {resp.text[:200]}"
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        return f"❌ ต่อ API ไม่ได้: {e}"
    except Exception as e:
        return f"❌ ข้อผิดพลาดไม่ทราบสาเหตุ: {e}"

def speak(text: str):
    """พูดออกลำโพง: ใช้ pyttsx3 ก่อน ถ้าไม่มีเสียงไทยค่อย gTTS"""
    if not text:
        return
    try:
        if HAS_THAI_VOICE:
            engine.say(text)
            engine.runAndWait()
        elif USE_GTTS_FALLBACK:
            from gtts import gTTS
            from playsound import playsound
            tts = gTTS(text=text, lang=LANG_TTS)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
                path = f.name
            tts.save(path)
            # playsound บน Windows เล่น mp3 ได้เลย
            playsound(path)
            try:
                os.remove(path)
            except Exception:
                pass
        else:
            # ถ้าไม่มีไทยทั้งสองแบบ ก็อ่านด้วยเสียง default
            engine.say(text)
            engine.runAndWait()
    except Exception as e:
        print("TTS error:", e)

def list_mics():
    """พิมพ์รายการไมโครโฟน (ช่วยเลือก device_index ถ้ามีหลายตัว)"""
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        print("\n=== Microphone devices ===")
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) > 0:
                print(f"[{i}] {info.get('name')}")
        p.terminate()
    except Exception as e:
        print("List mic error:", e)

def listen(device_index=None) -> str:
    """กด Enter เพื่อเริ่มฟังครั้งละประโยค (หลีกเลี่ยงไมค์เปิดตลอด)"""
    with sr.Microphone(device_index=device_index) as source:
        print("🎙 พูดได้เลยครับ (กำลังฟัง)...")
        r.adjust_for_ambient_noise(source, duration=0.5)
        audio = r.listen(source)
    try:
        text = r.recognize_google(audio, language=LANG_STT)
        print("🗣 คุณพูดว่า:", text)
        return text
    except sr.UnknownValueError:
        print("❌ ไม่เข้าใจเสียงพูดครับ")
        return ""
    except sr.RequestError as e:
        print("⚠️ บริการรู้จำเสียงมีปัญหา:", e)
        return ""

if __name__ == "__main__":
    # (ไม่บังคับ) แสดงรายการไมค์เพื่อเลือก
    # list_mics()
    # ถ้าอยากล็อกไมค์ตัวใดตัวหนึ่ง ให้ใส่ index เช่น device_index=1
    DEVICE_INDEX = None

    print("=== DeepSeek Thai Voice Assistant (Windows) ===")
    print("เคล็ดลับ: พิมพ์ Enter เพื่อเริ่มฟัง, พูดว่า 'หยุด' หรือ 'ออก' เพื่อจบโปรแกรม\n")

    try:
        while True:
            input("กด Enter เพื่อเริ่มฟัง… ")
            cmd = listen(device_index=DEVICE_INDEX)
            if not cmd:
                continue
            if cmd.strip().lower() in {"หยุด", "ออก", "จบ", "พอ", "บาย"}:
                print("👋 บายครับ")
                speak("บายครับ")
                break

            reply = ai_reply(cmd)
            print("🤖 AI:", reply)
            speak(reply)
    except KeyboardInterrupt:
        print("\n👋 จบการทำงาน")
