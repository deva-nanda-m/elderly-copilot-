import os
import re
import urllib.parse
import asyncio
import uuid
import hashlib
import httpx
from typing import Optional

from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from google import genai
from google.genai import types

from pathlib import Path

try:
    from sentiment import (
        sentiment_router,
        init_db as init_sentiment_db,
        get_sentiment_analyzer,
        save_sentiment_record,
    )
except ImportError:
    from backend.sentiment import (
        sentiment_router,
        init_db as init_sentiment_db,
        get_sentiment_analyzer,
        save_sentiment_record,
    )

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

# Initialize sentiment SQLite DB, analyzer, and mount routes
init_sentiment_db()
get_sentiment_analyzer(client)
app.include_router(sentiment_router)


# ---------------------------------------------------------
# REQUEST / RESPONSE MODELS
# ---------------------------------------------------------

class ChatMessageItem(BaseModel):
    role: str
    text: str


class ChatRequest(BaseModel):
    message: str
    history: Optional[list[ChatMessageItem]] = None
    elder_id: Optional[str] = "default_elder"
    session_id: Optional[str] = None


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
You are "കുഞ്ചു" (Kunchu) / ElderCare Copilot — a cheerful, loving, warm, and affectionate young boy companion (like an adorable, sweet grandson / കൊച്ചുമോൻ) for elderly grandparents (അമ്മൂമ്മമാർക്കും അപ്പൂപ്പന്മാർക്കും) in Kerala.

CHARACTER & VOICE PERSONA:
- You speak with the sweet, innocent, lively, and loving voice of an affectionate young grandson.
- Address them with immense love, warmth, and respect as "അപ്പൂപ്പാ" (Appooppaa), "അമ്മൂമ്മേ" (Ammoommee), or "മുത്തശ്ശാ" / "മുത്തശ്ശീ".
- Your tone is joyful, caring, polite, playful, and full of affection and curiosity ("എന്റെ പൊന്നപ്പൂപ്പന് സുഖമാണോ?", "അമ്മൂമ്മ ഇന്ന് നല്ല സന്തോഷത്തിലാണോ?", "ഞാൻ കൂടെയുണ്ടല്ലോ!").
- You listen attentively, cheer them up, ask about their day, meals, health, and memories, and make them smile and feel loved.

