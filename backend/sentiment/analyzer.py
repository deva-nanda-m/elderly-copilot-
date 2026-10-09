import json
import re
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any

from google import genai
from google.genai import types

from .models import SentimentResult, SentimentType, EmotionType, MEDICAL_DISCLAIMER


class BaseSentimentAnalyzer(ABC):
    """
    Abstract interface for sentiment analyzers.
    Enables modular swapping between Gemini, local ML models (e.g., PyTorch/ONNX),
    or fine-tuned IndicBERT in future iterations without changing application code.
    """

    @abstractmethod
    async def analyze(self, text: str, context: Optional[List[Dict[str, str]]] = None) -> SentimentResult:
        pass


class HeuristicFallbackAnalyzer(BaseSentimentAnalyzer):
    """
    Resilient rule-based fallback analyzer for Malayalam, English, and Manglish.
    Ensures zero failure even if external AI APIs encounter network blips or outages.
    """

    # Keyword dictionaries for emotional cues in Malayalam, English, and Manglish
    JOY_WORDS = {
        "സന്തോഷ", "നല്ല", "നന്നായി", "ഉഷാർ", "ഉത്സാഹം", "സുഖം", "സുഖമാണ്", "ഇഷ്ടം", "രസമുണ്ട്", "നന്ദി", "മനോഹരം", "ചിരി",
        "happy", "good", "great", "glad", "wonderful", "enjoy", "fine", "smile",
        "santhosham", "nallathu", "sukham", "ushar", "rasam"
    }

    SAD_WORDS = {
        "വിഷമ", "സങ്കട", "കരച്ചിൽ", "ദുഃഖ", "വേദന", "ക്ഷീണ", "വയ്യ", "മടു",
        "sad", "pain", "crying", "unhappy", "depressed", "sorrow", "tired",
        "vishamam", "sankadam", "vedhana", "sheenam", "vayya", "maduthu"
    }

    LONELY_WORDS = {
        "ഏകാന്ത", "ആരുമില്ല", "ഒറ്റയ്", "ഒറ്റപ്പെട", "തനിച്ചാ", "വിളിച്ചില്ല",
        "lonely", "alone", "nobody", "isolated", "miss", "nobody visits",
        "ekanthatha", "arumilla", "ottaykk", "ottapedal", "thanichanu"
    }

    ANXIOUS_WORDS = {
        "പേടി", "ഭയം", "ആശങ്ക", "ടെൻഷൻ", "ഉത്കണ്ഠ", "പ്രഷർ",
        "scared", "fear", "anxious", "worried", "tension", "nervous", "panic",
        "pedi", "bayam", "ashanka", "pediyakunnu"
    }

    FRUSTRATED_WORDS = {
        "ദേഷ്യ", "ശുണ്ഠി", "മടു", "വെറു",
        "angry", "frustrated", "irritated", "annoyed", "mad", "upset",
        "deshyam", "maduthu", "veruppu"
    }

    CALM_WORDS = {
        "ശാന്ത", "സമാധാനം", "വിശ്രമം", "കുഴപ്പമില്ല", "റിലാക്സ്",
        "calm", "relaxed", "peaceful", "peace", "rest", "fine",
        "shantham", "samadhanam", "kuzhappamilla"
    }

    async def analyze(self, text: str, context: Optional[List[Dict[str, str]]] = None) -> SentimentResult:
        lower_text = text.lower()

        # Detect primary language
        malayalam_chars = len(re.findall(r'[\u0D00-\u0D7F]', text))
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        if malayalam_chars > 0 and english_chars > 0:
            language = "mixed"
        elif malayalam_chars > 0:
            language = "malayalam"
        elif english_chars > 0:
            language = "english"
        else:
            language = "unknown"

        # Emotion counts using stem/substring matching across text
        emotion_scores = {
            EmotionType.HAPPY: sum(1 for w in self.JOY_WORDS if w in lower_text),
            EmotionType.CALM: sum(1 for w in self.CALM_WORDS if w in lower_text),
            EmotionType.LONELY: sum(1 for w in self.LONELY_WORDS if w in lower_text),
            EmotionType.ANXIOUS: sum(1 for w in self.ANXIOUS_WORDS if w in lower_text),
            EmotionType.FRUSTRATED: sum(1 for w in self.FRUSTRATED_WORDS if w in lower_text),
            EmotionType.SAD: sum(1 for w in self.SAD_WORDS if w in lower_text),
        }

        # Check dominant emotion
        top_emotion, top_count = max(emotion_scores.items(), key=lambda item: item[1])

        if top_count > 0:
            if top_emotion in (EmotionType.HAPPY, EmotionType.CALM):
                sentiment = SentimentType.POSITIVE
                explanation = f"Speech contains expressions of {top_emotion.value} and emotional wellbeing."
            elif top_emotion in (EmotionType.SAD, EmotionType.LONELY, EmotionType.ANXIOUS, EmotionType.FRUSTRATED):
                sentiment = SentimentType.NEGATIVE
                explanation = f"Speech indicates feelings of {top_emotion.value} in conversation."
            else:
                sentiment = SentimentType.NEUTRAL
                explanation = "Balanced speech tone without strong emotional variance."
            confidence = min(0.65 + (top_count * 0.1), 0.90)
        else:
            sentiment = SentimentType.NEUTRAL
            top_emotion = EmotionType.CALM
            confidence = 0.70
            explanation = "Calm, conversational utterance with steady, balanced emotional tone."

        return SentimentResult(
            sentiment=sentiment,
            emotion=top_emotion,
            confidence=round(confidence, 2),
            language=language,
            explanation=explanation,
            medical_disclaimer=MEDICAL_DISCLAIMER,
        )


