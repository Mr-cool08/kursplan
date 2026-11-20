from contextlib import closing, contextmanager
from datetime import date, timedelta
from pathlib import Path
import secrets
import sqlite3

import psycopg
from werkzeug.security import check_password_hash, generate_password_hash


_LEGACY_BOOKING_SELECT = """
    id,
    name,
    email,
    organization_number,
    utbildning,
    antal,
    COALESCE(faststalld_ort, onskad_ort, ort) AS ort,
    lokal,
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
)

_BOOKING_MIGRATION_COLUMNS = _BOOKING_DETAIL_COLUMNS[10:]


@contextmanager
def _cursor(db_path):
    # A Path selects the local SQLite file. Production passes a PostgreSQL DSN.
    if isinstance(db_path, Path):
        with (
            closing(sqlite3.connect(db_path, autocommit=False)) as conn,
            conn,
            closing(conn.cursor()) as cursor,
        ):
            yield cursor
    else:
        with psycopg.connect(db_path) as conn, conn.cursor() as cursor:
            yield cursor


def _execute(cursor, query, params=()):
    if isinstance(cursor, sqlite3.Cursor):
        query = query.replace("%s", "?")
    return cursor.execute(query, params)


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
    return cursor.fetchone()[0]


def create_booking(
    name, email, organization_number, utbildning, antal, ort, lokal, datum, db_path
):
    """Create a legacy exact-date request while preserving its original wishes."""
    with _cursor(db_path) as cursor:
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
        raise ValueError("Okänd datumtyp")

    if utbildningskalla not in {"katalog", "fritext"}:
        raise ValueError("Okänd utbildningskälla")

    if lokalansvar == "kund":
        legacy_lokal = lokaladress
    elif lokalansvar == "sakerhetsutbildarna":
        legacy_lokal = "Säkerhetsutbildarna ordnar lokalen"
        lokaladress = None
    else:
        raise ValueError("Okänt lokalansvar")

    normalized_ort = (
        onskad_ort.strip()
        if isinstance(onskad_ort, str) and onskad_ort.strip()
        else None
    )

    with _cursor(db_path) as cursor:
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
    with _cursor(db_path) as cursor:
        _execute(cursor, "SELECT id FROM users WHERE email = %s", (email,))
        return cursor.fetchone() is not None


def create_user(name, email, password, organization_number, role, db_path):
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            "INSERT INTO users (name, email, password, organization_number, role) "
            "VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id, name, organization_number, email",
            (name, email, password, organization_number, role),
        )
        return cursor.fetchone()


def update_status(booking_id, status, db_path):
    with _cursor(db_path) as cursor:
        _execute(cursor, "UPDATE bookings SET status = %s WHERE id = %s", (status, booking_id))


def cancel_booking(booking_id, db_path):
    """Cancel one pending booking and return the number of changed rows."""
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            "UPDATE bookings SET status = 'cancelled' "
            "WHERE id = %s AND status = 'pending'",
            (booking_id,),
        )
        return cursor.rowcount


def cancel_booking_for_email(booking_id, email, db_path):
    """Cancel a customer's pending booking without trusting its public id alone."""
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            "UPDATE bookings SET status = 'cancelled' "
            "WHERE id = %s AND email = %s AND status = 'pending'",
            (booking_id, email),
        )
        return cursor.rowcount


def get_email_by_booking_id(booking_id, db_path):
    with _cursor(db_path) as cursor:
        _execute(cursor, "SELECT email FROM bookings WHERE id = %s", (booking_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def book_bookings(booking_ids, booking_date, ort, db_path):
    if not booking_ids:
        return []

    query = (
        "UPDATE bookings SET faststallt_datum = %s, faststalld_ort = %s, "
        "status = 'accepted' WHERE status = 'pending'"
    )
    if isinstance(db_path, Path):
        placeholders = ", ".join(["%s"] * len(booking_ids))
        query += f" AND id IN ({placeholders})"
        params = (booking_date, ort, *booking_ids)
    else:
        query += " AND id = ANY(%s)"
        params = (booking_date, ort, booking_ids)

    query += " RETURNING id"

    with _cursor(db_path) as cursor:
        _execute(cursor, query, params)
        return [row[0] for row in cursor.fetchall()]


def remove_booking(booking_id, db_path):
    with _cursor(db_path) as cursor:
        _execute(cursor, "DELETE FROM bookings WHERE id = %s", (booking_id,))


def get_utbildning_by_booking_id(booking_id, db_path):
    with _cursor(db_path) as cursor:
        _execute(cursor, "SELECT utbildning FROM bookings WHERE id = %s", (booking_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def get_booking_by_id(booking_id, db_path):
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            f"SELECT {_LEGACY_BOOKING_SELECT} FROM bookings WHERE id = %s",
            (booking_id,),
        )
        return cursor.fetchone()


def get_booking_details_by_id(booking_id, db_path):
    """Return raw legacy, original-request and confirmed booking fields by name."""
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            f"SELECT {', '.join(_BOOKING_DETAIL_COLUMNS)} "
            "FROM bookings WHERE id = %s",
            (booking_id,),
        )
        row = cursor.fetchone()
        return dict(zip(_BOOKING_DETAIL_COLUMNS, row)) if row else None


def get_bookings_by_email(email, db_path):
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            f"SELECT {_LEGACY_BOOKING_SELECT} FROM bookings WHERE email = %s",
            (email,),
        )
        rows = cursor.fetchall()
        return rows if rows else None


def get_bookings_by_status(status, db_path):
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            f"SELECT {_LEGACY_BOOKING_SELECT} FROM bookings WHERE status = %s",
            (status,),
        )
        rows = cursor.fetchall()
        return rows if rows else None


