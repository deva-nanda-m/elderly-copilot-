import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "database" / "ddi.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            medicine_name TEXT NOT NULL,
            dose TEXT,
            frequency TEXT,
            active INTEGER DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS drug_interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drug_a TEXT NOT NULL,
            drug_b TEXT NOT NULL,
            severity TEXT NOT NULL,
            effect TEXT NOT NULL,
            description TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            source TEXT
        )
    """)

    # Demo user
    existing_user = conn.execute(
        "SELECT id FROM users WHERE id = 1"
    ).fetchone()

    if not existing_user:
        conn.execute(
            "INSERT INTO users (id, name) VALUES (?, ?)",
            (1, "Demo Elder")
        )

    # Demo interaction data — for software demonstration only.
    # Replace/expand this with a properly licensed, authoritative DDI dataset
    # before using the system for real clinical decisions.
    demo_interactions = [
        (
            "warfarin", "ibuprofen", "High",
            "Increased bleeding risk",
            "Using these medicines together may increase the risk of bleeding.",
            "Consult a doctor or pharmacist before using these medicines together.",
            "Demo data — verify with an authoritative drug-interaction source."
        ),
        (
            "warfarin", "aspirin", "High",
            "Increased bleeding risk",
            "Using these medicines together may increase the risk of bleeding.",
            "Consult a doctor or pharmacist before using these medicines together.",
            "Demo data — verify with an authoritative drug-interaction source."
        ),
        (
            "simvastatin", "clarithromycin", "High",
            "Increased statin exposure",
            "This combination can increase exposure to simvastatin and may increase the risk of adverse effects.",
            "Consult a doctor or pharmacist before using these medicines together.",
            "Demo data — verify with an authoritative drug-interaction source."
        ),
        (
            "metformin", "cimetidine", "Moderate",
            "Possible change in metformin exposure",
            "This combination may alter metformin exposure and requires professional review.",
            "Ask a doctor or pharmacist whether additional monitoring is needed.",
            "Demo data — verify with an authoritative drug-interaction source."
        ),
    ]

    count = conn.execute(
        "SELECT COUNT(*) AS count FROM drug_interactions"
    ).fetchone()["count"]

    if count == 0:
        conn.executemany("""
            INSERT INTO drug_interactions
            (drug_a, drug_b, severity, effect, description, recommendation, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, demo_interactions)

    conn.commit()
    conn.close()


def add_medication(user_id, medicine_name, dose="", frequency=""):
    conn = get_connection()
    conn.execute("""
        INSERT INTO medications
        (user_id, medicine_name, dose, frequency)
        VALUES (?, ?, ?, ?)
    """, (user_id, medicine_name, dose, frequency))
    conn.commit()
    conn.close()


def get_user_medications(user_id):
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, medicine_name, dose, frequency
        FROM medications
        WHERE user_id = ? AND active = 1
        ORDER BY id DESC
    """, (user_id,)).fetchall()
    conn.close()
    return rows
