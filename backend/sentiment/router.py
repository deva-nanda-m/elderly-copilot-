from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status

from .models import (
    AnalyzeUtteranceRequest,
    SentimentRecordDTO,
    SessionSentimentSummaryResponse,
    DailySummaryResponse,
    TrendResponse,
    MEDICAL_DISCLAIMER,
)
from .database import (
    save_sentiment_record,
    get_records_by_session,
    get_records_by_elder,
    get_daily_summary,
    get_historical_trends,
)
from .analyzer import get_sentiment_analyzer

router = APIRouter(prefix="/api/sentiment", tags=["Sentiment Analysis"])


@router.post(
    "/analyze",
    response_model=SentimentRecordDTO,
    summary="Analyze finalized speech transcript",
    description="Analyzes elderly user speech transcript in Malayalam, English, or mixed language, storing the result in SQLite."
)
async def analyze_utterance(payload: AnalyzeUtteranceRequest):
    if not payload.text or not payload.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Utterance text cannot be empty."
        )

    elder_id = payload.elder_id.strip() or "default_elder"
    session_id = payload.session_id.strip() if payload.session_id else f"session_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    timestamp = payload.timestamp or datetime.now(timezone.utc).isoformat()

    analyzer = get_sentiment_analyzer()
    analysis_result = await analyzer.analyze(payload.text.strip())

    # Save to SQLite with duplicate prevention
    record_dict, is_duplicate = save_sentiment_record(
        elder_id=elder_id,
        session_id=session_id,
        text=payload.text.strip(),
        language=analysis_result.language,
        sentiment=analysis_result.sentiment.value,
        emotion=analysis_result.emotion.value,
        confidence=analysis_result.confidence,
        explanation=analysis_result.explanation,
        timestamp=timestamp,
        idempotency_key=payload.idempotency_key,
    )

    return SentimentRecordDTO(
        id=record_dict["id"],
        elder_id=record_dict["elder_id"],
        session_id=record_dict["session_id"],
        text=record_dict["text"],
        language=record_dict["language"],
        sentiment=record_dict["sentiment"],
        emotion=record_dict["emotion"],
        confidence=record_dict["confidence"],
        explanation=record_dict["explanation"],
        timestamp=record_dict["timestamp"],
        is_duplicate=is_duplicate,
        medical_disclaimer=MEDICAL_DISCLAIMER,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionSentimentSummaryResponse,
    summary="Get sentiment records for a session",
    description="Retrieves all analyzed utterances and aggregated sentiment breakdown for an active or completed session."
)
async def get_session_sentiment(session_id: str):
    records = get_records_by_session(session_id.strip())
    if not records:
        return SessionSentimentSummaryResponse(
            elder_id="unknown",
            session_id=session_id,
            utterance_count=0,
            records=[],
            sentiment_breakdown={},
            emotion_breakdown={},
            dominant_sentiment=None,
            dominant_emotion=None,
            average_confidence=0.0,
            medical_disclaimer=MEDICAL_DISCLAIMER,
        )

    elder_id = records[0]["elder_id"]
    sentiment_breakdown = {}
    emotion_breakdown = {}
    total_conf = 0.0

    dto_records = []
    for r in records:
        s = r["sentiment"]
        e = r["emotion"]
        sentiment_breakdown[s] = sentiment_breakdown.get(s, 0) + 1
        emotion_breakdown[e] = emotion_breakdown.get(e, 0) + 1
        total_conf += r["confidence"]

        dto_records.append(
            SentimentRecordDTO(
                id=r["id"],
                elder_id=r["elder_id"],
                session_id=r["session_id"],
                text=r["text"],
                language=r["language"],
                sentiment=r["sentiment"],
                emotion=r["emotion"],
                confidence=r["confidence"],
                explanation=r["explanation"],
                timestamp=r["timestamp"],
                is_duplicate=False,
                medical_disclaimer=MEDICAL_DISCLAIMER,
            )
        )

    dominant_sentiment = max(sentiment_breakdown, key=sentiment_breakdown.get)
    dominant_emotion = max(emotion_breakdown, key=emotion_breakdown.get)
    avg_conf = round(total_conf / len(records), 3)

    return SessionSentimentSummaryResponse(
        elder_id=elder_id,
        session_id=session_id,
        utterance_count=len(records),
        records=dto_records,
        sentiment_breakdown=sentiment_breakdown,
        emotion_breakdown=emotion_breakdown,
        dominant_sentiment=dominant_sentiment,
        dominant_emotion=dominant_emotion,
        average_confidence=avg_conf,
        medical_disclaimer=MEDICAL_DISCLAIMER,
    )


@router.get(
    "/elder/{elder_id}",
    summary="Get recent analyzed utterances for an elder",
    description="Retrieves recent analyzed records for a specific elder identifier."
)
async def get_elder_sentiment_records(
    elder_id: str,
    limit: int = Query(default=50, ge=1, le=200, description="Max records to return")
):
    records = get_records_by_elder(elder_id.strip(), limit=limit)
    return {
        "elder_id": elder_id,
        "count": len(records),
        "records": records,
        "medical_disclaimer": MEDICAL_DISCLAIMER,
    }


@router.get(
    "/daily-summary/{elder_id}",
    response_model=DailySummaryResponse,
    summary="Get daily sentiment summary",
    description="Aggregates daily sentiments, indicating insufficient data if fewer than 3 utterances are recorded."
)
async def get_daily_sentiment_summary(
    elder_id: str,
    date: Optional[str] = Query(default=None, description="Date in YYYY-MM-DD format (defaults to today)")
):
    summary = get_daily_summary(elder_id.strip(), date_str=date)
    return DailySummaryResponse(
        elder_id=summary["elder_id"],
        date=summary["date"],
        insufficient_data=summary["insufficient_data"],
        message=summary["message"],
        total_utterances=summary["total_utterances"],
        sentiment_distribution=summary["sentiment_distribution"],
        sentiment_counts=summary["sentiment_counts"],
        emotion_counts=summary["emotion_counts"],
        dominant_sentiment=summary["dominant_sentiment"],
        dominant_emotion=summary["dominant_emotion"],
        average_confidence=summary["average_confidence"],
        wellbeing_notes=summary["wellbeing_notes"],
        medical_disclaimer=MEDICAL_DISCLAIMER,
    )


@router.get(
    "/trends/{elder_id}",
    response_model=TrendResponse,
    summary="Get historical sentiment trends",
    description="Generates historical day-by-day sentiment trajectory and wellbeing alert flags across recent days."
)
async def get_sentiment_trends(
    elder_id: str,
    days: int = Query(default=7, ge=2, le=90, description="Number of days to analyze")
):
    trends = get_historical_trends(elder_id.strip(), days=days)
    return TrendResponse(
        elder_id=trends["elder_id"],
        days_analyzed=trends["days_analyzed"],
        insufficient_data=trends["insufficient_data"],
        message=trends["message"],
        total_utterances=trends["total_utterances"],
        daily_trends=trends["daily_trends"],
        trend_direction=trends["trend_direction"],
        top_emotions=trends["top_emotions"],
        wellbeing_alerts=trends["wellbeing_alerts"],
        medical_disclaimer=MEDICAL_DISCLAIMER,
    )
