from datetime import date
import re
from urllib.parse import unquote, urlsplit

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
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

    user = database.get_user_identity(user_id, current_app.config["DB_PATH"])
    if user is None or user["role"] not in VALID_ROLES:
        current_app.logger.warning("Kundsession avvisades: kontot saknas eller har en ogiltig roll.")
        session.clear()
        return None

    role = user["role"]
    session.update(user_name=user["name"], user_email=user["email"],
                   organization_number=user["organization_number"])
    session["role"] = role
    return role


def _safe_next(value):
    """Only resume local booking pages; reject external and ambiguous URLs."""
    if not value or len(value) > 2000:
        return None
    decoded = unquote(value)
    if any(ord(char) < 32 for char in decoded) or "\\" in decoded:
        return None
    if not decoded.startswith("/") or decoded.startswith("//"):
        return None
    try:
        parsed = urlsplit(decoded)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        return None
    if not re.fullmatch(r"/bookings/[1-9][0-9]*", parsed.path):
        return None
    return value


def _booking_login_redirect():
    return redirect(url_for("user_page.login", next=request.full_path.rstrip("?")))


def _check_csrf():
    if not validate_booking_csrf_token(request.form.get("csrf_token", "")):
        abort(400, description="Formuläret kunde inte verifieras. Ladda om sidan och försök igen.")


def booking_detail_context(booking, *, is_admin=False, proposal_values=None):
    db_path = current_app.config["DB_PATH"]
    proposals = database.get_booking_proposals(booking["id"], db_path)
    latest = proposals[-1] if proposals else None
    # Proposal history is ordered oldest first by the data layer.
    active = next((p for p in reversed(proposals) if p["state"] == "pending"), None)
    requested_proposal_id = request.args.get("proposal_id", type=int)
    stale_proposal = requested_proposal_id is not None and (active is None or active["id"] != requested_proposal_id)
    defaults = {
        "proposed_date": (latest or {}).get("proposed_date") or booking.get("onskat_datum") or "",
        "proposed_city": (latest or {}).get("proposed_city") or booking.get("onskad_ort") or booking.get("ort") or "",
        "proposed_venue": (latest or {}).get("proposed_venue") or booking.get("lokaladress") or (booking.get("lokal") if not booking.get("lokalansvar") else "") or "",
    }
    return dict(
        booking=booking, proposals=proposals,
        history=database.get_booking_history(booking["id"], db_path, include_private=is_admin),
        is_admin=is_admin, csrf_token=get_booking_csrf_token(),
        status_labels=database.BOOKING_STATUSES,
        customer_cancellable=database.CUSTOMER_CANCELLABLE,
        admin_targets=sorted(database.ADMIN_TRANSITIONS.get(booking["status"], set())),
        active_proposal=active, proposal_values=proposal_values or defaults,
        selected_action=request.args.get("action") if not stale_proposal and request.args.get("action") in {"accept", "new_date", "cancel"} else None,
        selected_proposal_id=requested_proposal_id, stale_proposal=stale_proposal,
    )


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
    if antal < 1 or antal > 2147483647:
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
    if _current_database_role() is None:
        current_app.logger.warning("Åtkomst till kundöversikten kräver inloggning.")
        return redirect(url_for('user_page.login'))
    name = session.get('user_name')
    email = session.get('user_email')
    organization_number = session.get('organization_number')
    if not email:
        current_app.logger.warning("Kundsession avvisades: obligatoriska kontouppgifter saknas.")
        session.clear()
        return redirect(url_for('user_page.login'))
    bookings = database.list_booking_details(current_app.config["DB_PATH"], email=email)
    current_app.logger.debug("Kundöversikt hämtad med %s bokningar.", len(bookings))

    return render_template(
        'dashboard.html',
        name=name,
        organization_number=organization_number,
        bookings=bookings,
        csrf_token=get_booking_csrf_token(),
        status_labels=database.BOOKING_STATUSES,
        customer_cancellable=database.CUSTOMER_CANCELLABLE,
    )

