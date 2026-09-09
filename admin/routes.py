from flask import render_template, request, session, redirect, url_for, Blueprint
from functools import wraps
import database
import os


db_path = os.getenv("db_path")

if not db_path:
    raise ValueError("Database path is not set in the .env file.")


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


@admin_page.route("/")
@admin_required
def dashboard():
    bookings = database.get_all_bookings(db_path)
    print(bookings)
    return render_template(
        "dashboard.html",
        bookings=bookings
    )


@admin_page.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        print(email, password)
        user = database.login_user(
            email,
            password,
            "admin",
            db_path
        )

        if user:
            print("User logged in:", user)
            session["user_id"] = user[0]
            session["role"] = "admin"

            return redirect(url_for("admin_page.dashboard"))
        print("Login failed", user)
        return redirect(url_for("admin_page.login"))

    return render_template("login.html")

@admin_page.route("/booking", methods=["GET", "POST"])
def booking():
    return render_template("book.html")