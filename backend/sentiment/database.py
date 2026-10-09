import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "eldercare_sentiment.db"


@contextmanager
def get_db_connection(db_path: Optional[Path] = None):
    target_path = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = sqlite3.connect(str(target_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None):
    with get_db_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                elder_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                text TEXT NOT NULL,
                language TEXT NOT NULL,
                sentiment TEXT NOT NULL,
                emotion TEXT NOT NULL,
                confidence REAL NOT NULL,
                explanation TEXT NOT NULL,
                request_hash TEXT UNIQUE,
                timestamp TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_records_elder_time 
            ON sentiment_records(elder_id, timestamp);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_records_session 
            ON sentiment_records(session_id);
        """)
        conn.commit()


def compute_request_hash(elder_id: str, session_id: str, text: str, idempotency_key: Optional[str] = None) -> str:
    clean_text = " ".join(text.strip().lower().split())
    if idempotency_key:
        raw = f"{elder_id}:{session_id}:{idempotency_key}:{clean_text}"
    else:
        raw = f"{elder_id}:{session_id}:{clean_text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def save_sentiment_record(
    elder_id: str,
    session_id: str,
    text: str,
    language: str,
    sentiment: str,
    emotion: str,
    confidence: float,
    explanation: str,
    timestamp: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Tuple[Dict[str, Any], bool]:
    """
    Saves a sentiment record into SQLite.
    Returns (record_dict, is_duplicate).
    If duplicate detected via request_hash, returns existing record.
    """
    init_db(db_path)
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()
    req_hash = compute_request_hash(elder_id, session_id, text, idempotency_key)

    with get_db_connection(db_path) as conn:
        # Check if record already exists (duplicate prevention)
        cursor = conn.execute(
            "SELECT * FROM sentiment_records WHERE request_hash = ?", (req_hash,)
        )
        existing = cursor.fetchone()
        if existing:
            return dict(existing), True

        # Insert new record
        try:
            cursor = conn.execute(
                """
                INSERT INTO sentiment_records 
                (elder_id, session_id, text, language, sentiment, emotion, confidence, explanation, request_hash, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    elder_id,
                    session_id,
                    text.strip(),
                    language,
                    sentiment.lower(),
                    emotion.lower(),
                    round(float(confidence), 3),
                    explanation.strip(),
                    req_hash,
                    timestamp,
                ),
            )
            conn.commit()
            record_id = cursor.lastrowid
            new_cursor = conn.execute("SELECT * FROM sentiment_records WHERE id = ?", (record_id,))
            return dict(new_cursor.fetchone()), False
        except sqlite3.IntegrityError:
            # Race condition duplicate
            cursor = conn.execute(
                "SELECT * FROM sentiment_records WHERE request_hash = ?", (req_hash,)
            )
            existing = cursor.fetchone()
            return dict(existing), True


