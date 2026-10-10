from pathlib import Path

import pytesseract
from PIL import Image

from database import get_user_medications
from ddi_checker import check_interactions
from medicine_normalizer import normalize_medicine

from database import get_user_medications
from ddi_checker import check_interactions
from medicine_normalizer import normalize_medicine

# If Tesseract is in the project folder, this path works.
TESSERACT_PATH = Path(__file__).parent / "tesseract.exe"
if TESSERACT_PATH.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)


def extract_medicine_from_image(image_path):
    """Run OCR and convert the OCR text into a normalized medicine."""
    image = Image.open(image_path)
    ocr_text = pytesseract.image_to_string(image)

    detected = normalize_medicine(ocr_text)

    return ocr_text, detected


def check_medicine_image(image_path, user_id=1):
    """
    Full prototype flow:
    image -> OCR -> medicine normalization -> user's medicines -> DDI check
    """
    ocr_text, detected = extract_medicine_from_image(image_path)

    if not detected:
        return {
            "ocr_text": ocr_text,
            "medicine": None,
            "active_ingredient": None,
            "interactions": [],
            "message": "Medicine could not be identified. Please confirm the medicine name manually."
        }

    new_medicine = detected["active_ingredient"]

    rows = get_user_medications(user_id)
    existing_medicines = [row["medicine_name"] for row in rows]

    interactions = check_interactions(new_medicine, existing_medicines)

    return {
        "ocr_text": ocr_text,
        "medicine": detected["medicine"],
        "active_ingredient": detected["active_ingredient"],
        "matched_alias": detected["matched_alias"],
        "existing_medicines": existing_medicines,
        "interactions": interactions,
    }


if __name__ == "__main__":
    image_path = Path(__file__).parent / "medicine.jpg"

    result = check_medicine_image(image_path)

    print("\n========== OCR TEXT ==========")
    print(result["ocr_text"])

    print("========== MEDICINE ==========")
    print("Medicine:", result["medicine"])
    print("Active ingredient:", result["active_ingredient"])

    if result["interactions"]:
        print("\n========== INTERACTIONS ==========")
        for item in result["interactions"]:
            print(f"Severity: {item['severity']}")
            print(f"Effect: {item['effect']}")
            print(f"Description: {item['description']}")
            print(f"Recommendation: {item['recommendation']}")
            print("-" * 40)
    else:
        print("\nNo interaction was found in the current demo database.")
        print("This does NOT mean that the medicine is clinically interaction-free.")
