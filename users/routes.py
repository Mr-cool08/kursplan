from datetime import date
import re

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from csrf import (
    clear_booking_csrf_token,
    get_booking_csrf_token,
    validate_booking_csrf_token,
)
import database
from gen_func import read_utbildningar_from_json


user_page = Blueprint('user_page', __name__,
                        template_folder='templates')

VALID_ROLES = {"user", "admin"}

BOOKING_FORM_FIELDS = (
    "datumtyp",
    "onskat_datum",
    "onskat_startdatum",
    "onskat_slutdatum",
    "utbildningskalla",
    "utbildning",
    "egen_utbildning",
    "ort",
    "antal",
    "lokalansvar",
    "lokaladress",
)

BOOKING_FORM_DEFAULTS = {
    "datumtyp": "exakt",
    "onskat_datum": "",
    "onskat_startdatum": "",
    "onskat_slutdatum": "",
    "utbildningskalla": "katalog",
    "utbildning": "",
    "egen_utbildning": "",
    "ort": "",
    "antal": "",
    "lokalansvar": "",
    "lokaladress": "",
}


def _dashboard_redirect(role):
    if role == "admin":
        return redirect(url_for("admin_page.dashboard"))
    return redirect(url_for("user_page.dashboard"))


def _current_database_role():
    user_id = session.get("user_id")
    if not user_id:
        return None

    role = database.get_user_role(user_id, current_app.config["DB_PATH"])
    if role not in VALID_ROLES:
        session.clear()
        return None

    session["role"] = role
    return role


def _booking_values(form=None):
    if form is None:
        return BOOKING_FORM_DEFAULTS.copy()
    return {field: form.get(field, "") for field in BOOKING_FORM_FIELDS}


def _render_booking_form(*, utbildningar, values=None, errors=None, status=200):
    response = render_template(
        "book.html",
        utbildningar=utbildningar,
        values=values or BOOKING_FORM_DEFAULTS.copy(),
        errors=errors or {},
        csrf_token=get_booking_csrf_token(),
    )
    return (response, status) if status != 200 else response


def _parse_iso_date(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.isoformat() == value else None


def _validate_booking_form(form, utbildningar):
    errors = {}
    normalized = {
        "datumtyp": None,
        "onskat_datum": None,
        "onskat_startdatum": None,
        "onskat_slutdatum": None,
        "utbildning": None,
        "utbildningskalla": None,
        "antal": None,
        "onskad_ort": None,
        "lokalansvar": None,
        "lokaladress": None,
    }

    datumtyp = form.get("datumtyp", "").strip()
    if datumtyp == "exakt":
        exact_value = form.get("onskat_datum", "").strip()
        if not exact_value:
            errors["onskat_datum"] = "Ange ett datum."
        elif _parse_iso_date(exact_value) is None:
            errors["onskat_datum"] = "Ange ett giltigt datum."
        else:
            normalized["datumtyp"] = "exakt"
            normalized["onskat_datum"] = exact_value
    elif datumtyp == "intervall":
        start_value = form.get("onskat_startdatum", "").strip()
        end_value = form.get("onskat_slutdatum", "").strip()
        start_date = _parse_iso_date(start_value) if start_value else None
        end_date = _parse_iso_date(end_value) if end_value else None

        if not start_value:
            errors["onskat_startdatum"] = "Ange intervallets startdatum."
        elif start_date is None:
            errors["onskat_startdatum"] = "Ange ett giltigt startdatum."

        if not end_value:
            errors["onskat_slutdatum"] = "Ange intervallets slutdatum."
        elif end_date is None:
            errors["onskat_slutdatum"] = "Ange ett giltigt slutdatum."

        if start_date is not None and end_date is not None:
            if start_date == end_date:
                errors["onskat_slutdatum"] = "Datumen i intervallet måste vara olika."
            elif end_date < start_date:
                errors["onskat_slutdatum"] = "Till och med måste vara senare än Från och med."

        if not any(field in errors for field in ("onskat_startdatum", "onskat_slutdatum")):
            normalized.update(
                datumtyp="intervall",
                onskat_startdatum=start_value,
                onskat_slutdatum=end_value,
            )
    else:
        errors["datumtyp"] = "Välj exakt datum eller datumintervall."

    utbildningskalla = form.get("utbildningskalla", "").strip()
    if utbildningskalla == "katalog":
        utbildning = form.get("utbildning", "").strip()
        if not utbildning or utbildning not in utbildningar:
            errors["utbildning"] = "Välj en utbildning ur listan."
        else:
            normalized["utbildningskalla"] = "katalog"
            normalized["utbildning"] = utbildning
    elif utbildningskalla == "fritext":
        utbildning = form.get("egen_utbildning", "").strip()
        if not utbildning:
            errors["egen_utbildning"] = "Ange utbildningens namn."
        else:
            normalized["utbildningskalla"] = "fritext"
            normalized["utbildning"] = utbildning
    else:
        errors["utbildningskalla"] = "Välj hur utbildningen ska anges."

    ort = form.get("ort", "").strip()
    normalized["onskad_ort"] = ort or None

    antal_value = form.get("antal", "").strip()
    try:
        antal = int(antal_value) if re.fullmatch(r"[0-9]+", antal_value or "") else 0
    except ValueError:
        antal = 0
    if antal < 1:
        errors["antal"] = "Antal deltagare måste vara ett heltal från 1."
    else:
        normalized["antal"] = antal

    lokalansvar = form.get("lokalansvar", "").strip()
    if lokalansvar == "kund":
        lokaladress = form.get("lokaladress", "").strip()
        if not lokaladress:
            errors["lokaladress"] = "Ange adressen till utbildningslokalen."
        else:
            normalized.update(lokalansvar="kund", lokaladress=lokaladress)
    elif lokalansvar == "sakerhetsutbildarna":
        normalized.update(lokalansvar="sakerhetsutbildarna", lokaladress=None)
    else:
        errors["lokalansvar"] = "Välj vem som ordnar utbildningslokalen."

    return normalized, errors

@user_page.route('/')
def index():
    if role := _current_database_role():
        return _dashboard_redirect(role)
    return render_template('index.html')

@user_page.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('user_page.login'))
    name = session.get('user_name')
    email = session.get('user_email')
    organization_number = session.get('organization_number')
    if not email:
        session.clear()
        return redirect(url_for('user_page.login'))
    bookings = database.get_bookings_by_email(email, current_app.config["DB_PATH"])

    return render_template(
        'dashboard.html',
        name=name,
        organization_number=organization_number,
        bookings=bookings,
        csrf_token=get_booking_csrf_token(),
    )

