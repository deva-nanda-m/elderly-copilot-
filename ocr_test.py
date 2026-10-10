import pytesseract
import re
from PIL import Image

# Tesseract location
pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Users\home\Documents\Downloads\elder_care_ddi_v1\tesseract.exe"
)

# Medicine image
image_path = "medicine.jpg"

# Open image
image = Image.open(image_path)

# OCR
text = pytesseract.image_to_string(image)

print("\n========== OCR OUTPUT ==========")
print(text)

# ---------------------------------
# Simple medicine-name extraction
# ---------------------------------

medicine_keywords = [
    "metformin",
    "warfarin",
    "ibuprofen",
    "paracetamol",
    "aspirin",
    "amlodipine",
    "atorvastatin",
    "losartan",
    "omeprazole",
    "pantoprazole"
]

text_lower = text.lower()

detected_medicine = None

for medicine in medicine_keywords:
    if medicine in text_lower:
        detected_medicine = medicine.title()
        break

print("\n========== MEDICINE IDENTIFICATION ==========")

if detected_medicine:
    print("Detected medicine:", detected_medicine)
else:
    print("Medicine could not be identified.")