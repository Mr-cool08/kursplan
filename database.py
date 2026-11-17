from contextlib import closing, contextmanager
from pathlib import Path
import secrets
import sqlite3

import psycopg


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


def create_booking(name, email, organization_number, utbildning, antal, ort, lokal, datum, db_path):
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            "INSERT INTO bookings (name, email, organization_number, utbildning, antal, ort, lokal, datum) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (name, email, organization_number, utbildning, antal, ort, lokal, datum),
        )
        return cursor.fetchone()[0]


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


def get_email_by_booking_id(booking_id, db_path):
    with _cursor(db_path) as cursor:
        _execute(cursor, "SELECT email FROM bookings WHERE id = %s", (booking_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def book_bookings(booking_ids, booking_date, ort, db_path):
    if not booking_ids:
        return

    query = "UPDATE bookings SET datum = %s, ort = %s, status = 'accepted' WHERE status = 'pending'"
    if isinstance(db_path, Path):
        placeholders = ", ".join(["%s"] * len(booking_ids))
        query += f" AND id IN ({placeholders})"
        params = (booking_date, ort, *booking_ids)
    else:
        query += " AND id = ANY(%s)"
        params = (booking_date, ort, booking_ids)

    with _cursor(db_path) as cursor:
        _execute(cursor, query, params)


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
        _execute(cursor, "SELECT * FROM bookings WHERE id = %s", (booking_id,))
        return cursor.fetchone()


def get_bookings_by_email(email, db_path):
    with _cursor(db_path) as cursor:
        _execute(cursor, "SELECT * FROM bookings WHERE email = %s", (email,))
        rows = cursor.fetchall()
        return rows if rows else None


def get_bookings_by_status(status, db_path):
    with _cursor(db_path) as cursor:
        _execute(cursor, "SELECT * FROM bookings WHERE status = %s", (status,))
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


def login_user(email, password, role, db_path):
    with _cursor(db_path) as cursor:
        _execute(
            cursor,
            "SELECT id, name, organization_number FROM users "
            "WHERE email = %s AND password = %s AND role = %s",
            (email, password, role),
        )
        return cursor.fetchone()


def get_booking_by_name(name, db_path):
    with _cursor(db_path) as cursor:
        _execute(cursor, "SELECT * FROM bookings WHERE name = %s", (name,))
        return cursor.fetchall()


def get_all_bookings(db_path):
    with _cursor(db_path) as cursor:
        if isinstance(cursor, sqlite3.Cursor):
            # SQLite's built-in lower() only converts ASCII characters.
            cursor.connection.create_function(
                "lower", 1, lambda value: value.lower() if value is not None else None
            )
        _execute(cursor, "SELECT * FROM bookings ORDER BY lower(utbildning) ASC NULLS LAST, id ASC")
        return cursor.fetchall()


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
                status TEXT NOT NULL DEFAULT 'pending'
            )""",
        )
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
