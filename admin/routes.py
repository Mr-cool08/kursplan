from datetime import date
from functools import wraps

from flask import Blueprint, abort, current_app, flash, jsonify, make_response, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from csrf import clear_booking_csrf_token, get_booking_csrf_token
import company_lookup
import database
import gen_func
from users.routes import _check_csrf, booking_detail_context, notify_booking_status


admin_page = Blueprint("admin_page", __name__, template_folder="templates", url_prefix="/admin")


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            current_app.logger.warning("Administratörsåtkomst nekades: inloggning krävs.")
            return redirect(url_for("user_page.login"))
        user = database.get_user_identity(user_id, current_app.config["DB_PATH"])
        if user is None or not user["email_verified"]:
            current_app.logger.warning("Administratörsåtkomst nekades: kontot finns inte längre.")
            session.clear()
            return redirect(url_for("user_page.login"))
        session["role"] = user["role"]
        if user["role"] != "admin":
            current_app.logger.warning("Administratörsåtkomst nekades: administratörsbehörighet saknas.")
            return "Åtkomst nekad.", 403
        return func(*args, **kwargs)
    return wrapper


def _get_booking(booking_id):
    booking = database.get_booking_details_by_id(booking_id, current_app.config["DB_PATH"])
    if booking is None:
        current_app.logger.warning("Administratörens bokningsåtgärd nekades: bokningen saknas. booking_id=%s.", booking_id)
        abort(404)
    return booking


def _proposal_values():
    return {name: request.form.get(name, "").strip() for name in (
        "proposed_date", "proposed_city", "proposed_venue"
    )}


def _proposal_error(values):
    try:
        parsed = date.fromisoformat(values["proposed_date"])
    except ValueError:
        parsed = None
    if parsed is None or parsed.isoformat() != values["proposed_date"]:
        return "Ange ett giltigt utbildningsdatum."
    if not values["proposed_city"] or len(values["proposed_city"]) > 200:
        return "Ange en ort med högst 200 tecken."
    if not values["proposed_venue"] or len(values["proposed_venue"]) > 1000:
        return "Ange en utbildningslokal eller adress med högst 1 000 tecken."
    return None


def _confirmation_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="booking-proposal-preview")


def _confirmation_data(booking, values):
    proposals = database.get_booking_proposals(booking["id"], current_app.config["DB_PATH"])
    return dict(booking_id=booking["id"], user_id=session["user_id"],
                status=booking["status"], previous_proposal_id=proposals[-1]["id"] if proposals else None,
                values=values)


def _detail_response(booking, error, status=400, values=None):
    return render_template(
        "booking_detail.html", error=error,
        **booking_detail_context(booking, is_admin=True, proposal_values=values),
    ), status


def _deliver_proposal(booking, proposal):
    """Reserve delivery before SMTP so repeated requests cannot send twice."""
    db_path = current_app.config["DB_PATH"]
    if not database.claim_proposal_delivery(proposal["id"], db_path, actor_id=session["user_id"]):
        current_app.logger.warning("E-postutskick hoppades över: redan hanterat eller pågående. booking_id=%s proposal_id=%s.", booking["id"], proposal["id"])
        flash("Utskicket har redan hanterats eller pågår. Kontrollera förslagets leveransstatus.", "warning")
        return
    # Never build email links from an untrusted Host header.
    booking_url = "https://kursplan.se" + url_for("user_page.booking_detail", booking_id=booking["id"])
    current_app.logger.debug("E-postleverans av datumförslag påbörjas: booking_id=%s proposal_id=%s.", booking["id"], proposal["id"])
    try:
        result = gen_func.send_proposal_mail(booking, proposal, booking_url)
    except Exception:
        # SMTP exceptions can contain credentials or addresses.
        current_app.logger.error("Oväntat fel vid e-postutskick: booking_id=%s proposal_id=%s.", booking["id"], proposal["id"])
        result = "failed"
    if result not in {"sent", "disabled", "failed"}:
        current_app.logger.error("E-postleveransen gav ett okänt resultat: booking_id=%s proposal_id=%s.", booking["id"], proposal["id"])
        result = "failed"
    database.finish_proposal_delivery(proposal["id"], result, db_path)
    if result == "sent":
        current_app.logger.info("Datumförslag levererat med e-post: booking_id=%s proposal_id=%s.", booking["id"], proposal["id"])
        flash("Datumförslaget har sparats och e-post har skickats till kunden.", "success")
    elif result == "disabled":
        current_app.logger.info("E-postutskick avstängt; datumförslag sparat i kundportalen: booking_id=%s proposal_id=%s.", booking["id"], proposal["id"])
        flash("Datumförslaget har sparats i kundportalen. E-post är avstängd (send_mail=0); inget meddelande har skickats.", "warning")
    else:
        current_app.logger.error("E-postleverans misslyckades; datumförslag finns kvar för nytt försök: booking_id=%s proposal_id=%s.", booking["id"], proposal["id"])
        flash("Datumförslaget har sparats i kundportalen, men e-postmeddelandet kunde inte skickas. Kontrollera SMTP-inställningarna och försök skicka igen.", "error")


