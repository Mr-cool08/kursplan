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
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("user_page.login"))

        role = database.get_user_role(user_id, current_app.config["DB_PATH"])
        if role is None:
            session.clear()
            return redirect(url_for("user_page.login"))

        # Do not trust a stale session role: external database changes should
        # grant or revoke access on the very next admin request.
        session["role"] = role
        if role != "admin":
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
        bookings=bookings,
        name=session.get("user_name"),
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
    # Preserve old bookmarks and form posts while keeping /login as the one
    # authentication endpoint. A 307 keeps POST data intact.
    status_code = 307 if request.method == "POST" else 302
    return redirect(url_for("user_page.login"), code=status_code)

@admin_page.route("/booking", methods=["GET", "POST"])
@admin_required
def booking():
    return render_template("book.html")
