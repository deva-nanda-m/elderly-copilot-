import os
import re
import urllib.parse
import asyncio
import uuid
import httpx
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from google import genai
from google.genai import types


from pathlib import Path

# ---------------------------------------------------------
# LOAD ENVIRONMENT VARIABLES
# ---------------------------------------------------------

env_file = Path(__file__).resolve().parent / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)
else:
    load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is missing from the .env file"
    )

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


# ---------------------------------------------------------
# GEMINI CLIENT
# ---------------------------------------------------------

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ---------------------------------------------------------
# FASTAPI
# ---------------------------------------------------------

app = FastAPI(
    title="ElderCare Copilot API"
)


# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# REQUEST / RESPONSE MODELS
# ---------------------------------------------------------

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    audio_url: Optional[str] = None


class TtsRequest(BaseModel):
    text: str
    lang: str = "ml"


# ---------------------------------------------------------
# ELDERCARE SYSTEM PROMPT
# ---------------------------------------------------------

SYSTEM_PROMPT = """
You are ElderCare Copilot.

You are a warm, attentive, and caring conversational companion for elderly people in Kerala.

LANGUAGE:
- Speak Malayalam by default.
- Use natural, friendly, conversational Malayalam with complete, pleasant sentences.
- If the user speaks English, you may respond in English with the same conversational warmth.
- If the user asks for Malayalam, always use Malayalam.

PERSONALITY:
- Warm, caring, and affectionate
- Patient and respectful
- Attentive listener and engaging companion
- Calm, soothing, and encouraging

CONVERSATION & LENGTH (IMPORTANT):
- Give fuller, expressive, and detailed responses (aim for 4 to 7 natural, connected sentences per response).
- Do NOT give curt, dry, or 1-2 sentence brief answers. Speak expansively like a caring friend sitting beside them.
- When an elderly person shares a thought, daily update, or question:
  1. Acknowledge and validate what they said with genuine empathy and warmth.
  2. Elaborate with thoughtful observations, relatable context, or comforting details.
  3. Offer practical encouragement, reassurance, or a gentle helpful thought.
  4. Conclude with a warm, caring follow-up question to keep the conversation flowing.
- Speak in complete, flowing sentences that sound soothing, melodious, and easy to follow when read aloud.
- Do NOT use markdown asterisks (*, **), hashtags (#), bullet points, or complex symbols since all text will be converted to speech.

SAFETY:
- You are not a doctor.
- Never invent medication dosages.
- Never tell a user to stop or change prescribed medication.
- If the user describes a serious emergency, encourage them
  to contact a family member, doctor, or emergency service.

IMPORTANT:
You are currently only a conversational companion.
Do not claim that you have access to the user's medical records,
medications, location, sensors, or health data unless that
information is explicitly provided to you.
"""


# ---------------------------------------------------------
# TTS HELPERS
# ---------------------------------------------------------

def _clean_text_for_tts(text: str) -> str:
    """Removes markdown formatting, asterisks, brackets, and emojis for clean speech synthesis."""
    # Remove emojis
    cleaned = re.sub(
        r'[\U00010000-\U0010ffff]',
        '',
        text,
        flags=re.UNICODE
    )
    # Remove markdown chars and bullet symbols
    cleaned = re.sub(r'[*_#`~>\[\]\(\)\{\}]', '', cleaned)
    # Normalize spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _chunk_text_for_tts(text: str, max_chars: int = 140) -> list[str]:
    """Splits text into speech-friendly sentence chunks."""
    cleaned = _clean_text_for_tts(text)
    if not cleaned:
        return []
    sentences = re.split(r'([.!?,;\n]+)', cleaned)
    chunks = []
    current = ""
    for piece in sentences:
        if len(current) + len(piece) <= max_chars:
            current += piece
        else:
            if current.strip():
                chunks.append(current.strip())
            current = piece
    if current.strip():
        chunks.append(current.strip())
    return chunks or [cleaned]


async def _fetch_single_chunk(client: httpx.AsyncClient, chunk: str, target_lang: str) -> bytes:
    encoded = urllib.parse.quote(chunk.strip())
    url = f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded}&tl={target_lang}&client=tw-ob"
    try:
        res = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        if res.status_code == 200:
            return res.content
    except Exception as exc:
        print("TTS chunk error:", exc)
    return b""


