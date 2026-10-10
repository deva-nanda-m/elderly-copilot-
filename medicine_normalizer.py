import re


# Common medicine names and their active ingredients
MEDICINE_DATABASE = {
    "metformin": {
        "active_ingredient": "Metformin",
        "aliases": [
            "metformin",
            "glycomet"
        ]
    },

    "warfarin": {
        "active_ingredient": "Warfarin",
        "aliases": [
            "warfarin",
            "coumadin"
        ]
    },

    "ibuprofen": {
        "active_ingredient": "Ibuprofen",
        "aliases": [
            "ibuprofen",
            "brufen",
            "advil"
        ]
    },

    "paracetamol": {
        "active_ingredient": "Paracetamol",
        "aliases": [
            "paracetamol",
            "acetaminophen",
            "crocin"
        ]
    },

    "aspirin": {
        "active_ingredient": "Aspirin",
        "aliases": [
            "aspirin",
            "acetylsalicylic acid"
        ]
    },

    "amlodipine": {
        "active_ingredient": "Amlodipine",
        "aliases": [
            "amlodipine"
        ]
    },

    "atorvastatin": {
        "active_ingredient": "Atorvastatin",
        "aliases": [
            "atorvastatin"
        ]
    },

    "losartan": {
        "active_ingredient": "Losartan",
        "aliases": [
            "losartan"
        ]
    },

    "omeprazole": {
        "active_ingredient": "Omeprazole",
        "aliases": [
            "omeprazole"
        ]
    },

    "pantoprazole": {
        "active_ingredient": "Pantoprazole",
        "aliases": [
            "pantoprazole"
        ]
    }
}


def normalize_medicine(ocr_text):

    text = ocr_text.lower()

    # Remove punctuation
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # Search medicine aliases
    for medicine, data in MEDICINE_DATABASE.items():

        for alias in data["aliases"]:

            if alias.lower() in text:

                return {
                    "medicine": medicine.title(),
                    "active_ingredient": data["active_ingredient"],
                    "matched_alias": alias
                }

    return None