@user_page.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        _check_csrf()
        db_path = current_app.config["DB_PATH"]
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        organization_number = request.form.get('organization_number', '').strip()
        if not name or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not password or not organization_number:
            current_app.logger.warning("Kontoregistrering nekades: ogiltiga eller saknade formulärfält.")
            return render_template('signup.html', error='Fyll i namn, giltig e-postadress, lösenord och organisationsnummer.', csrf_token=get_booking_csrf_token()), 400
        if database.check_user_exists(email, db_path):
            current_app.logger.warning("Kontoregistrering nekades: kontot finns redan.")
            return render_template('signup.html', error='Det finns redan ett konto med denna e-postadress.', csrf_token=get_booking_csrf_token()), 400
        role = "user"
        user = database.create_user(name, email, generate_password_hash(password), organization_number, role, db_path)
        current_app.logger.info("Kundkonto skapat: user_id=%s.", user[0])
        session.clear()
        session['user_id'] = user[0]
        session['user_name'] = user[1]
        session['organization_number'] = user[2]
        session['user_email'] = user[3]
        session['role'] = role
        return redirect(url_for('user_page.login'))
    else:
        return render_template('signup.html', csrf_token=get_booking_csrf_token())
    
    
@user_page.route('/login', methods=['GET', 'POST'])
def login():
    next_url = _safe_next(request.form.get("next") if request.method == "POST" else request.args.get("next"))
    if request.method == 'GET':
        if role := _current_database_role():
            if next_url:
                return redirect(next_url)
            return _dashboard_redirect(role)
    elif request.method == 'POST':
        _check_csrf()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if not email or not password:
            current_app.logger.warning("Inloggning nekades: inloggningsuppgifter saknas.")
            return render_template('login.html', error='Ange både e-postadress och lösenord.', next_url=next_url, csrf_token=get_booking_csrf_token())

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
            current_app.logger.info("Inloggning lyckades: user_id=%s role=%s.", user_id, role)
            return redirect(next_url) if next_url else _dashboard_redirect(role)

        current_app.logger.warning("Inloggning nekades: felaktiga inloggningsuppgifter eller otillåten roll.")
        return render_template('login.html', error='Felaktig e-postadress eller lösenord.', next_url=next_url, csrf_token=get_booking_csrf_token())
    return render_template('login.html', next_url=next_url, csrf_token=get_booking_csrf_token())


@user_page.route('/booking', methods=['GET', 'POST'])
def booking():
    if _current_database_role() is None:
        current_app.logger.warning("Åtkomst till bokningsformuläret kräver inloggning.")
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
        current_app.logger.warning("Bokningsförfrågan nekades: ogiltiga fält=%s.", ",".join(sorted(errors)))
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
        current_app.logger.warning("Bokningsförfrågan nekades: obligatoriska kontouppgifter saknas.")
        return redirect(url_for("logout"))

    booking_id = database.create_booking_request(
        name=name,
        email=email,
        organization_number=organization_number,
        db_path=current_app.config["DB_PATH"],
        **normalized,
    )
    clear_booking_csrf_token()
    current_app.logger.info("Bokningsförfrågan skapad: booking_id=%s.", booking_id)
    return redirect(url_for('user_page.dashboard'))


