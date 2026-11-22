from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import logging
import hashlib
import secrets
import sqlite3
import time

import psycopg
from password_security import hash_password, is_password_hash, verify_password


logger = logging.getLogger(__name__)
_WRITE_OPERATIONS = frozenset({
    "create_booking", "create_booking_request", "create_user", "remove_booking",
    "ensure_test_user", "ensure_dev_users", "ensure_dev_bookings", "ensure_dev_booking_history",
    "ensure_dev_workflow_bookings", "create_proposal",
    "respond_to_proposal", "transition_booking", "add_booking_note",
    "claim_proposal_delivery", "finish_proposal_delivery", "create_database",
    "issue_email_verification", "verify_email_token", "revoke_email_verification",
    "create_login_session", "revoke_login_session",
})


BOOKING_STATUSES = {
    "pending": "Ny förfrågan",
    "registered": "Registrerad",
    "awaiting_response": "Inväntar kundens svar",
    "new_date_requested": "Nytt datum önskas",
    "accepted": "Accepterad",
    "rescheduled": "Ombokad",
    "completed": "Slutförd",
    "cancelled": "Avbruten",
}
CUSTOMER_CANCELLABLE = frozenset({
    "pending", "registered", "awaiting_response", "new_date_requested",
})
ADMIN_TRANSITIONS = {
    "pending": frozenset({"registered"}),
    "awaiting_response": frozenset({"cancelled"}),
    "accepted": frozenset({"completed", "rescheduled", "cancelled"}),
    "rescheduled": frozenset({"completed", "cancelled"}),
}


_LEGACY_BOOKING_SELECT = """
    id,
    name,
    email,
    organization_number,
    utbildning,
    antal,
    COALESCE(faststalld_ort, onskad_ort, ort) AS ort,
    COALESCE(faststalld_lokal, lokal) AS lokal,
    COALESCE(faststallt_datum, datum) AS datum,
    status
"""

_BOOKING_DETAIL_COLUMNS = (
    "id",
    "name",
    "email",
    "organization_number",
    "utbildning",
    "antal",
    "ort",
    "lokal",
    "datum",
    "status",
    "datumtyp",
    "onskat_datum",
    "onskat_startdatum",
    "onskat_slutdatum",
    "utbildningskalla",
    "onskad_ort",
    "lokalansvar",
    "lokaladress",
    "faststallt_datum",
    "faststalld_ort",
    "faststalld_lokal",
)

_BOOKING_MIGRATION_COLUMNS = _BOOKING_DETAIL_COLUMNS[10:]


def database_is_ready(db_path):
    if not db_path:
        return False

    try:
        if isinstance(db_path, Path):
            uri = f"{db_path.absolute().as_uri()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=1)) as conn:
                conn.execute("PRAGMA schema_version").fetchone()
        else:
            with psycopg.connect(
                db_path, connect_timeout=2, options="-c statement_timeout=2000", autocommit=True,
            ) as conn:
                conn.execute("SELECT 1")
    except (psycopg.Error, sqlite3.Error) as exc:
        logger.warning("Databasens beredskapskontroll misslyckades; feltyp=%s.", type(exc).__name__)
        return False

    return True


@contextmanager
def _cursor(db_path, operation="unspecified"):
    # A Path selects the local SQLite file. Production passes a PostgreSQL DSN.
    backend = "sqlite" if isinstance(db_path, Path) else "postgresql"
    logger.debug("Databastransaktion startar; operation=%s backend=%s.", operation, backend)
    try:
        if isinstance(db_path, Path):
            with (
                closing(sqlite3.connect(db_path, autocommit=True, timeout=15)) as conn,
            ):
                # Foreign keys must be enabled before opening the transaction.
                conn.execute("PRAGMA foreign_keys = ON")
                conn.autocommit = False
                with conn, closing(conn.cursor()) as cursor:
                    yield cursor
        else:
            with psycopg.connect(db_path) as conn, conn.cursor() as cursor:
                yield cursor
    except Exception as exc:
        # Database errors may include credentials, SQL values and personal data.
        logger.error("Databastransaktion misslyckades; operation=%s backend=%s feltyp=%s.",
                     operation, backend, type(exc).__name__)
        raise
    else:
        log = logger.info if operation in _WRITE_OPERATIONS else logger.debug
        log("Databastransaktion slutförd; operation=%s backend=%s.", operation, backend)


def _execute(cursor, query, params=()):
    if isinstance(cursor, sqlite3.Cursor):
        query = query.replace("%s", "?")
    # Log only the statement category, never SQL text or its bound values.
    statement = query.lstrip().split(None, 1)[0].upper()
    if statement not in {"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"}:
        statement = "OTHER"
    logger.debug("Databasfråga startar; typ=%s.", statement)
    result = cursor.execute(query, params)
    logger.debug("Databasfråga slutförd; typ=%s antal_rader=%s.", statement, cursor.rowcount)
    return result


def generate_secret_key():
    return secrets.token_hex(32)


def _insert_booking(cursor, columns, values):
    placeholders = ", ".join(["%s"] * len(columns))
    _execute(
        cursor,
        f"INSERT INTO bookings ({', '.join(columns)}) "
        f"VALUES ({placeholders}) RETURNING id",
        values,
    )
    booking_id = cursor.fetchone()[0]
    fields = dict(zip(columns, values))
    status = fields.get("status", "pending")
    _execute(cursor, "SELECT id, role FROM users WHERE email = %s", (fields.get("email"),))
    user = cursor.fetchone()
    _record_history(
        cursor, booking_id, None, status,
        ("admin" if user and user[1] == "admin" else "customer") if status == "pending" else "system",
        "Bokningsförfrågan skapad." if status == "pending" else "Bokning skapad.",
        user[0] if user and status == "pending" else None,
    )
    return booking_id


def create_booking(
    name, email, organization_number, utbildning, antal, ort, lokal, datum, db_path
):
    """Create a legacy exact-date request while preserving its original wishes."""
    with _cursor(db_path, "create_booking") as cursor:
        return _insert_booking(
            cursor,
            (
                "name",
                "email",
                "organization_number",
                "utbildning",
                "antal",
                "ort",
                "lokal",
                "datum",
                "datumtyp",
                "onskat_datum",
                "onskad_ort",
            ),
            (
                name,
                email,
                organization_number,
                utbildning,
                antal,
                ort,
                lokal,
                datum,
                "exakt",
                datum,
                ort.strip() if isinstance(ort, str) and ort.strip() else None,
            ),
        )


def create_booking_request(
    *,
    name,
    email,
    organization_number,
    utbildning,
    antal,
    datumtyp,
    onskat_datum=None,
    onskat_startdatum=None,
    onskat_slutdatum=None,
    utbildningskalla,
    onskad_ort=None,
    lokalansvar,
    lokaladress=None,
    db_path,
):
    """Persist a validated booking request with structured original wishes."""
    if datumtyp == "exakt":
        legacy_datum = onskat_datum
        onskat_startdatum = None
        onskat_slutdatum = None
    elif datumtyp == "intervall":
        legacy_datum = f"{onskat_startdatum} – {onskat_slutdatum}"
        onskat_datum = None
    else:
        logger.warning("Bokningsförfrågan avvisades; orsak=ogiltig_datumtyp.")
        raise ValueError("Okänd datumtyp")

    if utbildningskalla not in {"katalog", "fritext"}:
        logger.warning("Bokningsförfrågan avvisades; orsak=ogiltig_utbildningskälla.")
        raise ValueError("Okänd utbildningskälla")

    if lokalansvar == "kund":
        legacy_lokal = lokaladress
    elif lokalansvar == "sakerhetsutbildarna":
        legacy_lokal = "Säkerhetsutbildarna ordnar lokalen"
        lokaladress = None
    else:
        logger.warning("Bokningsförfrågan avvisades; orsak=ogiltigt_lokalansvar.")
        raise ValueError("Okänt lokalansvar")

    normalized_ort = (
        onskad_ort.strip()
        if isinstance(onskad_ort, str) and onskad_ort.strip()
        else None
    )

    with _cursor(db_path, "create_booking_request") as cursor:
        return _insert_booking(
            cursor,
            (
                "name",
                "email",
                "organization_number",
                "utbildning",
                "antal",
                "ort",
                "lokal",
                "datum",
                "datumtyp",
                "onskat_datum",
                "onskat_startdatum",
                "onskat_slutdatum",
                "utbildningskalla",
                "onskad_ort",
                "lokalansvar",
                "lokaladress",
            ),
            (
                name,
                email,
                organization_number,
                utbildning,
                antal,
                normalized_ort,
                legacy_lokal,
                legacy_datum,
                datumtyp,
                onskat_datum,
                onskat_startdatum,
                onskat_slutdatum,
                utbildningskalla,
                normalized_ort,
                lokalansvar,
                lokaladress,
            ),
        )