@user_page.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        db_path = current_app.config["DB_PATH"]
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        organization_number = request.form['organization_number']
        if database.check_user_exists(email, db_path):
            return render_template('signup.html', error='A user with this email already exists.')
        role = "user"
        user = database.create_user(name, email, password, organization_number, role, db_path)
        session.clear()
        session['user_id'] = user[0]
        session['user_name'] = user[1]
        session['organization_number'] = user[2]
        session['user_email'] = user[3]
        session['role'] = role
        return redirect(url_for('user_page.login'))
    else:
        return render_template('signup.html')
    
    
@user_page.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        if role := _current_database_role():
            return _dashboard_redirect(role)
    elif request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if not email or not password:
            return render_template('login.html', error='Ange både e-postadress och lösenord.')

        user = database.authenticate_user(email, password, current_app.config["DB_PATH"])
        if user and user[4] in VALID_ROLES:
            user_id, name, organization_number, user_email, role = user
            session.clear()
            session.update(
                user_id=user_id,
                user_name=name,
                organization_number=organization_number,
                user_email=user_email,
                role=role,
            )
            return _dashboard_redirect(role)

        return render_template('login.html', error='Felaktig e-postadress eller lösenord.')
    return render_template('login.html')


@user_page.route('/booking', methods=['GET', 'POST'])
def booking():
    if 'user_id' not in session:
        return redirect(url_for('user_page.login'))

    utbildningar = read_utbildningar_from_json()
    if request.method == 'GET':
        return _render_booking_form(utbildningar=utbildningar)

    values = _booking_values(request.form)
    submitted_token = request.form.get("csrf_token", "")
    if not validate_booking_csrf_token(submitted_token):
        return _render_booking_form(
            utbildningar=utbildningar,
            values=values,
            errors={
                "csrf_token": (
                    "Formuläret kunde inte verifieras. Ladda om sidan och försök igen."
                )
            },
            status=400,
        )

    normalized, errors = _validate_booking_form(request.form, utbildningar)
    if errors:
        return _render_booking_form(
            utbildningar=utbildningar,
            values=values,
            errors=errors,
            status=400,
        )

    name = session.get('user_name')
    email = session.get('user_email')
    organization_number = session.get('organization_number')
    if name is None or email is None or organization_number is None:
        return redirect(url_for("logout"))

    booking_id = database.create_booking_request(
        name=name,
        email=email,
        organization_number=organization_number,
        db_path=current_app.config["DB_PATH"],
        **normalized,
    )
    clear_booking_csrf_token()
    current_app.logger.debug("Created booking %s", booking_id)
    return redirect(url_for('user_page.dashboard'))


@user_page.route('/cancel_booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    role = _current_database_role()
    if role is None:
        return redirect(url_for('user_page.login'))
    if not validate_booking_csrf_token(request.form.get("csrf_token", "")):
        return "Formuläret kunde inte verifieras. Ladda om sidan och försök igen.", 400

    db_path = current_app.config["DB_PATH"]
    if role == "admin":
        database.cancel_booking(booking_id, db_path)
        clear_booking_csrf_token()
        return redirect(url_for('admin_page.dashboard'))

    email = session.get("user_email")
    if email:
        database.cancel_booking_for_email(booking_id, email, db_path)
    clear_booking_csrf_token()
    return redirect(url_for('user_page.dashboard'))