@admin_page.route("/", methods=["GET"])
@admin_required
def dashboard():
    status = request.args.get("status", "")
    if status and status not in database.BOOKING_STATUSES:
        current_app.logger.warning("Administratörsöversikt nekades: ogiltigt statusfilter.")
        abort(400, description="Välj en giltig bokningsstatus.")
    bookings = database.list_booking_details(current_app.config["DB_PATH"], status=status or None)
    current_app.logger.debug("Administratörsöversikt hämtad med %s bokningar.", len(bookings))
    return render_template(
        "admin_dashboard.html", bookings=bookings, name=session.get("user_name"),
        csrf_token=get_booking_csrf_token(), status_labels=database.BOOKING_STATUSES,
        selected_status=status,
    )


@admin_page.route("/logs", methods=["GET"])
@admin_required
def logs():
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        abort(400, description="Ange ett giltigt sidnummer.")
    if page < 1 or page > 2147483647:
        abort(400, description="Ange ett giltigt sidnummer.")
    page_size = 50
    events = database.list_booking_audit_log(
        current_app.config["DB_PATH"], limit=page_size + 1, offset=(page - 1) * page_size,
    )
    response = make_response(render_template(
        "admin_logs.html", events=events[:page_size], page=page,
        has_next=len(events) > page_size, status_labels=database.BOOKING_STATUSES,
    ))
    response.headers["Cache-Control"] = "private, no-store"
    return response


@admin_page.route("/bookings", methods=["POST"])
@admin_required
def book():
    _check_csrf()
    current_app.logger.warning("Direkt acceptans av bokningar nekades: kundens godkännande av datumförslag krävs.")
    return "Öppna en bokning och skicka ett datumförslag. Kunden måste godkänna förslaget innan bokningen accepteras.", 400


@admin_page.route("/bookings/<int:booking_id>/company", methods=["GET"])
@admin_required
def company_name(booking_id):
    booking = _get_booking(booking_id)
    result = company_lookup.service.lookup(booking["organization_number"])
    response = jsonify(status=result.status, name=result.name)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@admin_page.route("/bookings/<int:booking_id>", methods=["GET"])
@admin_required
def booking_detail(booking_id):
    current_app.logger.debug("Administratörens bokningsdetaljer begärda: booking_id=%s.", booking_id)
    return render_template("booking_detail.html", **booking_detail_context(_get_booking(booking_id), is_admin=True))


@admin_page.route("/bookings/<int:booking_id>/status", methods=["POST"])
@admin_required
def change_status(booking_id):
    _check_csrf()
    booking = _get_booking(booking_id)
    if not database.transition_booking(
        booking_id, request.form.get("target", ""), current_app.config["DB_PATH"],
        actor="admin", actor_id=session["user_id"],
    ):
        current_app.logger.warning("Administrativ statusändring nekades: otillåten övergång eller inaktuell status. booking_id=%s.", booking_id)
        return _detail_response(booking, "Statusändringen är inte tillåten för bokningens aktuella status.", 409)
    clear_booking_csrf_token()
    current_app.logger.info("Bokningsstatus uppdaterad av administratör: booking_id=%s status=%s.", booking_id, request.form["target"])
    flash("Bokningens status har uppdaterats.", "success")
    if request.form["target"] == "cancelled":
        notify_booking_status(booking_id, "cancelled")
    return redirect(url_for("admin_page.booking_detail", booking_id=booking_id))


@admin_page.route("/bookings/<int:booking_id>/notes", methods=["POST"])
@admin_required
def add_note(booking_id):
    _check_csrf()
    booking = _get_booking(booking_id)
    note = request.form.get("note", "").strip()
    if not note or len(note) > 4000:
        current_app.logger.warning("Administrativ anteckning nekades: ogiltig längd. booking_id=%s.", booking_id)
        return _detail_response(booking, "Ange en anteckning med 1–4 000 tecken.")
    database.add_booking_note(booking_id, note, current_app.config["DB_PATH"], actor_id=session["user_id"])
    current_app.logger.info("Administrativ anteckning sparad: booking_id=%s.", booking_id)
    clear_booking_csrf_token()
    flash("Den administrativa anteckningen har sparats.", "success")
    return redirect(url_for("admin_page.booking_detail", booking_id=booking_id))