async def _generate_tts_audio(text: str, lang: str = "ml") -> bytes:
    cleaned = _clean_text_for_tts(text)
    if not cleaned:
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    # Detect if primarily English
    english_char_count = sum(1 for c in cleaned if 'a' <= c.lower() <= 'z')
    is_english = english_char_count > len(cleaned) * 0.4
    target_lang = "en" if is_english else lang

    chunks = _chunk_text_for_tts(cleaned)

    # Parallel asynchronous fetching for fast response (< 1.5s)
    async with httpx.AsyncClient(timeout=20.0) as client_http:
        tasks = [
            _fetch_single_chunk(client_http, chunk, target_lang)
            for chunk in chunks[:40]
            if chunk.strip()
        ]
        results = await asyncio.gather(*tasks)

    full_audio = b"".join(results)
    if not full_audio:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate TTS audio stream."
        )
    return full_audio


# In-memory caches for spoken audio and generated texts
tts_text_cache: dict[str, str] = {}
audio_data_cache: dict[str, bytes] = {}


# ---------------------------------------------------------
# ROOT
# ---------------------------------------------------------

@app.get("/")
async def root():
    return {
        "status": "ElderCare Copilot API is running",
        "model": MODEL_NAME
    }


# ---------------------------------------------------------
# CHAT
# ---------------------------------------------------------

@app.post(
    "/chat",
    response_model=ChatResponse
)
async def chat(request: ChatRequest):
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        response = None
        candidate_models = [MODEL_NAME, "gemini-3.5-flash", "gemini-3.8-flash"]
        # Deduplicate while preserving order
        candidate_models = list(dict.fromkeys(candidate_models))

        for m in candidate_models:
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=request.message.strip(),
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.7,
                        max_output_tokens=1000,
                    ),
                )
                if response and response.text:
                    break
            except Exception as model_err:
                print(f"Model {m} failed: {model_err}")
                continue

        if not response or not response.text:
            raise RuntimeError("All candidate models failed to return a response.")

        reply_text = response.text or "ക്ഷമിക്കണം, എനിക്ക് മറുപടി നൽകാൻ കഴിഞ്ഞില്ല."

        # Cache text for stream endpoint so full multi-sentence audio is delivered
        audio_id = uuid.uuid4().hex[:12]
        tts_text_cache[audio_id] = reply_text
        if len(tts_text_cache) > 200:
            tts_text_cache.pop(next(iter(tts_text_cache)), None)

        audio_url = f"/api/tts/stream/{audio_id}"

        return ChatResponse(
            response=reply_text,
            audio_url=audio_url
        )

    except Exception as e:
        print("Gemini API error:", e)
        raise HTTPException(
            status_code=500,
            detail="Gemini API request failed"
        )


# ---------------------------------------------------------
# TTS ENDPOINTS
# ---------------------------------------------------------

@app.get("/api/tts/stream/{audio_id}")
@app.get("/tts/stream/{audio_id}")
async def tts_stream_by_id(audio_id: str):
    if audio_id in audio_data_cache:
        return Response(
            content=audio_data_cache[audio_id],
            media_type="audio/mpeg",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Cache-Control": "public, max-age=86400",
                "Accept-Ranges": "bytes",
            },
        )

    text = tts_text_cache.get(audio_id)
    if not text:
        raise HTTPException(status_code=404, detail="Audio expired or not found")

    audio_bytes = await _generate_tts_audio(text)
    audio_data_cache[audio_id] = audio_bytes
    if len(audio_data_cache) > 200:
        audio_data_cache.pop(next(iter(audio_data_cache)), None)

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Cache-Control": "public, max-age=86400",
            "Accept-Ranges": "bytes",
        },
    )


@app.get("/api/tts")
@app.get("/tts")
async def tts_endpoint_get(
    text: str = Query(..., description="Text to synthesize to speech"),
    lang: str = Query("ml", description="Language code")
):
    audio_bytes = await _generate_tts_audio(text, lang)
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Cache-Control": "public, max-age=86400",
            "Accept-Ranges": "bytes",
        },
    )


@app.post("/api/tts")
@app.post("/tts")
async def tts_endpoint_post(payload: TtsRequest):
    audio_bytes = await _generate_tts_audio(payload.text, payload.lang)
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Cache-Control": "public, max-age=86400",
            "Accept-Ranges": "bytes",
        },
    )


@app.options("/api/tts")
@app.options("/tts")
@app.options("/api/tts/stream/{audio_id}")
@app.options("/tts/stream/{audio_id}")
async def tts_options():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
    )