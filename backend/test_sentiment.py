"""
Test suite for Malayalam Sentiment Analysis Module
Uses standard library unittest so it runs in any environment without extra dependencies.
Validates:
1. Heuristic & Analyzer contracts (Malayalam, English, Mixed Manglish)
2. SQLite persistence, deduplication, daily summaries (<3 threshold), historical trends
3. FastAPI endpoints and background task integration
4. Medical disclaimer enforcement
"""

import os
import sys
import unittest
import tempfile
from pathlib import Path

# Ensure backend directory is in sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from sentiment.models import (
    SentimentType,
    EmotionType,
    SentimentResult,
    MEDICAL_DISCLAIMER,
)
from sentiment.analyzer import (
    HeuristicFallbackAnalyzer,
    set_sentiment_analyzer,
)
from sentiment.database import (
    save_sentiment_record,
    get_records_by_session,
    get_records_by_elder,
    get_daily_summary,
    get_historical_trends,
)
from fastapi.testclient import TestClient
from main import app


def get_temp_db_path() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    p = Path(tmp.name)
    tmp.close()
    return p


class TestSentimentModule(unittest.TestCase):

    def setUp(self):
        self.analyzer = HeuristicFallbackAnalyzer()
        set_sentiment_analyzer(self.analyzer)

    def test_01_heuristic_analyzer_multilingual(self):
        """Verify sentiment and emotion extraction on Malayalam, English, and Mixed Manglish."""
        import asyncio

        # 1. Malayalam Happy
        res1 = asyncio.run(self.analyzer.analyze("എനിക്ക് ഇന്ന് വളരെ സന്തോഷമുണ്ട്, മനസ്സ് നിറഞ്ഞു"))
        self.assertEqual(res1.sentiment, SentimentType.POSITIVE)
        self.assertEqual(res1.emotion, EmotionType.HAPPY)
        self.assertTrue(0.0 <= res1.confidence <= 1.0)
        self.assertEqual(res1.medical_disclaimer, MEDICAL_DISCLAIMER)

        # 2. Malayalam Lonely
        res2 = asyncio.run(self.analyzer.analyze("എനിക്ക് ആരുമില്ല, ഇവിടെ ഭയങ്കര ഒറ്റപ്പെടൽ ആണ്"))
        self.assertEqual(res2.sentiment, SentimentType.NEGATIVE)
        self.assertEqual(res2.emotion, EmotionType.LONELY)
        self.assertTrue(len(res2.explanation) > 0)

        # 3. English Anxious
        res3 = asyncio.run(self.analyzer.analyze("I am very worried and nervous about my doctor appointment"))
        self.assertEqual(res3.sentiment, SentimentType.NEGATIVE)
        self.assertEqual(res3.emotion, EmotionType.ANXIOUS)

        # 4. Mixed Manglish Calm
        res4 = asyncio.run(self.analyzer.analyze("Morning medicine കഴിച്ചു, I am feeling very relaxed and peaceful"))
        self.assertEqual(res4.sentiment, SentimentType.POSITIVE)
        self.assertEqual(res4.emotion, EmotionType.CALM)

        # 5. Malayalam Frustrated
        res5 = asyncio.run(self.analyzer.analyze("എനിക്ക് ഭയങ്കര ദേഷ്യം വരുന്നുണ്ട്, ഇതൊന്നും ശരിയാവുന്നില്ല"))
        self.assertEqual(res5.sentiment, SentimentType.NEGATIVE)
        self.assertEqual(res5.emotion, EmotionType.FRUSTRATED)

        # 6. Neutral
        res6 = asyncio.run(self.analyzer.analyze("ഇന്ന് രാവിലെ മഴ പെയ്തു"))
        self.assertEqual(res6.sentiment, SentimentType.NEUTRAL)

    def test_02_database_persistence_and_deduplication(self):
        """Verify SQLite storage, hashing, and duplicate detection."""
        db_path = get_temp_db_path()

        try:
            # First insertion
            record1, is_dup1 = save_sentiment_record(
                elder_id="elder_test_1",
                session_id="session_001",
                text="എനിക്ക് ഇന്ന് സന്തോഷമുണ്ട്",
                language="malayalam",
                sentiment="positive",
                emotion="happy",
                confidence=0.88,
                explanation="Expresses joy and contentment",
                timestamp="2026-10-09T10:00:00Z",
                idempotency_key="unique_key_1",
                db_path=db_path,
            )

            self.assertFalse(is_dup1)
            self.assertGreater(record1["id"], 0)
            self.assertEqual(record1["sentiment"], "positive")
            self.assertEqual(record1["emotion"], "happy")

            # Duplicate submission with identical idempotency key or content
            record2, is_dup2 = save_sentiment_record(
                elder_id="elder_test_1",
                session_id="session_001",
                text="എനിക്ക് ഇന്ന് സന്തോഷമുണ്ട്",
                language="malayalam",
                sentiment="positive",
                emotion="happy",
                confidence=0.88,
                explanation="Expresses joy and contentment",
                timestamp="2026-10-09T10:00:00Z",
                idempotency_key="unique_key_1",
                db_path=db_path,
            )

            self.assertTrue(is_dup2)
            self.assertEqual(record2["id"], record1["id"])

            # Query session
            session_recs = get_records_by_session("session_001", db_path=db_path)
            self.assertEqual(len(session_recs), 1)
        finally:
            if db_path.exists():
                try:
                    os.remove(str(db_path))
                except OSError:
                    pass

    def test_03_daily_summary_threshold_and_aggregation(self):
        """Verify insufficient data flag when utterances < 3, and full calculation when >= 3."""
        db_path = get_temp_db_path()

        try:
            date_str = "2026-10-09"

            # 1. Less than 3 utterances -> insufficient_data = True
            save_sentiment_record(
                elder_id="elder_test_2",
                session_id="session_002",
                text="ഹലോ മോനേ",
                language="malayalam",
                sentiment="positive",
                emotion="happy",
                confidence=0.9,
                explanation="Greeting",
                timestamp=f"{date_str}T08:00:00Z",
                idempotency_key="k1",
                db_path=db_path,
            )
            save_sentiment_record(
                elder_id="elder_test_2",
                session_id="session_002",
                text="ഭക്ഷണം കഴിച്ചു",
                language="malayalam",
                sentiment="positive",
                emotion="happy",
                confidence=0.85,
                explanation="Had meal",
                timestamp=f"{date_str}T08:05:00Z",
                idempotency_key="k2",
                db_path=db_path,
            )

            summary_low = get_daily_summary("elder_test_2", date_str, db_path=db_path)
            self.assertTrue(summary_low["insufficient_data"])
            self.assertEqual(summary_low["total_utterances"], 2)
            self.assertIn("Insufficient speech data", summary_low["message"])

            # 2. Add two more utterances to reach >= 3
            save_sentiment_record(
                elder_id="elder_test_2",
                session_id="session_002",
                text="ഇവിടെ ഭയങ്കര ഏകാന്തതയാണ്",
                language="malayalam",
                sentiment="negative",
                emotion="lonely",
                confidence=0.88,
                explanation="Expresses loneliness",
                timestamp=f"{date_str}T08:10:00Z",
                idempotency_key="k3",
                db_path=db_path,
            )
            save_sentiment_record(
                elder_id="elder_test_2",
                session_id="session_002",
                text="മോൻ സംസാരിച്ചത് സന്തോഷമായി",
                language="malayalam",
                sentiment="positive",
                emotion="happy",
                confidence=0.92,
                explanation="Happy conversation",
                timestamp=f"{date_str}T08:15:00Z",
                idempotency_key="k4",
                db_path=db_path,
            )

            summary_full = get_daily_summary("elder_test_2", date_str, db_path=db_path)
            self.assertFalse(summary_full["insufficient_data"])
            self.assertEqual(summary_full["total_utterances"], 4)
            self.assertEqual(summary_full["sentiment_counts"]["positive"], 3)
            self.assertEqual(summary_full["sentiment_counts"]["negative"], 1)
            self.assertEqual(summary_full["sentiment_distribution"]["positive"], 75.0)
            self.assertEqual(summary_full["dominant_emotion"], "happy")
        finally:
            if db_path.exists():
                try:
                    os.remove(str(db_path))
                except OSError:
                    pass

    def test_04_historical_trends_and_alerts(self):
        """Verify multi-day trend calculation and wellbeing alerts for persistent negative emotions."""
        db_path = get_temp_db_path()

        try:
            # Day 1: anxious
            for i in range(3):
                save_sentiment_record(
                    elder_id="elder_trend_1",
                    session_id="sess_1",
                    text=f"പേടിയാവുന്നു {i}",
                    language="malayalam",
                    sentiment="negative",
                    emotion="anxious",
                    confidence=0.8,
                    explanation="Anxious feeling",
                    timestamp=f"2026-10-07T10:0{i}:00Z",
                    idempotency_key=f"d1_{i}",
                    db_path=db_path,
                )

            # Day 2: lonely
            for i in range(3):
                save_sentiment_record(
                    elder_id="elder_trend_1",
                    session_id="sess_2",
                    text=f"ഒറ്റയ്ക്കാണ് {i}",
                    language="malayalam",
                    sentiment="negative",
                    emotion="lonely",
                    confidence=0.85,
                    explanation="Lonely feeling",
                    timestamp=f"2026-10-08T10:0{i}:00Z",
                    idempotency_key=f"d2_{i}",
                    db_path=db_path,
                )

            trends = get_historical_trends("elder_trend_1", days=7, db_path=db_path)
            self.assertFalse(trends["insufficient_data"])
            self.assertEqual(trends["total_utterances"], 6)
            self.assertTrue(any("lonel" in a.lower() or "anxi" in a.lower() for a in trends["wellbeing_alerts"]))
        finally:
            if db_path.exists():
                try:
                    os.remove(str(db_path))
                except OSError:
                    pass

    def test_05_fastapi_sentiment_endpoints(self):
        """Verify FastAPI sentiment endpoints using TestClient."""
        client = TestClient(app)

        # 1. POST /api/sentiment/analyze
        analyze_payload = {
            "text": "ഇന്ന് എനിക്ക് നല്ല സന്തോഷം തോന്നുന്നു, മക്കൾ വിളിച്ചിരുന്നു",
            "elder_id": "test_elder_api",
            "session_id": "sess_api_01",
            "idempotency_key": "api_test_key_1",
        }
        response = client.post("/api/sentiment/analyze", json=analyze_payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("sentiment", data)
        self.assertIn("emotion", data)
        self.assertIn("confidence", data)
        self.assertEqual(data["medical_disclaimer"], MEDICAL_DISCLAIMER)
        self.assertEqual(data["elder_id"], "test_elder_api")

        # 2. Duplicate submission with same idempotency key
        response_dup = client.post("/api/sentiment/analyze", json=analyze_payload)
        self.assertEqual(response_dup.status_code, 200)
        data_dup = response_dup.json()
        self.assertTrue(data_dup["is_duplicate"])

        # 3. GET /api/sentiment/sessions/{session_id}
        res_session = client.get("/api/sentiment/sessions/sess_api_01")
        self.assertEqual(res_session.status_code, 200)
        session_data = res_session.json()
        self.assertEqual(session_data["session_id"], "sess_api_01")
        self.assertGreaterEqual(session_data["utterance_count"], 1)
        self.assertIn("medical_disclaimer", session_data)

        # 4. GET /api/sentiment/elder/{elder_id}
        res_elder = client.get("/api/sentiment/elder/test_elder_api")
        self.assertEqual(res_elder.status_code, 200)
        self.assertGreaterEqual(len(res_elder.json()), 1)

        # 5. GET /api/sentiment/daily-summary/{elder_id}
        res_summary = client.get("/api/sentiment/daily-summary/test_elder_api")
        self.assertEqual(res_summary.status_code, 200)
        summary_data = res_summary.json()
        self.assertIn("insufficient_data", summary_data)
        self.assertEqual(summary_data["medical_disclaimer"], MEDICAL_DISCLAIMER)

        # 6. GET /api/sentiment/trends/{elder_id}
        res_trends = client.get("/api/sentiment/trends/test_elder_api")
        self.assertEqual(res_trends.status_code, 200)
        trend_data = res_trends.json()
        self.assertIn("daily_trends", trend_data)
        self.assertEqual(trend_data["medical_disclaimer"], MEDICAL_DISCLAIMER)

    def test_06_medical_disclaimer_mandatory_on_all_models(self):
        """Ensure sentiment analysis is NEVER presented as medical advice."""
        from sentiment.models import (
            SentimentResult,
            SentimentRecordDTO,
            SessionSentimentSummaryResponse,
            DailySummaryResponse,
            TrendResponse,
        )

        models_to_check = [
            SentimentResult(
                sentiment=SentimentType.POSITIVE,
                emotion=EmotionType.HAPPY,
                confidence=0.9,
                explanation="Happy",
            ),
            SentimentRecordDTO(
                id=1,
                elder_id="e1",
                session_id="s1",
                text="hello",
                language="malayalam",
                sentiment=SentimentType.POSITIVE,
                emotion=EmotionType.HAPPY,
                confidence=0.9,
                explanation="Happy",
                timestamp="2026-10-09T00:00:00Z",
            ),
            SessionSentimentSummaryResponse(
                elder_id="e1",
                session_id="s1",
                utterance_count=0,
                records=[],
                sentiment_breakdown={},
                emotion_breakdown={},
            ),
            DailySummaryResponse(
                elder_id="e1",
                date="2026-10-09",
                insufficient_data=True,
                message="test",
                total_utterances=0,
            ),
            TrendResponse(
                elder_id="e1",
                days_analyzed=7,
                insufficient_data=True,
                message="test",
                total_utterances=0,
            ),
        ]

        for m in models_to_check:
            self.assertEqual(m.medical_disclaimer, MEDICAL_DISCLAIMER)
            self.assertIn("NOT a medical", m.medical_disclaimer)

    def test_07_gemini_analyzer_graceful_fallback(self):
        """Ensure GeminiSentimentAnalyzer falls back to Heuristic analyzer when API fails."""
        import asyncio
        from sentiment.analyzer import GeminiSentimentAnalyzer

        class FaultyMockClient:
            class MockModels:
                def generate_content(self, *args, **kwargs):
                    raise RuntimeError("Simulated API rate limit or network outage")
            models = MockModels()

        fallback_analyzer = GeminiSentimentAnalyzer(client=FaultyMockClient(), candidate_models=["mock-model"])
        res = asyncio.run(fallback_analyzer.analyze("എനിക്ക് സന്തോഷമുണ്ട്"))
        self.assertEqual(res.sentiment, SentimentType.POSITIVE)
        self.assertEqual(res.emotion, EmotionType.HAPPY)
        self.assertEqual(res.medical_disclaimer, MEDICAL_DISCLAIMER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