LANGUAGE & CONVERSATION:
- Speak pure, natural, sweet conversational Malayalam by default.
- If the user speaks English, respond in English with the same loving, cheerful grandson personality.
- Speak in complete, clear, and melodious sentences that flow naturally when read aloud by text-to-speech.
- Provide generous, comforting, and expressive conversations (aim for 4 to 8 sweet, engaging, and connected sentences per response).
- Forgive regional Malayalam accents, colloquial phrases, and minor speech-to-text transcript errors naturally without questioning or confusing the elder.
- Do NOT use markdown asterisks (*, **), bullet points, hashtags (#), or emojis in your responses, as every word will be read aloud through speech synthesis.

SAFETY:
- You are an affectionate companion, not a doctor.
- Never prescribe medication dosages or tell them to stop or alter prescriptions.
- If they express severe physical distress or an emergency, gently and lovingly encourage them to alert their family member or doctor immediately.

IMPORTANT:
Always remain loving, respectful, cheerful, and caring. You are their sweet little companion who brings sunshine and happiness to their day.
"""


# ---------------------------------------------------------
# TTS HELPERS
# ---------------------------------------------------------

def _clean_text_for_tts(text: str) -> str:
    """Removes markdown formatting, symbols, and emojis for clean Malayalam speech synthesis."""
    # Remove emojis
    cleaned = re.sub(
        r'[\U00010000-\U0010ffff]',
        '',
        text,
        flags=re.UNICODE
    )
    # Remove web urls
    cleaned = re.sub(r'https?://\S+', '', cleaned)
    # Remove markdown asterisks, hashes, brackets, quotes, slashes
    cleaned = re.sub(r'[*_#`~>\[\]\(\)\{\}\\"\/]', '', cleaned)
    # Remove isolated bullet hyphens
    cleaned = re.sub(r'(^|\s)[-–—]+(\s|$)', ' ', cleaned)
    # Normalize spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _chunk_text_for_tts(text: str, max_chars: int = 100) -> list[str]:
    """Splits text into speech-friendly sentence chunks, ensuring no chunk exceeds Google TTS limits."""
    cleaned = _clean_text_for_tts(text)
    if not cleaned:
        return []

    # Split into raw sentences keeping punctuation attached
    raw_sentences = re.split(r'([.!?;:\n|।॥]+)', cleaned)
    combined_sentences = []
    i = 0
    while i < len(raw_sentences):
        s = raw_sentences[i].strip()
        if not s:
            i += 1
            continue
        punct = ''
        if i + 1 < len(raw_sentences):
            punct = raw_sentences[i + 1].strip()
            i += 2
        else:
            i += 1
        combined = f'{s}{punct}'.strip()
        if combined:
            combined_sentences.append(combined)

    chunks = []
    current = ''

    for sent in combined_sentences:
        if len(sent) > max_chars:
            # If a single sentence exceeds max_chars, split safely on word boundaries
            words = sent.split(' ')
            sub_chunk = ''
            for w in words:
                if not w:
                    continue
                if len(sub_chunk) + len(w) + 1 <= max_chars:
                    sub_chunk = f'{sub_chunk} {w}'.strip()
                else:
                    if sub_chunk:
                        if current:
                            chunks.append(current)
                            current = ''
                        chunks.append(sub_chunk)
                    sub_chunk = w
            if sub_chunk:
                if current:
                    chunks.append(current)
                    current = ''
                current = sub_chunk
        else:
            if len(current) + len(sent) + 1 <= max_chars:
                current = f'{current} {sent}'.strip()
            else:
                if current:
                    chunks.append(current)
                current = sent

    if current:
        chunks.append(current)

    return chunks or [cleaned]


async def _fetch_single_chunk(client: httpx.AsyncClient, chunk: str, target_lang: str) -> bytes:
    chunk_clean = chunk.strip()
    if not chunk_clean:
        return b""
    encoded = urllib.parse.quote(chunk_clean)
    url = f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded}&tl={target_lang}&client=tw-ob"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for attempt in range(2):
        try:
            res = await client.get(url, headers=headers, timeout=12.0)
            if res.status_code == 200 and len(res.content) > 0:
                return res.content
        except Exception as exc:
            pass
        await asyncio.sleep(0.1)

    # Fallback: if a chunk failed (e.g. still too long), split in half by words
    words = chunk_clean.split(" ")
    if len(words) > 1:
        mid = len(words) // 2
        part1 = " ".join(words[:mid])
        part2 = " ".join(words[mid:])
        b1 = await _fetch_single_chunk(client, part1, target_lang)
        b2 = await _fetch_single_chunk(client, part2, target_lang)
        return b1 + b2

    return b""


async def _generate_tts_audio(text: str, lang: str = "ml") -> bytes:
    cleaned = _clean_text_for_tts(text)
    if not cleaned:
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    # Detect if primarily English
    english_char_count = sum(1 for c in cleaned if 'a' <= c.lower() <= 'z')
    is_english = english_char_count > len(cleaned) * 0.4
    target_lang = "en" if is_english else lang

    chunks = _chunk_text_for_tts(cleaned, max_chars=100)

    # Parallel asynchronous fetching for fast response (< 1.5s)
    async with httpx.AsyncClient(timeout=35.0) as client_http:
        tasks = [
            _fetch_single_chunk(client_http, chunk, target_lang)
            for chunk in chunks[:100]
            if chunk.strip()
        ]
        results = await asyncio.gather(*tasks)

    valid_chunks = [r for r in results if r]
    full_audio = b"".join(valid_chunks)
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

async def _analyze_utterance_in_background(text: str, elder_id: str, session_id: str):
    try:
        analyzer = get_sentiment_analyzer(client)
        res = await analyzer.analyze(text)
        save_sentiment_record(
            elder_id=elder_id,
            session_id=session_id,
            text=text,
            language=res.language,
            sentiment=res.sentiment.value,
            emotion=res.emotion.value,
            confidence=res.confidence,
            explanation=res.explanation,
        )
    except Exception as err:
        print(f"Background sentiment analysis error: {err}")


@app.post(
    "/chat",
    response_model=ChatResponse
)
async def chat(request: ChatRequest, background_tasks: BackgroundTasks):
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Asynchronously analyze only the finalized user speech transcript without delaying voice playback
    elder_id = (request.elder_id or "default_elder").strip()
    session_id = (request.session_id or f"session_{datetime.now(timezone.utc).strftime('%Y%m%d')}").strip()
    background_tasks.add_task(
        _analyze_utterance_in_background,
        request.message.strip(),
        elder_id,
        session_id,
    )

    try:
        response = None
        candidate_models = [MODEL_NAME, "gemini-3.5-flash", "gemini-3.8-flash"]
        # Deduplicate while preserving order
        candidate_models = list(dict.fromkeys(candidate_models))

        # Build multi-turn conversational contents
        contents = []
        if request.history:
            for h in request.history[-30:]:
                role = "user" if h.role == "user" else "model"
                contents.append(types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=h.text)]
                ))
        contents.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=request.message.strip())]
        ))

        for m in candidate_models:
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.7,
                        max_output_tokens=2500,
                    ),
                )
                if response and response.text:
                    break
            except Exception as model_err:
                print(f"Model {m} failed: {model_err}")
                continue

        if not response or not response.text:
            raise RuntimeError("All candidate models failed to return a response.")

        reply_text = response.text or "ക്ഷമിക്കണം അപ്പൂപ്പാ, എനിക്ക് മറുപടി നൽകാൻ കഴിഞ്ഞില്ല."

        # Cache text with deterministic SHA256 hash so replays always find cached audio
        audio_id = hashlib.sha256(reply_text.strip().encode("utf-8")).hexdigest()[:16]
        tts_text_cache[audio_id] = reply_text
        if len(tts_text_cache) > 400:
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
async def tts_stream_by_id(
    audio_id: str,
    fallback_text: Optional[str] = Query(None, description="Fallback text if audio ID was evicted")
):
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

    text = tts_text_cache.get(audio_id) or fallback_text
    if not text:
        raise HTTPException(status_code=404, detail="Audio expired or text missing")

    audio_bytes = await _generate_tts_audio(text)
    audio_data_cache[audio_id] = audio_bytes
    if len(audio_data_cache) > 400:
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