class GeminiSentimentAnalyzer(BaseSentimentAnalyzer):
    """
    LLM-powered sentiment analysis optimized for elderly Malayalam, English,
    and mixed-dialect/Manglish communication.
    """

    SYSTEM_INSTRUCTION = """
You are an expert NLP sentiment and emotion classifier specializing in speech transcripts from elderly individuals in Kerala.
You analyze finalized speech transcripts which may be in Malayalam script, English, or Manglish (Malayalam written phonetically in English).

YOUR TASK:
Analyze the user utterance and determine:
1. "sentiment": exactly one of ["positive", "neutral", "negative", "mixed"]
2. "emotion": exactly one of ["happy", "sad", "lonely", "anxious", "calm", "frustrated"]
3. "confidence": float between 0.0 and 1.0 representing classification confidence
4. "language": "malayalam", "english", or "mixed"
5. "explanation": a concise 1-2 sentence empathetic explanation in English of the cues detected (e.g. mentions of loneliness, pleasant mood, bodily discomfort, medical anxiety, or calmness).

CLASSIFICATION CRITERIA FOR ELDERLY SPEECH:
- "happy": Gratitude, joy, seeing family, feeling well, laughter, pleasure in talking, cheerful greetings.
- "sad": Grieving, feeling down, sorrow, despair, physical weariness combined with dejection.
- "lonely": Expressing absence of family ("nobody visits", "I am all alone", "ആരുമില്ല", "ഒറ്റയ്ക്ക്"), yearning for companionship.
- "anxious": Worrying about health/medicines, fear of falls, agitation, apprehension ("പേടിയാവുന്നു", "ടെൻഷൻ").
- "frustrated": Irritation, impatience, complaints about things not working, anger ("മടുത്തു", "ദേഷ്യം").
- "calm": Routine updates, peaceful affirmations, ordinary conversational remarks, neutral storytelling.
- "mixed": Expressing both positive warmth and underlying sadness/pain simultaneously.

IMPORTANT:
- Output MUST be valid strict JSON only.
- Never output markdown fences (no ```json).
- Never diagnose any medical or psychological condition.
"""

    def __init__(self, client: genai.Client, candidate_models: Optional[List[str]] = None):
        self.client = client
        self.candidate_models = candidate_models or ["gemini-3.5-flash", "gemini-3.8-flash"]
        self.fallback_analyzer = HeuristicFallbackAnalyzer()

    async def analyze(self, text: str, context: Optional[List[Dict[str, str]]] = None) -> SentimentResult:
        if not text or not text.strip():
            return SentimentResult(
                sentiment=SentimentType.NEUTRAL,
                emotion=EmotionType.CALM,
                confidence=0.5,
                language="unknown",
                explanation="Empty transcript provided.",
            )

        prompt = f"""
Analyze the following elderly speaker transcript:
Transcript: "{text.strip()}"

Respond ONLY with a JSON object with keys:
"sentiment", "emotion", "confidence", "language", "explanation"
"""

        for model in self.candidate_models:
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=self.SYSTEM_INSTRUCTION,
                        temperature=0.1,
                        max_output_tokens=300,
                    ),
                )

                if response and response.text:
                    cleaned = response.text.strip()
                    # Robust JSON block extraction
                    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
                    if match:
                        cleaned = match.group(0)

                    parsed = json.loads(cleaned)

                    # Validate and normalize
                    raw_sentiment = str(parsed.get("sentiment", "neutral")).lower()
                    raw_emotion = str(parsed.get("emotion", "calm")).lower()

                    sentiment = SentimentType(raw_sentiment) if raw_sentiment in [s.value for s in SentimentType] else SentimentType.NEUTRAL
                    emotion = EmotionType(raw_emotion) if raw_emotion in [e.value for e in EmotionType] else EmotionType.CALM
                    confidence = float(parsed.get("confidence", 0.8))
                    confidence = max(0.0, min(1.0, confidence))
                    language = str(parsed.get("language", "malayalam")).lower()
                    explanation = str(parsed.get("explanation", "Classification determined from speech linguistic cues.")).strip()

                    return SentimentResult(
                        sentiment=sentiment,
                        emotion=emotion,
                        confidence=round(confidence, 2),
                        language=language,
                        explanation=explanation,
                        medical_disclaimer=MEDICAL_DISCLAIMER,
                    )
            except Exception as exc:
                print(f"Gemini sentiment model {model} attempt error: {exc}")
                continue

        # If all Gemini models fail or return invalid format, use fallback analyzer
        safe_preview = text[:50].encode("ascii", errors="replace").decode("ascii")
        print(f"Falling back to HeuristicFallbackAnalyzer for utterance: {safe_preview}")
        return await self.fallback_analyzer.analyze(text, context)


_global_analyzer: Optional[BaseSentimentAnalyzer] = None


def get_sentiment_analyzer(client: Optional[genai.Client] = None) -> BaseSentimentAnalyzer:
    """
    Factory function for sentiment analyzer.
    Enables swapping the analyzer instance across the entire application.
    """
    global _global_analyzer
    if _global_analyzer is None:
        if client:
            _global_analyzer = GeminiSentimentAnalyzer(client)
        else:
            _global_analyzer = HeuristicFallbackAnalyzer()
    return _global_analyzer


def set_sentiment_analyzer(analyzer: BaseSentimentAnalyzer):
    """Allows injecting custom / test analyzers or dedicated ML models."""
    global _global_analyzer
    _global_analyzer = analyzer
