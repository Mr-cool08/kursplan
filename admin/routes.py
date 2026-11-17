from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from functools import wraps
import database
import gen_func


admin_page = Blueprint(
    "admin_page",
    __name__,
    template_folder="templates",
    url_prefix="/admin"
)


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:
            return redirect(url_for("admin_page.login"))

        if session.get("role") != "admin":
            return "Forbidden", 403

        return func(*args, **kwargs)

    return wrapper


@admin_page.route("/", methods=["GET"])
@admin_required
def dashboard():
    bookings = database.get_all_bookings(current_app.config["DB_PATH"])
    current_app.logger.debug("Loaded %s bookings for the admin dashboard", len(bookings))
    return render_template(
        "admin_dashboard.html",
        bookings=bookings
    )


@admin_page.route("/bookings", methods=["POST"])
@admin_required
def book():
    booking_ids = []
    for booking_id in request.form.getlist("booking_ids"):
        try:
            booking_ids.append(int(booking_id))
        except ValueError:
            continue

    booking_date = request.form.get("date", "").strip()
    ort = request.form.get("ort", "").strip()

    if not booking_ids or not booking_date or not ort:
        current_app.logger.debug("Missing booking IDs, date or city")
        return redirect(url_for("admin_page.dashboard"))

    db_path = current_app.config["DB_PATH"]
    database.book_bookings(booking_ids, booking_date, ort, db_path)
    for booking_id in booking_ids:
        email = database.get_email_by_booking_id(booking_id, db_path)
        utbildning = database.get_utbildning_by_booking_id(booking_id, db_path)
        gen_func.send_booked_mail(email, booking_date, ort, booking_id, utbildning)
    return redirect(url_for("admin_page.dashboard"))


@admin_page.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = database.login_user(
            email,
            password,
            "admin",
            current_app.config["DB_PATH"]
        )

        if user:
            current_app.logger.debug("Admin login succeeded")
            session["user_id"] = user[0]
            session["role"] = "admin"

            return redirect(url_for("admin_page.dashboard"))
        current_app.logger.debug("Admin login failed")
        return redirect(url_for("admin_page.login"))

    return render_template("login.html")

@admin_page.route("/booking", methods=["GET", "POST"])
def booking():
    return render_template("book.html")