@user_page.route('/cancel_booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    role = _current_database_role()
    if role is None:
        current_app.logger.warning("Avbokning nekades: inloggning krävs. booking_id=%s.", booking_id)
        return redirect(url_for('user_page.login'))
    if not validate_booking_csrf_token(request.form.get("csrf_token", "")):
        return "Formuläret kunde inte verifieras. Ladda om sidan och försök igen.", 400

    db_path = current_app.config["DB_PATH"]
    email = session.get("user_email")
    # The database transition checks both ownership and the allowed state.
    changed = database.transition_booking(
        booking_id, "cancelled", db_path,
        actor="admin" if role == "admin" else "customer",
        email=email, actor_id=session["user_id"],
    )
    clear_booking_csrf_token()
    if not changed:
        current_app.logger.warning("Avbokning nekades: aktuell status eller behörighet tillåter inte ändringen. booking_id=%s.", booking_id)
        flash("Bokningen kunde inte avbrytas. Kontrollera aktuell status och behörighet.", "error")
    else:
        current_app.logger.info("Bokning avbruten: booking_id=%s.", booking_id)
    if role == "admin":
        return redirect(url_for('admin_page.dashboard'))
    return redirect(url_for('user_page.dashboard'))


@user_page.route('/bookings/<int:booking_id>', methods=['GET'])
def booking_detail(booking_id):
    role = _current_database_role()
    if role is None:
        current_app.logger.warning("Bokningsdetaljer kräver inloggning: booking_id=%s.", booking_id)
        return _booking_login_redirect()
    booking = database.get_booking_details_by_id(booking_id, current_app.config["DB_PATH"])
    if not booking or booking["email"] != session.get("user_email"):
        current_app.logger.warning("Bokningsdetaljer nekades: bokningen saknas eller tillhör ett annat konto. booking_id=%s.", booking_id)
        abort(404)
    current_app.logger.debug("Kundens bokningsdetaljer hämtade: booking_id=%s.", booking_id)
    return render_template("booking_detail.html", **booking_detail_context(booking))


@user_page.route('/bookings/<int:booking_id>/proposals/<int:proposal_id>/respond', methods=['POST'])
def respond_proposal(booking_id, proposal_id):
    if _current_database_role() is None:
        current_app.logger.warning("Svar på datumförslag nekades: inloggning krävs. booking_id=%s proposal_id=%s.", booking_id, proposal_id)
        return redirect(url_for("user_page.login", next=url_for("user_page.booking_detail", booking_id=booking_id)))
    _check_csrf()
    db_path = current_app.config["DB_PATH"]
    booking = database.get_booking_details_by_id(booking_id, db_path)
    if not booking or booking["email"] != session.get("user_email"):
        current_app.logger.warning("Svar på datumförslag nekades: bokningen saknas eller tillhör ett annat konto. booking_id=%s proposal_id=%s.", booking_id, proposal_id)
        abort(404)
    action = request.form.get("action", "")
    if action not in {"accept", "new_date", "cancel"}:
        current_app.logger.warning("Svar på datumförslag nekades: ogiltigt svarsalternativ. booking_id=%s proposal_id=%s.", booking_id, proposal_id)
        abort(400, description="Välj ett giltigt svar på förslaget.")
    changed = database.respond_to_proposal(
        booking_id, proposal_id, session["user_email"], action, db_path,
        actor_id=session["user_id"],
    )
    if not changed:
        current_app.logger.warning("Svar på datumförslag nekades: förslaget är inte längre aktuellt. booking_id=%s proposal_id=%s.", booking_id, proposal_id)
        return render_template(
            "booking_detail.html", error="Förslaget är inte längre aktuellt. Läs bokningens senaste uppgifter innan du svarar.",
            **booking_detail_context(database.get_booking_details_by_id(booking_id, db_path)),
        ), 409
    clear_booking_csrf_token()
    current_app.logger.info("Svar på datumförslag sparat: booking_id=%s proposal_id=%s action=%s.", booking_id, proposal_id, action)
    messages = {
        "accept": "Du har accepterat förslaget. Utbildningen är nu bekräftad.",
        "new_date": "Du har begärt ett nytt datumförslag. Vi återkommer med ett nytt förslag.",
        "cancel": "Bokningen är avbruten. Bokningen och historiken finns kvar på ditt konto.",
    }
    flash(messages[action], "success")
    return redirect(url_for("user_page.booking_detail", booking_id=booking_id))