def ensure_test_user(email, password, db_path):
    with _cursor(db_path) as cursor:
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
        raise ValueError("Utvecklingskontona måste ha olika e-postadresser.")

    accounts = (
        ("Utvecklingsadministratör", admin_email, admin_password, "admin"),
        ("Utvecklingsanvändare", user_email, user_password, "user"),
    )
    with _cursor(db_path) as cursor:
        for name, email, password, role in accounts:
            _execute(
                cursor,
                "INSERT INTO users (name, email, password, organization_number, role) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
                (name, email, generate_password_hash(password), "0000000000", role),
            )


def ensure_dev_bookings(user_email, db_path):
    """Create sample bookings once per development customer without sending mail."""
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            "SELECT id, name, organization_number FROM users "
            "WHERE email = %s AND role = 'user'",
            (user_email,),
        )
        user = cursor.fetchone()
        if user is None:
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
            return []

        today = date.today()

        def future_date(days):
            return (today + timedelta(days=days)).isoformat()

        samples = (
            {
                "utbildning": "HLR", "utbildningskalla": "katalog", "antal": 8,
                "datumtyp": "exakt", "onskat_datum": future_date(30),
                "onskad_ort": "Stockholm", "lokalansvar": "kund",
                "lokaladress": "Exempelgatan 1, Stockholm", "status": "pending",
            },
            {
                "utbildning": "Exempel: Anpassad säkerhetsutbildning",
                "utbildningskalla": "fritext", "antal": 12,
                "datumtyp": "intervall", "onskat_startdatum": future_date(45),
                "onskat_slutdatum": future_date(49), "status": "pending",
            },
            {
                "utbildning": "Heta arbeten", "utbildningskalla": "katalog", "antal": 6,
                "datumtyp": "intervall", "onskat_startdatum": future_date(14),
                "onskat_slutdatum": future_date(18), "onskad_ort": "Göteborg",
                "faststallt_datum": future_date(16), "faststalld_ort": "Göteborg",
                "status": "accepted",
            },
            {
                "utbildning": "Första hjälpen", "utbildningskalla": "katalog", "antal": 10,
                "datumtyp": "exakt", "onskat_datum": future_date(60),
                "onskad_ort": "Malmö", "status": "cancelled",
            },
        )
        booking_ids = []
        for sample in samples:
            booking = dict.fromkeys(_BOOKING_DETAIL_COLUMNS[1:])
            booking.update(
                name=user[1], email=user_email, organization_number=user[2],
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
        return booking_ids


def authenticate_user(email, password, db_path):
    """Authenticate a user and return the database-backed account role."""
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            "SELECT id, name, organization_number, email, role, password FROM users "
            "WHERE email = %s",
            (email,),
        )
        user = cursor.fetchone()

    if user is None:
        return None
    stored_password = user[5]
    if (
        stored_password.startswith(("scrypt:", "pbkdf2:"))
        and stored_password.count("$") == 2
    ):
        try:
            valid = check_password_hash(stored_password, password)
        except ValueError:
            valid = False
    else:
        # Existing accounts remain usable until a separate password migration.
        valid = secrets.compare_digest(
            stored_password.encode("utf-8"), password.encode("utf-8")
        )
    return user[:5] if valid else None


def login_user(email, password, role, db_path):
    """Authenticate a specific role (kept for backwards compatibility)."""
    user = authenticate_user(email, password, db_path)
    if user is None or user[4] != role:
        return None
    return user[:3]


def get_user_role(user_id, db_path):
    """Return the user's current role so access follows database changes."""
    with _cursor(db_path) as cursor:
        _execute(cursor, "SELECT role FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def get_booking_by_name(name, db_path):
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            f"SELECT {_LEGACY_BOOKING_SELECT} FROM bookings WHERE name = %s",
            (name,),
        )
        return cursor.fetchall()


def get_all_bookings(db_path):
    with _cursor(db_path) as cursor:
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


def create_database(db_path):
    if isinstance(db_path, Path):
        id_column = "INTEGER PRIMARY KEY AUTOINCREMENT"
    else:
        id_column = "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"

    with _cursor(db_path) as cursor:
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
                faststalld_ort TEXT
            )""",
        )
        _migrate_bookings(cursor)
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