def get_records_by_session(session_id: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT * FROM sentiment_records WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_records_by_elder(elder_id: str, limit: int = 100, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT * FROM sentiment_records WHERE elder_id = ? ORDER BY timestamp DESC LIMIT ?",
            (elder_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_daily_summary(
    elder_id: str,
    date_str: Optional[str] = None,
    min_utterances_threshold: int = 3,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    init_db(db_path)
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    start_iso = f"{date_str}T00:00:00"
    end_iso = f"{date_str}T23:59:59.999999"

    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT * FROM sentiment_records 
            WHERE elder_id = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            """,
            (elder_id, start_iso, end_iso),
        )
        rows = [dict(r) for r in cursor.fetchall()]

    total = len(rows)
    if total < min_utterances_threshold:
        return {
            "elder_id": elder_id,
            "date": date_str,
            "insufficient_data": True,
            "message": f"Insufficient speech data for {date_str} ({total} utterance{'s' if total != 1 else ''} found; minimum {min_utterances_threshold} required for daily summary).",
            "total_utterances": total,
            "sentiment_distribution": {},
            "sentiment_counts": {},
            "emotion_counts": {},
            "dominant_sentiment": None,
            "dominant_emotion": None,
            "average_confidence": None,
            "wellbeing_notes": "Not enough speech interactions to generate a reliable summary. Encourage friendly conversations.",
        }

    # Aggregate sentiment counts & percentages
    sentiment_counts: Dict[str, int] = {}
    emotion_counts: Dict[str, int] = {}
    conf_sum = 0.0

    for r in rows:
        s = r["sentiment"]
        e = r["emotion"]
        sentiment_counts[s] = sentiment_counts.get(s, 0) + 1
        emotion_counts[e] = emotion_counts.get(e, 0) + 1
        conf_sum += r["confidence"]

    sentiment_distribution = {
        k: round((v / total) * 100, 1) for k, v in sentiment_counts.items()
    }
    avg_conf = round(conf_sum / total, 3)

    dominant_sentiment = max(sentiment_counts, key=sentiment_counts.get)
    dominant_emotion = max(emotion_counts, key=emotion_counts.get)

    # Contextual wellbeing notes (non-clinical)
    neg_ratio = (sentiment_counts.get("negative", 0) + sentiment_counts.get("mixed", 0)) / total
    lonely_or_sad_count = emotion_counts.get("lonely", 0) + emotion_counts.get("sad", 0)
    anxious_count = emotion_counts.get("anxious", 0)

    if lonely_or_sad_count >= 2 and neg_ratio >= 0.4:
        notes = "Observations indicate recurring sadness or feelings of loneliness today. Gentle companionship, family contact, or cheerful reminiscence is recommended."
    elif anxious_count >= 2:
        notes = "Expressions of worry or anxiety were observed during conversations today. Calming reassurance and gentle routine checks are suggested."
    elif dominant_sentiment == "positive":
        notes = f"Overall positive, joyful mood detected ({emotion_counts.get('happy', 0)} happy cues). The elder appeared engaged and in good spirits."
    else:
        notes = f"Conversations were mostly balanced and calm today (dominant emotion: {dominant_emotion}). Interaction was steady."

    return {
        "elder_id": elder_id,
        "date": date_str,
        "insufficient_data": False,
        "message": f"Daily sentiment summary successfully generated across {total} utterances.",
        "total_utterances": total,
        "sentiment_distribution": sentiment_distribution,
        "sentiment_counts": sentiment_counts,
        "emotion_counts": emotion_counts,
        "dominant_sentiment": dominant_sentiment,
        "dominant_emotion": dominant_emotion,
        "average_confidence": avg_conf,
        "wellbeing_notes": notes,
    }


def get_historical_trends(
    elder_id: str,
    days: int = 7,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    init_db(db_path)
    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=days - 1)).date()
    start_iso = f"{start_date.isoformat()}T00:00:00"

    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT * FROM sentiment_records 
            WHERE elder_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (elder_id, start_iso),
        )
        rows = [dict(r) for r in cursor.fetchall()]

    total = len(rows)
    if total < 3:
        return {
            "elder_id": elder_id,
            "days_analyzed": days,
            "insufficient_data": True,
            "message": f"Insufficient historical data across {days} days ({total} utterance{'s' if total != 1 else ''} recorded; minimum 3 required).",
            "total_utterances": total,
            "daily_trends": [],
            "trend_direction": "insufficient_history",
            "top_emotions": [],
            "wellbeing_alerts": [],
        }

    # Group by date
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    overall_emotions: Dict[str, int] = {}

    for r in rows:
        d = r["timestamp"][:10]
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(r)
        e = r["emotion"]
        overall_emotions[e] = overall_emotions.get(e, 0) + 1

    daily_trends = []
    daily_pos_ratios = []

    for d, d_rows in sorted(by_date.items()):
        d_total = len(d_rows)
        d_sent_counts: Dict[str, int] = {}
        d_emot_counts: Dict[str, int] = {}
        for dr in d_rows:
            ds = dr["sentiment"]
            de = dr["emotion"]
            d_sent_counts[ds] = d_sent_counts.get(ds, 0) + 1
            d_emot_counts[de] = d_emot_counts.get(de, 0) + 1

        d_dom_sent = max(d_sent_counts, key=d_sent_counts.get)
        d_dom_emot = max(d_emot_counts, key=d_emot_counts.get)
        pos_ratio = round(d_sent_counts.get("positive", 0) / d_total, 2)
        neg_ratio = round(d_sent_counts.get("negative", 0) / d_total, 2)

        daily_pos_ratios.append(pos_ratio)
        daily_trends.append({
            "date": d,
            "utterances_count": d_total,
            "dominant_sentiment": d_dom_sent,
            "dominant_emotion": d_dom_emot,
            "positive_ratio": pos_ratio,
            "negative_ratio": neg_ratio,
        })

    # Evaluate trend direction
    if len(daily_pos_ratios) >= 2:
        first_half = sum(daily_pos_ratios[: len(daily_pos_ratios) // 2]) / max(1, len(daily_pos_ratios) // 2)
        second_half = sum(daily_pos_ratios[len(daily_pos_ratios) // 2 :]) / max(1, len(daily_pos_ratios) - len(daily_pos_ratios) // 2)
        if second_half - first_half >= 0.15:
            trend_dir = "improving"
        elif first_half - second_half >= 0.15:
            trend_dir = "declining"
        else:
            trend_dir = "stable"
    else:
        trend_dir = "insufficient_history"

    top_emotions = [
        k for k, _ in sorted(overall_emotions.items(), key=lambda x: x[1], reverse=True)[:3]
    ]

    # Alerts
    alerts = []
    lonely_count = overall_emotions.get("lonely", 0)
    anxious_count = overall_emotions.get("anxious", 0)
    sad_count = overall_emotions.get("sad", 0)

    if lonely_count >= 3:
        alerts.append(f"Frequent expressions of loneliness noted ({lonely_count} times in the period). Consider coordinating caregiver/family phone calls.")
    if anxious_count >= 3:
        alerts.append(f"Elevated anxiety cues observed ({anxious_count} times in the period). Ensure routine wellness reassurance.")
    if sad_count >= 4 and trend_dir == "declining":
        alerts.append("Downward emotional trajectory with elevated sadness patterns. Non-clinical observation shared for caregiver support.")

    return {
        "elder_id": elder_id,
        "days_analyzed": days,
        "insufficient_data": False,
        "message": f"Historical trend generated across {len(daily_trends)} active days.",
        "total_utterances": total,
        "daily_trends": daily_trends,
        "trend_direction": trend_dir,
        "top_emotions": top_emotions,
        "wellbeing_alerts": alerts,
    }