@admin_page.route("/bookings/<int:booking_id>/proposal/preview", methods=["POST"])
@admin_required
def preview_proposal(booking_id):
    _check_csrf()
    booking = _get_booking(booking_id)
    values = _proposal_values()
    if booking["status"] not in {"pending", "registered", "new_date_requested"}:
        current_app.logger.warning("Förhandsgranskning av datumförslag nekades: otillåten bokningsstatus. booking_id=%s.", booking_id)
        return _detail_response(booking, "Ett nytt datumförslag kan inte skickas i aktuell status.", 409)
    if error := _proposal_error(values):
        current_app.logger.warning("Förhandsgranskning av datumförslag nekades: ogiltiga formulärfält. booking_id=%s.", booking_id)
        return _detail_response(booking, error, values=values)
    confirmation_token = _confirmation_serializer().dumps(_confirmation_data(booking, values))
    current_app.logger.debug("Datumförslag förhandsgranskat: booking_id=%s.", booking_id)
    return render_template(
        "proposal_preview.html", booking=booking, proposal_values=values,
        confirmation_token=confirmation_token, csrf_token=get_booking_csrf_token(),
        status_labels=database.BOOKING_STATUSES,
    )


@admin_page.route("/bookings/<int:booking_id>/proposal", methods=["POST"])
@admin_required
def send_proposal(booking_id):
    _check_csrf()
    booking = _get_booking(booking_id)
    values = _proposal_values()
    if error := _proposal_error(values):
        current_app.logger.warning("Datumförslag nekades: ogiltiga formulärfält. booking_id=%s.", booking_id)
        return _detail_response(booking, error, values=values)
    try:
        confirmed = _confirmation_serializer().loads(request.form.get("confirmation_token", ""), max_age=1800)
    except BadSignature:
        current_app.logger.warning("Datumförslag nekades: förhandsgranskningens bekräftelse saknas, är ogiltig eller har gått ut. booking_id=%s.", booking_id)
        return _detail_response(booking, "Förhandsgranska förslaget igen innan du skickar det.", values=values)
    if request.form.get("confirmed") != "1" or confirmed != _confirmation_data(booking, values):
        current_app.logger.warning("Datumförslag nekades: bekräftelsen stämmer inte med aktuella uppgifter. booking_id=%s.", booking_id)
        return _detail_response(booking, "Uppgifterna har ändrats. Förhandsgranska förslaget igen.", values=values)
    proposal = database.create_proposal(
        booking_id, values["proposed_date"], values["proposed_city"], values["proposed_venue"],
        current_app.config["DB_PATH"], actor_id=session["user_id"],
        expected_status=confirmed["status"],
        expected_previous_proposal_id=confirmed["previous_proposal_id"],
    )
    if proposal is None:
        current_app.logger.warning("Datumförslag nekades: bokningen har redan behandlats. booking_id=%s.", booking_id)
        return _detail_response(_get_booking(booking_id), "Bokningen har redan behandlats. Kontrollera det senaste förslaget.", 409)
    clear_booking_csrf_token()
    current_app.logger.info("Datumförslag skapat: booking_id=%s proposal_id=%s.", booking_id, proposal["id"])
    _deliver_proposal(booking, proposal)
    return redirect(url_for("admin_page.booking_detail", booking_id=booking_id))


@admin_page.route("/bookings/<int:booking_id>/proposals/<int:proposal_id>/retry", methods=["POST"])
@admin_required
def retry_delivery(booking_id, proposal_id):
    _check_csrf()
    booking = _get_booking(booking_id)
    proposal = next((p for p in database.get_booking_proposals(booking_id, current_app.config["DB_PATH"])
                     if p["id"] == proposal_id), None)
    if proposal is None:
        current_app.logger.warning("Nytt leveransförsök nekades: datumförslaget saknas. booking_id=%s proposal_id=%s.", booking_id, proposal_id)
        abort(404)
    if booking["status"] != "awaiting_response" or proposal["state"] != "pending":
        current_app.logger.warning("Nytt leveransförsök nekades: datumförslaget är inte längre aktuellt. booking_id=%s proposal_id=%s.", booking_id, proposal_id)
        return _detail_response(booking, "Förslaget är inte längre aktuellt och ska inte skickas igen.", 409)
    current_app.logger.info("Nytt e-postleveransförsök begärt: booking_id=%s proposal_id=%s.", booking_id, proposal_id)
    _deliver_proposal(booking, proposal)
    clear_booking_csrf_token()
    return redirect(url_for("admin_page.booking_detail", booking_id=booking_id))


@admin_page.route("/login", methods=["GET", "POST"])
def login():
    return redirect(url_for("user_page.login"), code=307 if request.method == "POST" else 302)


@admin_page.route("/booking", methods=["GET", "POST"])
@admin_required
def booking():
    return redirect(url_for("user_page.booking"))
