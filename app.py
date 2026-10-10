from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename

from database import init_db, get_connection, add_medication, get_user_medications
from ddi_checker import check_interactions
from ocr_ddi_bridge import check_medicine_image

app = Flask(__name__)
app.secret_key = "elder-care-ddi-demo-key"

init_db()

# Demo user for the first prototype
DEMO_USER_ID = 1

# Uploaded medicine images are stored here.
UPLOAD_FOLDER = Path(__file__).parent / "uploads"
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_UPLOAD_SIZE = 8 * 1024 * 1024  # 8 MB

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE


@app.route("/")
def index():
    medications = get_user_medications(DEMO_USER_ID)
    return render_template("index.html", medications=medications)


@app.route("/medications")
def medications():
    meds = get_user_medications(DEMO_USER_ID)
    return render_template("medications.html", medications=meds)


@app.route("/medications/add", methods=["POST"])
def add_medication_route():
    name = request.form.get("medicine_name", "").strip()
    dose = request.form.get("dose", "").strip()
    frequency = request.form.get("frequency", "").strip()

    if not name:
        flash("Please enter a medicine name.", "error")
        return redirect(url_for("medications"))

    add_medication(DEMO_USER_ID, name, dose, frequency)
    flash(f"{name} was added to the medication list.", "success")
    return redirect(url_for("medications"))


@app.route("/check", methods=["POST"])
def check():
    new_medicine = request.form.get("new_medicine", "").strip()

    if not new_medicine:
        flash("Please enter the new medicine name.", "error")
        return redirect(url_for("index"))

    existing = get_user_medications(DEMO_USER_ID)
    existing_names = [m["medicine_name"] for m in existing]

    results = check_interactions(new_medicine, existing_names)

    return render_template(
        "result.html",
        new_medicine=new_medicine,
        results=results,
        existing=existing_names
    )


@app.route("/check-image", methods=["POST"])
def check_image():
    """Identify a medicine from an uploaded photo and check interactions."""
    uploaded_file = request.files.get("medicine_image")

    if not uploaded_file or not uploaded_file.filename:
        flash("Please select a medicine image.", "error")
        return redirect(url_for("index"))

    extension = Path(uploaded_file.filename).suffix.lower()

    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        flash("Please upload a JPG, JPEG, PNG, or WEBP image.", "error")
        return redirect(url_for("index"))

    filename = secure_filename(uploaded_file.filename)
    if not filename:
        flash("The uploaded filename is not valid.", "error")
        return redirect(url_for("index"))

    # Avoid overwriting an existing upload.
    target = UPLOAD_FOLDER / filename
    counter = 1
    while target.exists():
        target = UPLOAD_FOLDER / f"{Path(filename).stem}_{counter}{extension}"
        counter += 1

    uploaded_file.save(target)

    try:
        result = check_medicine_image(target, DEMO_USER_ID)
    except Exception as exc:
        # Keep the user-facing error simple; the terminal will contain the
        # traceback when Flask is running in debug mode.
        flash(f"Could not process the medicine image: {exc}", "error")
        return redirect(url_for("index"))

    return render_template(
        "result.html",
        new_medicine=result.get("medicine"),
        active_ingredient=result.get("active_ingredient"),
        ocr_text=result.get("ocr_text", ""),
        results=result.get("interactions", []),
        existing=result.get("existing_medicines", []),
        matched_alias=result.get("matched_alias"),
        image_check=True,
    )


if __name__ == "__main__":
    app.run(debug=True)
