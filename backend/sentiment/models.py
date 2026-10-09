from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict
from pydantic import BaseModel, Field

MEDICAL_DISCLAIMER = (
    "Sentiment and emotional analysis is provided for companionship and wellbeing awareness "
    "only and is NOT a medical, psychiatric, or clinical diagnosis."
)


class SentimentType(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"


class EmotionType(str, Enum):
    HAPPY = "happy"
    SAD = "sad"
    LONELY = "lonely"
    ANXIOUS = "anxious"
    CALM = "calm"
    FRUSTRATED = "frustrated"


class SentimentResult(BaseModel):
    sentiment: SentimentType
    emotion: EmotionType
    confidence: float = Field(ge=0.0, le=1.0)
    language: str = "malayalam"
    explanation: str
    medical_disclaimer: str = MEDICAL_DISCLAIMER


class AnalyzeUtteranceRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Finalized user speech transcript")
    elder_id: str = Field(default="default_elder", description="Elder identifier")
    session_id: Optional[str] = Field(default=None, description="Current conversation session ID")
    timestamp: Optional[str] = Field(default=None, description="ISO-8601 formatted timestamp")
    idempotency_key: Optional[str] = Field(default=None, description="Unique key to avoid duplicate insertion")


class SentimentRecordDTO(BaseModel):
    id: int
    elder_id: str
    session_id: str
    text: str
    language: str
    sentiment: SentimentType
    emotion: EmotionType
    confidence: float
    explanation: str
    timestamp: str
    is_duplicate: bool = False
    medical_disclaimer: str = MEDICAL_DISCLAIMER


class SessionSentimentSummaryResponse(BaseModel):
    elder_id: str
    session_id: str
    utterance_count: int
    records: List[SentimentRecordDTO]
    sentiment_breakdown: Dict[str, int]
    emotion_breakdown: Dict[str, int]
    dominant_sentiment: Optional[str] = None
    dominant_emotion: Optional[str] = None
    average_confidence: float = 0.0
    medical_disclaimer: str = MEDICAL_DISCLAIMER


class DailySummaryResponse(BaseModel):
    elder_id: str
    date: str
    insufficient_data: bool
    message: str
    total_utterances: int
    sentiment_distribution: Dict[str, float] = {}
    sentiment_counts: Dict[str, int] = {}
    emotion_counts: Dict[str, int] = {}
    dominant_sentiment: Optional[str] = None
    dominant_emotion: Optional[str] = None
    average_confidence: Optional[float] = None
    wellbeing_notes: Optional[str] = None
    medical_disclaimer: str = MEDICAL_DISCLAIMER


class DailyTrendItem(BaseModel):
    date: str
    utterances_count: int
    dominant_sentiment: Optional[str] = None
    dominant_emotion: Optional[str] = None
    positive_ratio: float = 0.0
    negative_ratio: float = 0.0


class TrendResponse(BaseModel):
    elder_id: str
    days_analyzed: int
    insufficient_data: bool
    message: str
    total_utterances: int
    daily_trends: List[DailyTrendItem] = []
    trend_direction: str = "insufficient_history"  # improving, stable, declining, insufficient_history
    top_emotions: List[str] = []
    wellbeing_alerts: List[str] = []
    medical_disclaimer: str = MEDICAL_DISCLAIMER
