from medicine_normalizer import normalize_medicine


ocr_text = """
Rx
Metformin Hydrochloride
Tablets IP 500 mg

Glycomet 500

For Oral Use
"""


result = normalize_medicine(ocr_text)


if result:

    print("Medicine detected:")
    print("-------------------")

    print("Medicine:", result["medicine"])
    print("Active ingredient:", result["active_ingredient"])
    print("Matched name:", result["matched_alias"])

else:

    print("Medicine could not be identified.")