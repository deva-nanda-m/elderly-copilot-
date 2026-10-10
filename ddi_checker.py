import re
from database import get_connection


ALIASES = {
    "tylenol": "paracetamol",
    "acetaminophen": "paracetamol",
    "ibuprofen tablet": "ibuprofen",
    "warfarin tablet": "warfarin",
}


def normalize_medicine_name(name):
    """
    Basic normalization for the prototype.
    A production system should use a validated medicine dictionary
    and should not silently guess an uncertain OCR result.
    """
    name = name.lower().strip()
    name = re.sub(r"\s+", " ", name)

    # Remove common strength expressions for simple matching.
    name = re.sub(r"\b\d+(\.\d+)?\s*(mg|mcg|g|ml)\b", "", name)
    name = re.sub(r"\s+", " ", name).strip()

    return ALIASES.get(name, name)


def find_interaction(drug1, drug2):
    a = normalize_medicine_name(drug1)
    b = normalize_medicine_name(drug2)

    conn = get_connection()

    row = conn.execute("""
        SELECT *
        FROM drug_interactions
        WHERE
            (LOWER(drug_a) = ? AND LOWER(drug_b) = ?)
            OR
            (LOWER(drug_a) = ? AND LOWER(drug_b) = ?)
        LIMIT 1
    """, (a, b, b, a)).fetchone()

    conn.close()
    return row


def check_interactions(new_medicine, existing_medicines):
    results = []

    for medicine in existing_medicines:
        interaction = find_interaction(new_medicine, medicine)

        if interaction:
            results.append(dict(interaction))

    severity_order = {
        "High": 0,
        "Moderate": 1,
        "Low": 2
    }

    results.sort(
        key=lambda item: severity_order.get(item["severity"], 99)
    )

    return results