def check_user_exists(email, db_path):
    with _cursor(db_path, "check_user_exists") as cursor:
        _execute(cursor, "SELECT id FROM users WHERE LOWER(email) = LOWER(%s)", (email,))
        return cursor.fetchone() is not None


def create_user(name, email, password, organization_number, role, db_path, *, email_verified=True):
    stored_password = hash_password(password)
    with _cursor(db_path, "create_user") as cursor:
        _execute(
            cursor,
            "INSERT INTO users (name, email, password, organization_number, role, email_verified) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "RETURNING id, name, organization_number, email",
            (name, email, stored_password, organization_number, role, int(email_verified)),
        )
        return cursor.fetchone()


def update_status(booking_id, status, db_path):
    """Compatibility entry point; never bypass the defined admin transitions."""
    return transition_booking(booking_id, status, db_path)


def cancel_booking(booking_id, db_path):
    """Compatibility entry point for the allowed administrator cancellation."""
    return int(transition_booking(booking_id, "cancelled", db_path))


def cancel_booking_for_email(booking_id, email, db_path):
    """Cancel only the owner's booking in a customer-cancellable state."""
    return int(transition_booking(
        booking_id, "cancelled", db_path, actor="customer", email=email,
    ))


def get_email_by_booking_id(booking_id, db_path):
    with _cursor(db_path, "get_email_by_booking_id") as cursor:
        _execute(cursor, "SELECT email FROM bookings WHERE id = %s", (booking_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def book_bookings(booking_ids, booking_date, ort, db_path):
    """Retired: only the customer's response may confirm a booking."""
    if not booking_ids:
        logger.debug("Äldre bokningsfunktion anropades utan valda bokningar.")
        return []
    logger.warning("Direkt bekräftelse via den äldre bokningsfunktionen avvisades.")
    raise ValueError("Skicka ett datumförslag som kunden får godkänna.")


def remove_booking(booking_id, db_path):
    with _cursor(db_path, "remove_booking") as cursor:
        _execute(cursor, "DELETE FROM bookings WHERE id = %s", (booking_id,))


def get_utbildning_by_booking_id(booking_id, db_path):
    with _cursor(db_path, "get_utbildning_by_booking_id") as cursor:
        _execute(cursor, "SELECT utbildning FROM bookings WHERE id = %s", (booking_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def get_booking_by_id(booking_id, db_path):
    with _cursor(db_path, "get_booking_by_id") as cursor:
        _execute(
            cursor,
            f"SELECT {_LEGACY_BOOKING_SELECT} FROM bookings WHERE id = %s",
            (booking_id,),
        )
        return cursor.fetchone()


def get_booking_details_by_id(booking_id, db_path):
    """Return raw legacy, original-request and confirmed booking fields by name."""
    with _cursor(db_path, "get_booking_details_by_id") as cursor:
        _execute(
            cursor,
            f"SELECT {', '.join(_BOOKING_DETAIL_COLUMNS)} "
            "FROM bookings WHERE id = %s",
            (booking_id,),
        )
        row = cursor.fetchone()
        return dict(zip(_BOOKING_DETAIL_COLUMNS, row)) if row else None


def get_bookings_by_email(email, db_path):
    with _cursor(db_path, "get_bookings_by_email") as cursor:
        _execute(
            cursor,
            f"SELECT {_LEGACY_BOOKING_SELECT} FROM bookings WHERE email = %s",
            (email,),
        )
        rows = cursor.fetchall()
        return rows if rows else None


def get_bookings_by_status(status, db_path):
    with _cursor(db_path, "get_bookings_by_status") as cursor:
        _execute(
            cursor,
            f"SELECT {_LEGACY_BOOKING_SELECT} FROM bookings WHERE status = %s",
            (status,),
        )
        rows = cursor.fetchall()
        return rows if rows else None


def ensure_test_user(email, password, db_path):
    stored_password = hash_password(password)
    with _cursor(db_path, "ensure_test_user") as cursor:
        _execute(
            cursor,
            "INSERT INTO users (name, email, password, organization_number, role) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
            ("Admin", email, stored_password, "0000000000", "admin"),
        )


def ensure_dev_users(admin_email, admin_password, user_email, user_password, db_path):
    """Create development accounts without changing existing account details."""
    admin_email = admin_email.strip()
    user_email = user_email.strip()
    if admin_email.casefold() == user_email.casefold():
        logger.warning("Utvecklingskonton avvisades; orsak=samma_e-postadress.")
        raise ValueError("Utvecklingskontona måste ha olika e-postadresser.")

    accounts = (
        ("Utvecklingsadministratör", admin_email, admin_password, "admin"),
        ("Utvecklingsanvändare", user_email, user_password, "user"),
    )
    with _cursor(db_path, "ensure_dev_users") as cursor:
        for name, email, password, role in accounts:
            _execute(
                cursor,
                "INSERT INTO users (name, email, password, organization_number, role) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
                (name, email, hash_password(password), "0000000000", role),
            )


def ensure_dev_bookings(user_email, db_path):
    """Create fictional bookings for real companies once per development customer."""
    with _cursor(db_path, "ensure_dev_bookings") as cursor:
        _execute(
            cursor,
            "SELECT id, name FROM users "
            "WHERE email = %s AND role = 'user'",
            (user_email,),
        )
        user = cursor.fetchone()
        if user is None:
            logger.debug("Exempelbokningar hoppades över; utvecklingskund saknas.")
            return []

        # This table is only created in development mode. The marker and all
        # bookings share one transaction, including when startup is retried.
        _execute(
            cursor,
            "CREATE TABLE IF NOT EXISTS dev_booking_seeds ("
            "user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE)",
        )
        _execute(
            cursor,
            "INSERT INTO dev_booking_seeds (user_id) VALUES (%s) "
            "ON CONFLICT (user_id) DO NOTHING RETURNING user_id",
            (user[0],),
        )
        if cursor.fetchone() is None:
            logger.debug("Exempelbokningar finns redan; inga nya skapades.")
            return []

        today = date.today()

        def future_date(days):
            return (today + timedelta(days=days)).isoformat()

        samples = (
            {
                "organization_number": "5560747551",  # IKEA of Sweden AB
                "utbildning": "HLR", "utbildningskalla": "katalog", "antal": 8,
                "datumtyp": "exakt", "onskat_datum": future_date(30),
                "onskad_ort": "Stockholm", "lokalansvar": "kund",
                "lokaladress": "Exempelgatan 1, Stockholm", "status": "pending",
            },
            {
                "organization_number": "5560125790",  # Aktiebolaget Volvo
                "utbildning": "Exempel: Anpassad säkerhetsutbildning",
                "utbildningskalla": "fritext", "antal": 12,
                "datumtyp": "intervall", "onskat_startdatum": future_date(45),
                "onskat_slutdatum": future_date(49), "status": "pending",
            },
            {
                "organization_number": "5560160680",  # Telefonaktiebolaget LM Ericsson
                "utbildning": "Heta arbeten", "utbildningskalla": "katalog", "antal": 6,
                "datumtyp": "intervall", "onskat_startdatum": future_date(14),
                "onskat_slutdatum": future_date(18), "onskad_ort": "Göteborg",
                "faststallt_datum": future_date(16), "faststalld_ort": "Göteborg",
                "status": "accepted",
            },
            {
                "organization_number": "5560427220",  # H & M Hennes & Mauritz AB
                "utbildning": "Första hjälpen", "utbildningskalla": "katalog", "antal": 10,
                "datumtyp": "exakt", "onskat_datum": future_date(60),
                "onskad_ort": "Malmö", "status": "cancelled",
            },
        )
        booking_ids = []
        for sample in samples:
            booking = dict.fromkeys(_BOOKING_DETAIL_COLUMNS[1:])
            booking.update(
                name=user[1], email=user_email,
                lokalansvar="sakerhetsutbildarna",
            )
            booking.update(sample)
            booking["datum"] = (
                booking["onskat_datum"] if booking["datumtyp"] == "exakt"
                else f"{booking['onskat_startdatum']} – {booking['onskat_slutdatum']}"
            )
            booking["ort"] = booking["onskad_ort"]
            booking["lokal"] = (
                booking["lokaladress"] if booking["lokalansvar"] == "kund"
                else "Säkerhetsutbildarna ordnar lokalen"
            )
            booking_ids.append(_insert_booking(cursor, tuple(booking), tuple(booking.values())))
        logger.debug("Exempelbokningar förberedda; antal=%s.", len(booking_ids))
        return booking_ids


def ensure_dev_workflow_bookings(user_email, admin_email, db_path):
    """Seed interactive workflow examples once, called only by DEV_MODE startup.

    Keep each scenario, its proposals, history and marker in one transaction.
    Existing bookings and earlier examples are never rewritten. No mail or
    external services are invoked; even the failed delivery is simulated.
    """
    scenarios = (
        ("registered", "Registrera och förbered datumförslag", "registered",
         "Administratör: öppna Skicka datumförslag, fyll i datum, ort och lokal och välj Granska förslaget."),
        ("accept_proposal", "Acceptera datumförslag", "awaiting_response",
         "Kund: välj Acceptera förslaget. Datum, ort och lokal blir fastställda och status blir Accepterad."),
        ("request_new_date", "Begär nytt datum", "awaiting_response",
         "Kund: välj Begär nytt datum. Administratören kan sedan skicka ett nytt förslag inom samma bokningsnummer."),
        ("cancel_proposal", "Avbryt datumförslag", "awaiting_response",
         "Kund: välj Avbryt bokningen. Förslaget avaktiveras och bokningen finns kvar som Avbruten."),
        ("failed_delivery", "Försök skicka e-post igen", "awaiting_response",
         "Administratör: ett misslyckat utskick är simulerat. Välj Försök skicka e-post igen. "
         "Sätt send_mail=0 före testet för att hålla utskick avstängda. Kunden kan redan svara i portalen."),
        ("new_date_requested", "Nytt datum önskas", "new_date_requested",
         "Administratör: granska kundens tidigare svar och skicka ett nytt datumförslag. Det avböjda förslaget bevaras."),
        ("second_proposal", "Flera datumförslag", "awaiting_response",
         "Kund: jämför det avböjda förslaget med det aktuella under Tidigare datumförslag och kundsvar. "
         "Bara det aktuella förslaget kan besvaras."),
        ("accepted", "Bekräftad utbildning", "accepted",
         "Administratör: prova Markera som ombokad, slutförd eller avbruten. "
         "Jämför ursprungligt önskemål med kundens accepterade datum, ort och lokal."),
        ("rescheduled", "Ombokad utbildning", "rescheduled",
         "Administratör: bokningen kan slutföras eller avbrytas. Ombokad är en statusmarkering; "
         "senast fastställda datum, ort och lokal behålls."),
        ("completed", "Slutförd utbildning", "completed",
         "Kund och administratör: en genomförd utbildning med passerat datum visas med fastställda uppgifter "
         "och historik. Inga fler statusåtgärder erbjuds."),
        ("cancelled_customer", "Avbruten av kunden", "cancelled",
         "Kund och administratör: förslaget är avaktiverat och kundens svar Avbryt bokningen finns i historiken. "
         "Bokningen kan inte accepteras igen."),
        ("cancelled_admin", "Avbruten av administratören", "cancelled",
         "Kund och administratör: administratören har avbrutit bokningen. Förslaget är avaktiverat "
         "utan ett påhittat kundsvar."),
    )
    with _cursor(db_path, "ensure_dev_workflow_bookings") as cursor:
        # Reserve this customer before reading markers, including on SQLite.
        _execute(cursor, "UPDATE users SET name = name WHERE email = %s AND role = 'user' RETURNING id, name",
                 (user_email,))
        customer = cursor.fetchone()
        _execute(cursor, "SELECT id FROM users WHERE email = %s AND role = 'admin'", (admin_email,))
        admin = cursor.fetchone()
        if customer is None or admin is None:
            logger.debug("Exempelflöden hoppades över; utvecklingskund eller administratör saknas.")
            return []
        _execute(cursor, "CREATE TABLE IF NOT EXISTS dev_booking_scenario_seeds ("
                 "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                 "scenario TEXT NOT NULL, "
                 "booking_id INTEGER REFERENCES bookings(id) ON DELETE SET NULL, "
                 "PRIMARY KEY (user_id, scenario))")
        _execute(cursor, "CREATE TABLE IF NOT EXISTS dev_booking_history_seeds ("
                 "booking_id INTEGER PRIMARY KEY REFERENCES bookings(id) ON DELETE CASCADE)")

        booking_ids = []
        today = date.today()
        for index, (key, title, target, guide) in enumerate(scenarios):
            _execute(cursor, "INSERT INTO dev_booking_scenario_seeds (user_id, scenario) VALUES (%s, %s) "
                     "ON CONFLICT (user_id, scenario) DO NOTHING RETURNING scenario", (customer[0], key))
            if cursor.fetchone() is None:
                continue
            days = -21 if target == "completed" else 21 + index * 7
            start = (today + timedelta(days=days)).isoformat()
            end = (today + timedelta(days=days + 4)).isoformat()
            interval = index % 2 == 1
            own_venue = index % 4 == 0
            booking = dict.fromkeys(_BOOKING_DETAIL_COLUMNS[1:])
            booking.update(
                name=customer[1], email=user_email,
                organization_number=("5560747551", "5560125790", "5560160680", "5560427220")[index % 4],
                utbildning=f"Exempel: {title}", utbildningskalla="fritext", antal=(1, 4, 12, 24)[index % 4],
                datumtyp="intervall" if interval else "exakt",
                onskat_datum=None if interval else start,
                onskat_startdatum=start if interval else None,
                onskat_slutdatum=end if interval else None,
                onskad_ort=None if index % 3 == 0 else "Göteborg",
                lokalansvar="kund" if own_venue else "sakerhetsutbildarna",
                lokaladress="Exempelgatan 2, Göteborg" if own_venue else None,
                status="pending",
            )
            booking["datum"] = f"{start} – {end}" if interval else start
            booking["ort"] = booking["onskad_ort"]
            booking["lokal"] = (booking["lokaladress"] if own_venue
                                else "Säkerhetsutbildarna ordnar lokalen")
            booking_id = _insert_booking(cursor, tuple(booking), tuple(booking.values()))
            _execute(cursor, "UPDATE dev_booking_scenario_seeds SET booking_id = %s "
                     "WHERE user_id = %s AND scenario = %s", (booking_id, customer[0], key))
            _record_history(cursor, booking_id, "pending", "pending", "customer",
                            "Exempeldata: En fiktiv utbildningsförfrågan att öva på.", customer[0], kind="demo")

            def change_status(status, actor, note):
                _execute(cursor, "UPDATE bookings SET status = %s WHERE id = %s", (status, booking_id))
                _record_history(cursor, booking_id, booking["status"], status, actor,
                                f"Exempeldata: {note}", admin[0] if actor == "admin" else customer[0])
                booking["status"] = status

            def propose(offset, delivery_status="disabled"):
                proposed_date = (today + timedelta(days=days + offset)).isoformat()
                _execute(cursor, "INSERT INTO booking_proposals (booking_id, proposed_date, proposed_city, "
                         "proposed_venue, state, created_at, delivery_status) "
                         "VALUES (%s, %s, %s, %s, 'pending', %s, %s) RETURNING id",
                         (booking_id, proposed_date, "Uppsala", "Exempellokalen, Övningsgatan 3, Uppsala",
                          _now(), delivery_status))
                proposal_id = cursor.fetchone()[0]
                change_status("awaiting_response", "admin", "Datumförslag skapat. Inväntar kundens svar.")
                note = ("Ett misslyckat e-postutskick är simulerat. Inget mejl har skickats."
                        if delivery_status == "failed" else "Inget mejl har skickats. Förslaget finns i kundportalen.")
                _record_history(cursor, booking_id, None, None, "system", f"Exempeldata: {note}",
                                kind="delivery", is_private=True)
                return proposal_id, proposed_date

            def respond(proposal_id, action, proposed_date=None):
                state, status, note = {
                    "new_date": ("declined", "new_date_requested", "Kunden har begärt ett nytt datumförslag."),
                    "accept": ("accepted", "accepted", "Kunden har accepterat datumförslaget."),
                    "cancel": ("cancelled", "cancelled", "Kunden har avbrutit bokningen."),
                }[action]
                _execute(cursor, "UPDATE booking_proposals SET state = %s, response = %s, responded_at = %s "
                         "WHERE id = %s", (state, action, _now(), proposal_id))
                if action == "accept":
                    _execute(cursor, "UPDATE bookings SET faststallt_datum = %s, faststalld_ort = %s, "
                             "faststalld_lokal = %s WHERE id = %s",
                             (proposed_date, "Uppsala", "Exempellokalen, Övningsgatan 3, Uppsala", booking_id))
                change_status(status, "customer", note)

            change_status("registered", "admin", "Administratören har registrerat förfrågan.")
            if target != "registered":
                proposal_id, proposed_date = propose(2, "failed" if key == "failed_delivery" else "disabled")
                if key == "second_proposal":
                    respond(proposal_id, "new_date")
                    proposal_id, proposed_date = propose(7)
                elif target == "new_date_requested":
                    respond(proposal_id, "new_date")
                elif target in {"accepted", "rescheduled", "completed"}:
                    respond(proposal_id, "accept", proposed_date)
                    if target != "accepted":
                        change_status(target, "admin", "Administratören har markerat bokningen som "
                                      + ("ombokad." if target == "rescheduled" else "slutförd."))
                elif key == "cancelled_customer":
                    respond(proposal_id, "cancel")
                elif key == "cancelled_admin":
                    _execute(cursor, "UPDATE booking_proposals SET state = 'cancelled' WHERE id = %s", (proposal_id,))
                    change_status("cancelled", "admin", "Administratören har avbrutit bokningen.")

            _record_history(cursor, booking_id, target, target, "admin",
                            "Exempeldata: Intern övningsanteckning. Kontrollera lokal och deltagarantal; "
                            "den här anteckningen visas bara för administratörer.",
                            admin[0], kind="note", is_private=True)
            _record_history(cursor, booking_id, target, target, "system",
                            f"Exempeldata – prova själv: {guide}", kind="demo")
            # These examples already have complete, explicitly fictional history.
            # Do not append the generic startup history on the next restart.
            _execute(cursor, "INSERT INTO dev_booking_history_seeds (booking_id) VALUES (%s)", (booking_id,))
            booking_ids.append(booking_id)
        logger.debug("Exempelflöden förberedda; antal=%s.", len(booking_ids))
        return booking_ids


def ensure_dev_booking_history(admin_email, db_path):
    """Append clearly marked demo events once per booking, only from dev startup."""
    status_events = {
        "pending": ("customer", "Förfrågan har skickats in och inväntar handläggning."),
        "registered": ("admin", "Administratören har registrerat förfrågan."),
        "awaiting_response": ("admin", "Ett datumförslag inväntar kundens svar."),
        "new_date_requested": ("customer", "Kunden har begärt ett nytt datumförslag."),
        "accepted": ("customer", "Kunden har accepterat datumförslaget. Utbildningen är bekräftad."),
        "rescheduled": ("admin", "Administratören har markerat bokningen som ombokad."),
        "completed": ("admin", "Administratören har markerat utbildningen som slutförd."),
        "cancelled": ("admin", "Administratören har avbrutit bokningen."),
    }
    with _cursor(db_path, "ensure_dev_booking_history") as cursor:
        # Reserve the development administrator before any reads, also avoiding
        # read-to-write upgrade races between simultaneous SQLite startups.
        _execute(cursor, "UPDATE users SET name = name WHERE email = %s AND role = 'admin' RETURNING id",
                 (admin_email,))
        admin = cursor.fetchone()
        if admin is None:
            logger.debug("Exempelhistorik hoppades över; utvecklingsadministratör saknas.")
            return []
        _execute(cursor, "CREATE TABLE IF NOT EXISTS dev_booking_history_seeds ("
                 "booking_id INTEGER PRIMARY KEY REFERENCES bookings(id) ON DELETE CASCADE)")
        _execute(cursor, "SELECT b.id FROM bookings b LEFT JOIN dev_booking_history_seeds s "
                 "ON s.booking_id = b.id WHERE s.booking_id IS NULL ORDER BY b.id")
        booking_ids = [row[0] for row in cursor.fetchall()]
        seeded_ids = []
        for booking_id in booking_ids:
            booking = _lock_booking(cursor, booking_id)
            if booking is None:
                continue
            _execute(cursor, "INSERT INTO dev_booking_history_seeds (booking_id) VALUES (%s) "
                     "ON CONFLICT (booking_id) DO NOTHING RETURNING booking_id", (booking_id,))
            if cursor.fetchone() is None:
                continue
            status = booking["status"]
            _record_history(cursor, booking_id, status, status, "admin",
                            "Exempeldata: Administratören har granskat förfrågan.", admin[0], kind="demo")
            actor, note = status_events.get(status, ("admin", "Bokningens aktuella status har granskats."))
            actor_id = admin[0]
            if actor == "customer":
                _execute(cursor, "SELECT id FROM users WHERE email = %s", (booking["email"],))
                customer = cursor.fetchone()
                actor_id = customer[0] if customer else None
                if customer is None:
                    actor = "system"
            # These are sample descriptions of the current state, not real
            # transitions. Never change booking fields, proposals or prior logs.
            _record_history(cursor, booking_id, status, status, actor,
                            f"Exempeldata: {note}", actor_id, kind="demo")
            _record_history(cursor, booking_id, status, status, "admin",
                            "Exempeldata: Stäm av lokal och praktiska detaljer med kunden inför utbildningen.",
                            admin[0], kind="note", is_private=True)
            seeded_ids.append(booking_id)
        logger.debug("Exempelhistorik förberedd; antal_bokningar=%s.", len(seeded_ids))
        return seeded_ids


def _password_identity(email, password, db_path, *, require_verified):
    """Verify credentials and upgrade a legacy password without returning it."""
    with _cursor(db_path, "authenticate_user") as cursor:
        _execute(
            cursor,
            "SELECT id, name, organization_number, email, role, password, email_verified FROM users "
            "WHERE LOWER(email) = LOWER(%s) "
            "ORDER BY CASE WHEN email = %s THEN 0 ELSE 1 END, id LIMIT 1",
            (email, email),
        )
        user = cursor.fetchone()
        if user is None or (require_verified and not user[6]):
            logger.debug("Autentisering misslyckades.")
            return None
        valid, needs_upgrade = verify_password(user[5], password)
        if not valid:
            logger.debug("Autentisering misslyckades.")
            return None
        if needs_upgrade:
            # Compare the old value so a concurrent password change wins.
            _execute(cursor, "UPDATE users SET password = %s WHERE id = %s AND password = %s",
                     (hash_password(password), user[0], user[5]))
            if cursor.rowcount != 1:
                return None
    logger.debug("Autentisering slutförd; godkänd=true.")
    return user[:5]


def authenticate_user(email, password, db_path):
    """Only verified accounts may open an authenticated session."""
    return _password_identity(email, password, db_path, require_verified=True)


def issue_email_verification(email, password, db_path):
    """Create a single-use 24-hour link; limit sends to one per minute/account."""
    user = _password_identity(email, password, db_path, require_verified=False)
    if user is None:
        return None
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = int(time.time())
    with _cursor(db_path, "issue_email_verification") as cursor:
        # Share the account lock with verification. A no-op UPDATE also obtains
        # SQLite's write lock before reading/rotating the token.
        _execute(cursor, "UPDATE users SET email_verified = email_verified "
                 "WHERE id = %s AND email_verified = 0 RETURNING id", (user[0],))
        if cursor.fetchone() is None:
            return None
        _execute(cursor,
                 "INSERT INTO user_email_verifications (user_id, token_hash, expires_at, sent_at) "
                 "SELECT id, %s, %s, %s FROM users WHERE id = %s AND email_verified = 0 "
                 "ON CONFLICT (user_id) DO UPDATE SET token_hash = excluded.token_hash, "
                 "expires_at = excluded.expires_at, sent_at = excluded.sent_at "
                 "WHERE user_email_verifications.sent_at <= %s RETURNING user_id",
                 (digest, now + 86400, now, user[0], now - 60))
        issued = cursor.fetchone()
    return token if issued else None


def _token_digest(token):
    if not isinstance(token, str) or len(token) != 43 or not token.isascii():
        return None
    if not all(char.isalnum() or char in "_-" for char in token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def get_email_verification(token, db_path):
    """Check a link without consuming it (mail scanners may follow GET links)."""
    digest = _token_digest(token)
    if digest is None:
        return False
    with _cursor(db_path, "get_email_verification") as cursor:
        _execute(cursor,
                 "SELECT v.user_id FROM user_email_verifications v "
                 "JOIN users u ON u.id = v.user_id "
                 "WHERE v.token_hash = %s AND v.expires_at > %s AND u.email_verified = 0",
                 (digest, int(time.time())))
        return cursor.fetchone() is not None


def get_email_verification_recipient(token, db_path):
    """Return the account's stored address, never an address from the request."""
    digest = _token_digest(token)
    if digest is None:
        return None
    with _cursor(db_path, "get_email_verification_recipient") as cursor:
        _execute(cursor,
                 "SELECT u.email FROM user_email_verifications v "
                 "JOIN users u ON u.id = v.user_id "
                 "WHERE v.token_hash = %s AND v.expires_at > %s AND u.email_verified = 0",
                 (digest, int(time.time())))
        row = cursor.fetchone()
        return row[0] if row else None


def verify_email_token(token, db_path):
    """Atomically consume a valid link; never authenticate the browser."""
    digest = _token_digest(token)
    if digest is None:
        return False
    with _cursor(db_path, "verify_email_token") as cursor:
        # Lock the account before consuming the token, in the same order as
        # resends. Re-read the token after any concurrent rotation has committed.
        _execute(cursor,
                 "UPDATE users SET email_verified = email_verified WHERE email_verified = 0 AND id = "
                 "(SELECT user_id FROM user_email_verifications "
                 "WHERE token_hash = %s AND expires_at > %s) RETURNING id",
                 (digest, int(time.time())))
        user = cursor.fetchone()
        if user is None:
            return False
        _execute(cursor, "DELETE FROM user_email_verifications "
                 "WHERE user_id = %s AND token_hash = %s AND expires_at > %s RETURNING user_id",
                 (user[0], digest, int(time.time())))
        if cursor.fetchone() is None:
            return False
        _execute(cursor, "UPDATE users SET email_verified = 1 WHERE id = %s", (user[0],))
    return True


def revoke_email_verification(token, db_path):
    """Remove only this failed delivery so a later attempt can retry safely."""
    digest = _token_digest(token)
    if digest is None:
        return False
    with _cursor(db_path, "revoke_email_verification") as cursor:
        _execute(cursor, "DELETE FROM user_email_verifications WHERE token_hash = %s", (digest,))
        return cursor.rowcount == 1


def login_user(email, password, role, db_path):
    """Authenticate a specific role (kept for backwards compatibility)."""
    user = authenticate_user(email, password, db_path)
    if user is None or user[4] != role:
        logger.debug("Rollspecifik autentisering nekades.")
        return None
    return user[:3]


def create_login_session(user_id, db_path, *, now, expires_at, previous_token=None):
    """Open a browser session and retire its previous session in one transaction."""
    token = secrets.token_urlsafe(32)
    digest = _token_digest(token)
    previous_digest = _token_digest(previous_token)
    with _cursor(db_path, "create_login_session") as cursor:
        _execute(cursor, "DELETE FROM login_sessions WHERE expires_at <= %s", (now,))
        if previous_digest is not None:
            _execute(cursor, "DELETE FROM login_sessions WHERE token_hash = %s", (previous_digest,))
        _execute(cursor,
                 "INSERT INTO login_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                 (digest, user_id, expires_at))
    return token


def refresh_login_session(token, user_id, db_path, *, now, expires_at):
    """Extend an active session without recreating it after logout or expiry."""
    digest = _token_digest(token)
    if digest is None:
        return False
    with _cursor(db_path, "refresh_login_session") as cursor:
        # A request that arrived earlier cannot shorten a concurrently refreshed session.
        _execute(cursor,
                 "UPDATE login_sessions SET expires_at = "
                 "CASE WHEN expires_at < %s THEN %s ELSE expires_at END "
                 "WHERE token_hash = %s AND user_id = %s AND expires_at > %s",
                 (expires_at, expires_at, digest, user_id, now))
        return cursor.rowcount == 1


def revoke_login_session(token, db_path):
    """Retire just this browser session, including when logout is retried."""
    digest = _token_digest(token)
    if digest is None:
        return
    with _cursor(db_path, "revoke_login_session") as cursor:
        _execute(cursor, "DELETE FROM login_sessions WHERE token_hash = %s", (digest,))


def get_user_role(user_id, db_path):
    """Return the user's current role so access follows database changes."""
    with _cursor(db_path, "get_user_role") as cursor:
        _execute(cursor, "SELECT role FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def get_user_identity(user_id, db_path):
    """Read the current identity rather than authorizing a cached session email."""
    with _cursor(db_path, "get_user_identity") as cursor:
        _execute(cursor, "SELECT id, name, email, organization_number, role, email_verified "
                 "FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return dict(zip(("id", "name", "email", "organization_number", "role", "email_verified"), row)) if row else None


def get_booking_by_name(name, db_path):
    with _cursor(db_path, "get_booking_by_name") as cursor:
        _execute(
            cursor,
            f"SELECT {_LEGACY_BOOKING_SELECT} FROM bookings WHERE name = %s",
            (name,),
        )
        return cursor.fetchall()


def get_all_bookings(db_path):
    with _cursor(db_path, "get_all_bookings") as cursor:
        if isinstance(cursor, sqlite3.Cursor):
            # SQLite's built-in lower() only converts ASCII characters.
            cursor.connection.create_function(
                "lower", 1, lambda value: value.lower() if value is not None else None
            )
        _execute(
            cursor,
            f"SELECT {_LEGACY_BOOKING_SELECT} FROM bookings "
            "ORDER BY lower(utbildning) ASC NULLS LAST, id ASC",
        )
        return cursor.fetchall()


_PROPOSAL_COLUMNS = (
    "id", "booking_id", "proposed_date", "proposed_city", "proposed_venue",
    "state", "created_at", "sent_at", "responded_at", "response", "delivery_status",
)
_HISTORY_COLUMNS = (
    "id", "booking_id", "from_status", "to_status", "actor", "actor_id",
    "note", "created_at", "kind", "is_private", "actor_name",
)
_UNSET = object()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_history(cursor, booking_id, from_status, to_status, actor, note,
                    actor_id=None, *, kind="status", is_private=False):
    # Preserve the name at the time of the action, even if the account is later
    # renamed or removed. Read it from the database, never from form/session names.
    actor_name = None
    if actor_id is not None:
        _execute(cursor, "SELECT name FROM users WHERE id = %s", (actor_id,))
        user = cursor.fetchone()
        actor_name = user[0] if user else None
    _execute(
        cursor,
        "INSERT INTO booking_history (booking_id, from_status, to_status, actor, "
        "actor_id, note, created_at, kind, is_private, actor_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (booking_id, from_status, to_status, actor, actor_id, note, _now(), kind, int(is_private), actor_name),
    )


def list_booking_details(db_path, email=None, status=None):
    """List named request/confirmed fields and the latest visible history event."""
    columns = ", ".join(f"b.{name}" for name in _BOOKING_DETAIL_COLUMNS)
    visibility = " AND h.is_private = 0" if email is not None else ""
    query = (
        f"SELECT {columns}, "
        "(SELECT h.note FROM booking_history h WHERE h.booking_id = b.id"
        f"{visibility} ORDER BY h.id DESC LIMIT 1), "
        "(SELECT h.created_at FROM booking_history h WHERE h.booking_id = b.id"
        f"{visibility} ORDER BY h.id DESC LIMIT 1) FROM bookings b WHERE 1 = 1"
    )
    params = []
    if email is not None:
        query += " AND b.email = %s"
        params.append(email)
    if status is not None:
        query += " AND b.status = %s"
        params.append(status)
    query += (
        " ORDER BY CASE b.status WHEN 'new_date_requested' THEN 0 "
        "WHEN 'pending' THEN 1 WHEN 'registered' THEN 2 "
        "WHEN 'awaiting_response' THEN 3 WHEN 'accepted' THEN 4 "
        "WHEN 'rescheduled' THEN 5 ELSE 6 END, b.id DESC"
    )
    with _cursor(db_path, "list_booking_details") as cursor:
        _execute(cursor, query, params)
        return [dict(zip((*_BOOKING_DETAIL_COLUMNS, "latest_event", "latest_event_at"), row))
                for row in cursor.fetchall()]


def get_booking_proposals(booking_id, db_path):
    with _cursor(db_path, "get_booking_proposals") as cursor:
        _execute(cursor, f"SELECT {', '.join(_PROPOSAL_COLUMNS)} "
                 "FROM booking_proposals WHERE booking_id = %s ORDER BY id ASC", (booking_id,))
        return [dict(zip(_PROPOSAL_COLUMNS, row)) for row in cursor.fetchall()]


def get_booking_history(booking_id, db_path, include_private=True, *, include_actor_details=False):
    columns = ", ".join(
        "NULL AS actor_name" if name == "actor_name" and not include_actor_details else f"h.{name}"
        for name in _HISTORY_COLUMNS
    )
    if include_actor_details:
        columns += ", u.name AS actor_current_name"
    query = f"SELECT {columns} FROM booking_history h"
    if include_actor_details:
        query += " LEFT JOIN users u ON u.id = h.actor_id"
    query += " WHERE h.booking_id = %s"
    if not include_private:
        query += " AND h.is_private = 0"
    with _cursor(db_path, "get_booking_history") as cursor:
        _execute(cursor, query + " ORDER BY h.id ASC", (booking_id,))
        names = (*_HISTORY_COLUMNS, "actor_current_name") if include_actor_details else _HISTORY_COLUMNS
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def list_booking_audit_log(db_path, *, limit=51, offset=0):
    """Read a bounded page of booking actions, newest first, for administrators."""
    columns = ", ".join(f"h.{name}" for name in _HISTORY_COLUMNS)
    with _cursor(db_path, "list_booking_audit_log") as cursor:
        _execute(cursor, f"SELECT {columns}, u.name AS actor_current_name, b.utbildning "
                 "FROM booking_history h JOIN bookings b ON b.id = h.booking_id "
                 "LEFT JOIN users u ON u.id = h.actor_id "
                 "ORDER BY h.id DESC LIMIT %s OFFSET %s", (limit, offset))
        return [dict(zip((*_HISTORY_COLUMNS, "actor_current_name", "utbildning"), row))
                for row in cursor.fetchall()]


def _lock_booking(cursor, booking_id, email=None):
    # Make the first statement a write. PostgreSQL takes a row lock and SQLite
    # obtains its write reservation before any read snapshot is established.
    query = "UPDATE bookings SET status = status WHERE id = %s"
    params = (booking_id,)
    if email is not None:
        query += " AND email = %s"
        params += (email,)
    _execute(cursor, query + f" RETURNING {', '.join(_BOOKING_DETAIL_COLUMNS)}", params)
    row = cursor.fetchone()
    return dict(zip(_BOOKING_DETAIL_COLUMNS, row)) if row else None


def create_proposal(booking_id, proposed_date, proposed_city, proposed_venue, db_path, actor_id=None,
                    *, expected_status=None, expected_previous_proposal_id=_UNSET):
    """Preserve a new proposal and move an eligible request to awaiting_response."""
    try:
        parsed = date.fromisoformat(proposed_date)
    except (ValueError, TypeError):
        logger.warning("Datumförslag avvisades; orsak=ogiltigt_datum.")
        raise ValueError("Ange ett giltigt datum.") from None
    if parsed.isoformat() != proposed_date:
        logger.warning("Datumförslag avvisades; orsak=ogiltigt_datumformat.")
        raise ValueError("Ange ett giltigt datum.")
    if not isinstance(proposed_city, str) or not proposed_city.strip() or len(proposed_city) > 200:
        logger.warning("Datumförslag avvisades; orsak=ogiltig_ort.")
        raise ValueError("Ange en ort med högst 200 tecken.")
    if not isinstance(proposed_venue, str) or not proposed_venue.strip() or len(proposed_venue) > 1000:
        logger.warning("Datumförslag avvisades; orsak=ogiltig_lokal.")
        raise ValueError("Ange en lokal med högst 1 000 tecken.")
    with _cursor(db_path, "create_proposal") as cursor:
        booking = _lock_booking(cursor, booking_id)
        if not booking or booking["status"] not in {"pending", "registered", "new_date_requested"}:
            logger.warning("Datumförslag avvisades; bokning saknas eller status tillåter inte ändringen.")
            return None
        if expected_status is not None and booking["status"] != expected_status:
            logger.warning("Datumförslag avvisades; bokningens status har ändrats.")
            return None
        if expected_previous_proposal_id is not _UNSET:
            _execute(cursor, "SELECT id FROM booking_proposals WHERE booking_id = %s ORDER BY id DESC LIMIT 1", (booking_id,))
            latest = cursor.fetchone()
            if (latest[0] if latest else None) != expected_previous_proposal_id:
                logger.warning("Datumförslag avvisades; föregående förslag har ändrats.")
                return None
        _execute(cursor,
                 "INSERT INTO booking_proposals (booking_id, proposed_date, proposed_city, "
                 "proposed_venue, state, created_at, delivery_status) "
                 "VALUES (%s, %s, %s, %s, 'pending', %s, 'pending') "
                 f"RETURNING {', '.join(_PROPOSAL_COLUMNS)}",
                 (booking_id, proposed_date, proposed_city.strip(), proposed_venue.strip(), _now()))
        proposal = dict(zip(_PROPOSAL_COLUMNS, cursor.fetchone()))
        _execute(cursor, "UPDATE bookings SET status = 'awaiting_response' WHERE id = %s", (booking_id,))
        _record_history(cursor, booking_id, booking["status"], "awaiting_response", "admin",
                        "Datumförslag skapat. Inväntar kundens svar.", actor_id)
        return proposal


def respond_to_proposal(booking_id, proposal_id, email, action, db_path, actor_id=None):
    """Apply exactly one response to the current proposal, under the booking lock."""
    if action not in {"accept", "new_date", "cancel"} or not email:
        logger.warning("Svar på datumförslag avvisades; ogiltig åtgärd eller kund saknas.")
        return False
    with _cursor(db_path, "respond_to_proposal") as cursor:
        booking = _lock_booking(cursor, booking_id, email=email)
        if not booking or booking["status"] != "awaiting_response":
            logger.warning("Svar på datumförslag avvisades; ägarskap eller bokningsstatus tillåter inte åtgärden.")
            return False
        _execute(cursor, f"SELECT {', '.join(_PROPOSAL_COLUMNS)} FROM booking_proposals "
                 "WHERE id = %s AND booking_id = %s AND state = 'pending'",
                 (proposal_id, booking_id))
        row = cursor.fetchone()
        if row is None:
            logger.warning("Svar på datumförslag avvisades; aktuellt obesvarat förslag saknas.")
            return False
        proposal = dict(zip(_PROPOSAL_COLUMNS, row))
        target, proposal_state, note = {
            "accept": ("accepted", "accepted", "Kunden har accepterat datumförslaget. Utbildningen är bekräftad."),
            "new_date": ("new_date_requested", "declined", "Kunden har begärt ett nytt datumförslag."),
            "cancel": ("cancelled", "cancelled", "Kunden har avbrutit bokningen."),
        }[action]
        _execute(cursor, "UPDATE booking_proposals SET state = %s, response = %s, responded_at = %s "
                 "WHERE id = %s AND state = 'pending'", (proposal_state, action, _now(), proposal_id))
        if action == "accept":
            _execute(cursor, "UPDATE bookings SET status = %s, faststallt_datum = %s, "
                     "faststalld_ort = %s, faststalld_lokal = %s WHERE id = %s",
                     (target, proposal["proposed_date"], proposal["proposed_city"], proposal["proposed_venue"], booking_id))
        else:
            _execute(cursor, "UPDATE bookings SET status = %s WHERE id = %s", (target, booking_id))
        _record_history(cursor, booking_id, booking["status"], target, "customer", note, actor_id)
        return True


def transition_booking(booking_id, target, db_path, actor="admin", email=None, actor_id=None):
    """Enforce the role-specific status graph without allowing direct acceptance."""
    if actor not in {"admin", "customer"} or (actor == "customer" and not email):
        logger.warning("Statusändring avvisades; ogiltig aktör eller kund saknas.")
        return False
    with _cursor(db_path, "transition_booking") as cursor:
        booking = _lock_booking(cursor, booking_id, email=email if actor == "customer" else None)
        if booking is None:
            logger.warning("Statusändring avvisades; bokning saknas eller tillhör annan kund.")
            return False
        source = booking["status"]
        if actor == "customer":
            allowed = target == "cancelled" and source in CUSTOMER_CANCELLABLE
        else:
            allowed = target in ADMIN_TRANSITIONS.get(source, ())
        if not allowed:
            logger.warning("Statusändring avvisades; otillåten statusövergång.")
            return False
        _execute(cursor, "UPDATE bookings SET status = %s WHERE id = %s", (target, booking_id))
        if target == "cancelled":
            # Cancelling as an administrator deactivates the proposal without
            # fabricating a customer response or a customer response time.
            _execute(cursor, "UPDATE booking_proposals SET state = 'cancelled', response = %s, "
                     "responded_at = %s WHERE booking_id = %s AND state = 'pending'",
                     ("cancel" if actor == "customer" else None,
                      _now() if actor == "customer" else None, booking_id))
        note = {
            "registered": "Administratören har registrerat förfrågan.",
            "rescheduled": "Administratören har markerat bokningen som ombokad.",
            "completed": "Administratören har markerat utbildningen som slutförd.",
            "cancelled": "Kunden har avbrutit bokningen." if actor == "customer" else "Administratören har avbrutit bokningen.",
        }[target]
        _record_history(cursor, booking_id, source, target, actor, note, actor_id)
        return True


def add_booking_note(booking_id, note, db_path, actor_id=None):
    if not isinstance(note, str) or not note.strip() or len(note) > 5000:
        logger.warning("Bokningsanteckning avvisades; ogiltigt innehåll eller längd.")
        return False
    with _cursor(db_path, "add_booking_note") as cursor:
        booking = _lock_booking(cursor, booking_id)
        if booking is None:
            logger.warning("Bokningsanteckning avvisades; bokning saknas.")
            return False
        _record_history(cursor, booking_id, booking["status"], booking["status"], "admin",
                        note.strip(), actor_id, kind="note", is_private=True)
        return True


def claim_proposal_delivery(proposal_id, db_path, actor_id=None):
    """Only one request may send/retry a still-current unanswered proposal."""
    with _cursor(db_path, "claim_proposal_delivery") as cursor:
        _execute(cursor, "UPDATE booking_proposals SET delivery_status = 'sending' "
                 "WHERE id = %s AND state = 'pending' "
                 "AND delivery_status IN ('pending', 'failed', 'disabled') "
                 "AND EXISTS (SELECT 1 FROM bookings b WHERE b.id = booking_id AND b.status = 'awaiting_response') "
                 "RETURNING booking_id",
                 (proposal_id,))
        row = cursor.fetchone()
        if row is None:
            return False
        _record_history(cursor, row[0], None, None, "admin" if actor_id is not None else "system",
                        f"E-postutskick av datumförslag #{proposal_id} påbörjat.",
                        actor_id, kind="delivery", is_private=True)
        return True


def finish_proposal_delivery(proposal_id, delivery_status, db_path):
    if delivery_status not in {"sent", "failed", "disabled"}:
        logger.warning("Utskicksresultat avvisades; ogiltig leveransstatus.")
        raise ValueError("Okänt utskicksresultat.")
    with _cursor(db_path, "finish_proposal_delivery") as cursor:
        _execute(cursor, "UPDATE booking_proposals SET delivery_status = %s, sent_at = %s "
                 "WHERE id = %s AND delivery_status = 'sending' RETURNING booking_id",
                 (delivery_status, _now() if delivery_status == "sent" else None, proposal_id))
        row = cursor.fetchone()
        if row is None:
            logger.warning("Utskicksresultat sparades inte; inget pågående utskick finns.")
            return False
        note = {
            "sent": "Datumförslaget har skickats med e-post.",
            "failed": "E-postutskicket misslyckades. Förslaget finns i kundportalen.",
            "disabled": "E-post är avstängd. Förslaget finns i kundportalen.",
        }[delivery_status]
        _record_history(cursor, row[0], None, None, "system", note,
                        kind="delivery", is_private=delivery_status != "sent")
        return True


def _existing_booking_columns(cursor):
    if isinstance(cursor, sqlite3.Cursor):
        cursor.execute("PRAGMA table_info(bookings)")
        return {row[1] for row in cursor.fetchall()}

    _execute(
        cursor,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'bookings'",
    )
    return {row[0] for row in cursor.fetchall()}


def _migrate_bookings(cursor):
    existing_columns = _existing_booking_columns(cursor)
    for column in _BOOKING_MIGRATION_COLUMNS:
        if column not in existing_columns:
            # Column names come exclusively from the fixed migration allowlist above.
            _execute(cursor, f"ALTER TABLE bookings ADD COLUMN {column} TEXT")
            logger.debug("Databasmigrering förberedd; ny bokningskolumn=%s.", column)

    # A legacy pending row can only represent the old exact-date request form.
    # Accepted/cancelled rows may already contain administrator-confirmed values,
    # so their unknown original wishes deliberately remain NULL.
    _execute(
        cursor,
        "UPDATE bookings SET datumtyp = 'exakt' "
        "WHERE status = 'pending' AND datumtyp IS NULL AND datum IS NOT NULL",
    )
    _execute(
        cursor,
        "UPDATE bookings SET onskat_datum = datum "
        "WHERE status = 'pending' AND datumtyp = 'exakt' "
        "AND onskat_datum IS NULL AND datum IS NOT NULL",
    )
    _execute(
        cursor,
        "UPDATE bookings SET onskad_ort = NULLIF(TRIM(ort), '') "
        "WHERE status = 'pending' AND onskad_ort IS NULL AND ort IS NOT NULL",
    )

    # Older accepted bookings already represent confirmations. Preserve that
    # meaning and their saved details, without inventing a customer response or
    # guessing the original wishes of these older rows.
    _execute(cursor, "UPDATE bookings SET faststallt_datum = COALESCE(faststallt_datum, datum), "
             "faststalld_ort = COALESCE(faststalld_ort, ort), "
             "faststalld_lokal = COALESCE(faststalld_lokal, lokal) WHERE status = 'accepted'")


def _create_workflow_tables(cursor, id_column):
    _execute(cursor, f"""CREATE TABLE IF NOT EXISTS booking_proposals (
        id {id_column},
        booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
        proposed_date TEXT NOT NULL,
        proposed_city TEXT NOT NULL,
        proposed_venue TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending', 'accepted', 'declined', 'cancelled')),
        created_at TEXT NOT NULL,
        sent_at TEXT,
        responded_at TEXT,
        response TEXT CHECK (response IN ('accept', 'new_date', 'cancel')),
        delivery_status TEXT NOT NULL CHECK (delivery_status IN ('pending', 'sending', 'sent', 'failed', 'disabled'))
    )""")
    _execute(cursor, "CREATE UNIQUE INDEX IF NOT EXISTS booking_one_pending_proposal "
             "ON booking_proposals (booking_id) WHERE state = 'pending'")
    _execute(cursor, f"""CREATE TABLE IF NOT EXISTS booking_history (
        id {id_column},
        booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
        from_status TEXT,
        to_status TEXT,
        actor TEXT NOT NULL,
        actor_id INTEGER,
        actor_name TEXT,
        note TEXT NOT NULL,
        created_at TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'status',
        is_private INTEGER NOT NULL DEFAULT 0 CHECK (is_private IN (0, 1))
    )""")
    _migrate_booking_history(cursor)
    _execute(cursor, "CREATE INDEX IF NOT EXISTS booking_history_booking_id ON booking_history (booking_id, id)")
    # Baseline entries record the state observed at migration, not a fabricated
    # past transition. Existing IDs, statuses and customer accounts stay intact.
    _execute(cursor, "INSERT INTO booking_history (booking_id, from_status, to_status, actor, "
             "note, created_at, kind, is_private) "
             "SELECT b.id, NULL, b.status, 'system', "
             "'Befintlig bokning bevarad. Historiken börjar vid migreringen.', %s, 'baseline', 0 "
             "FROM bookings b WHERE NOT EXISTS (SELECT 1 FROM booking_history h WHERE h.booking_id = b.id)",
             (_now(),))


def _migrate_booking_history(cursor):
    """Add actor snapshots without rewriting historical events or guessing names."""
    if isinstance(cursor, sqlite3.Cursor):
        cursor.execute("PRAGMA table_info(booking_history)")
        columns = {row[1] for row in cursor.fetchall()}
    else:
        _execute(cursor, "SELECT column_name FROM information_schema.columns "
                 "WHERE table_schema = current_schema() AND table_name = 'booking_history'")
        columns = {row[0] for row in cursor.fetchall()}
    if "actor_name" not in columns:
        _execute(cursor, "ALTER TABLE booking_history ADD COLUMN actor_name TEXT")


def _migrate_users(cursor):
    """Preserve existing accounts and remove legacy plaintext without resets."""
    if isinstance(cursor, sqlite3.Cursor):
        cursor.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in cursor.fetchall()}
    else:
        _execute(cursor, "SELECT column_name FROM information_schema.columns "
                 "WHERE table_schema = current_schema() AND table_name = 'users'")
        columns = {row[0] for row in cursor.fetchall()}
    if "email_verified" not in columns:
        # Grandfather existing accounts; public signup explicitly stores 0.
        _execute(cursor, "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1 "
                 "CHECK (email_verified IN (0, 1))")
    _execute(cursor, "SELECT id, password FROM users")
    for user_id, stored in cursor.fetchall():
        if not is_password_hash(stored):
            _execute(cursor, "UPDATE users SET password = %s WHERE id = %s AND password = %s",
                     (hash_password(stored), user_id, stored))
    _execute(cursor, """CREATE TABLE IF NOT EXISTS user_email_verifications (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        expires_at INTEGER NOT NULL,
        sent_at INTEGER NOT NULL
    )""")


def create_database(db_path):
    if isinstance(db_path, Path):
        id_column = "INTEGER PRIMARY KEY AUTOINCREMENT"
    else:
        id_column = "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"

    with _cursor(db_path, "create_database") as cursor:
        if not isinstance(cursor, sqlite3.Cursor):
            # Serialize additive migrations across simultaneous application
            # startups without holding a session lock after commit.
            _execute(cursor, "SELECT pg_advisory_xact_lock(760821943)")
        _execute(
            cursor,
            f"""CREATE TABLE IF NOT EXISTS bookings (
                id {id_column},
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                organization_number TEXT,
                utbildning TEXT,
                antal INTEGER,
                ort TEXT,
                lokal TEXT,
                datum TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                datumtyp TEXT,
                onskat_datum TEXT,
                onskat_startdatum TEXT,
                onskat_slutdatum TEXT,
                utbildningskalla TEXT,
                onskad_ort TEXT,
                lokalansvar TEXT,
                lokaladress TEXT,
                faststallt_datum TEXT,
                faststalld_ort TEXT,
                faststalld_lokal TEXT
            )""",
        )
        _migrate_bookings(cursor)
        _create_workflow_tables(cursor, id_column)
        _execute(
            cursor,
            f"""CREATE TABLE IF NOT EXISTS users (
                id {id_column},
                name TEXT NOT NULL,
                organization_number TEXT,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                role TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                email_verified INTEGER NOT NULL DEFAULT 1 CHECK (email_verified IN (0, 1))
            )""",
        )
        _migrate_users(cursor)
        _execute(cursor, """CREATE TABLE IF NOT EXISTS login_sessions (
            token_hash TEXT PRIMARY KEY NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at BIGINT NOT NULL
        )""")
        _execute(cursor, "CREATE INDEX IF NOT EXISTS login_sessions_expiry_idx ON login_sessions (expires_at)")
