from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import logging
import secrets
import sqlite3

import psycopg
from werkzeug.security import check_password_hash, generate_password_hash


logger = logging.getLogger(__name__)
_WRITE_OPERATIONS = frozenset({
    "create_booking", "create_booking_request", "create_user", "remove_booking",
    "ensure_test_user", "ensure_dev_users", "ensure_dev_bookings", "create_proposal",
    "respond_to_proposal", "transition_booking", "add_booking_note",
    "claim_proposal_delivery", "finish_proposal_delivery", "create_database",
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
    _execute(cursor, "SELECT id FROM users WHERE email = %s", (fields.get("email"),))
    user = cursor.fetchone()
    _record_history(
        cursor, booking_id, None, status,
        "customer" if status == "pending" else "system",
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
        _execute(cursor, "SELECT id FROM users WHERE email = %s", (email,))
        return cursor.fetchone() is not None


def create_user(name, email, password, organization_number, role, db_path):
    with _cursor(db_path, "create_user") as cursor:
        _execute(
            cursor,
            "INSERT INTO users (name, email, password, organization_number, role) "
            "VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id, name, organization_number, email",
            (name, email, password, organization_number, role),
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
    with _cursor(db_path, "ensure_test_user") as cursor:
        _execute(
            cursor,
            "INSERT INTO users (name, email, password, organization_number, role) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
            ("Admin", email, password, "0000000000", "admin"),
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
                (name, email, generate_password_hash(password), "0000000000", role),
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


def authenticate_user(email, password, db_path):
    """Authenticate a user and return the database-backed account role."""
    with _cursor(db_path, "authenticate_user") as cursor:
        _execute(
            cursor,
            "SELECT id, name, organization_number, email, role, password FROM users "
            "WHERE email = %s",
            (email,),
        )
        user = cursor.fetchone()

    if user is None:
        logger.debug("Autentisering misslyckades.")
        return None
    stored_password = user[5]
    if (
        stored_password.startswith(("scrypt:", "pbkdf2:"))
        and stored_password.count("$") == 2
    ):
        try:
            valid = check_password_hash(stored_password, password)
        except ValueError:
            logger.warning("Autentisering misslyckades; ogiltigt lösenordshashformat.")
            valid = False
    else:
        # Existing accounts remain usable until a separate password migration.
        valid = secrets.compare_digest(
            stored_password.encode("utf-8"), password.encode("utf-8")
        )
    logger.debug("Autentisering slutförd; godkänd=%s.", valid)
    return user[:5] if valid else None


def login_user(email, password, role, db_path):
    """Authenticate a specific role (kept for backwards compatibility)."""
    user = authenticate_user(email, password, db_path)
    if user is None or user[4] != role:
        logger.debug("Rollspecifik autentisering nekades.")
        return None
    return user[:3]


def get_user_role(user_id, db_path):
    """Return the user's current role so access follows database changes."""
    with _cursor(db_path, "get_user_role") as cursor:
        _execute(cursor, "SELECT role FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def get_user_identity(user_id, db_path):
    """Read the current identity rather than authorizing a cached session email."""
    with _cursor(db_path, "get_user_identity") as cursor:
        _execute(cursor, "SELECT id, name, email, organization_number, role "
                 "FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return dict(zip(("id", "name", "email", "organization_number", "role"), row)) if row else None


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
    "note", "created_at", "kind", "is_private",
)
_UNSET = object()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_history(cursor, booking_id, from_status, to_status, actor, note,
                    actor_id=None, *, kind="status", is_private=False):
    _execute(
        cursor,
        "INSERT INTO booking_history (booking_id, from_status, to_status, actor, "
        "actor_id, note, created_at, kind, is_private) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (booking_id, from_status, to_status, actor, actor_id, note, _now(), kind, int(is_private)),
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


def get_booking_history(booking_id, db_path, include_private=True):
    query = f"SELECT {', '.join(_HISTORY_COLUMNS)} FROM booking_history WHERE booking_id = %s"
    if not include_private:
        query += " AND is_private = 0"
    with _cursor(db_path, "get_booking_history") as cursor:
        _execute(cursor, query + " ORDER BY id ASC", (booking_id,))
        return [dict(zip(_HISTORY_COLUMNS, row)) for row in cursor.fetchall()]


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


def claim_proposal_delivery(proposal_id, db_path):
    """Only one request may send/retry a still-current unanswered proposal."""
    with _cursor(db_path, "claim_proposal_delivery") as cursor:
        _execute(cursor, "UPDATE booking_proposals SET delivery_status = 'sending' "
                 "WHERE id = %s AND state = 'pending' "
                 "AND delivery_status IN ('pending', 'failed', 'disabled') "
                 "AND EXISTS (SELECT 1 FROM bookings b WHERE b.id = booking_id AND b.status = 'awaiting_response')",
                 (proposal_id,))
        return cursor.rowcount == 1


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
        note TEXT NOT NULL,
        created_at TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'status',
        is_private INTEGER NOT NULL DEFAULT 0 CHECK (is_private IN (0, 1))
    )""")
    _execute(cursor, "CREATE INDEX IF NOT EXISTS booking_history_booking_id ON booking_history (booking_id, id)")
    # Baseline entries record the state observed at migration, not a fabricated
    # past transition. Existing IDs, statuses and customer accounts stay intact.
    _execute(cursor, "INSERT INTO booking_history (booking_id, from_status, to_status, actor, "
             "note, created_at, kind, is_private) "
             "SELECT b.id, NULL, b.status, 'system', "
             "'Befintlig bokning bevarad. Historiken börjar vid migreringen.', %s, 'baseline', 0 "
             "FROM bookings b WHERE NOT EXISTS (SELECT 1 FROM booking_history h WHERE h.booking_id = b.id)",
             (_now(),))


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
                status TEXT NOT NULL DEFAULT 'pending'
            )""",
        )
