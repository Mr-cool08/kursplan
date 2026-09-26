import psycopg
import secrets

def generate_secret_key():
    # Generate a random 32-byte key using secrets module.
    return secrets.token_hex(32)




    
def create_booking(name, email, organization_number, utbildning, antal, ort, lokal, datum, db_path):
    # Create a new booking in the database.
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO bookings (name, email, organization_number, utbildning, antal, ort, lokal, datum) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (name, email, organization_number, utbildning, antal, ort, lokal, datum),
            )
            return cursor.fetchone()[0]


def check_user_exists(email, db_path):
    # Check if a user with the given email exists in the database.
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            return cursor.fetchone() is not None




def create_user(name, email, password, organization_number, role, db_path):
    # Create a new user in the database.
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (name, email, password, organization_number, role) "
                "VALUES (%s, %s, %s, %s, %s) "
                "RETURNING id, name, organization_number, email",
                (name, email, password, organization_number, role),
            )
            return cursor.fetchone()

def update_status(booking_id, status, db_path):
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE bookings SET status = %s WHERE id = %s",
                (status, booking_id),
            )

def get_email_by_booking_id(booking_id, db_path):
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT email FROM bookings WHERE id = %s", (booking_id,))
            row = cursor.fetchone()
            return row[0] if row else None


def book_bookings(booking_ids, booking_date, ort, db_path):
    if not booking_ids:
        return
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE bookings SET datum = %s, ort = %s, status = 'accepted' "
                "WHERE status = 'pending' AND id = ANY(%s)",
                (booking_date, ort, booking_ids),
            )

def remove_booking(booking_id, db_path):
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM bookings WHERE id = %s", (booking_id,))
    
    
def get_utbildning_by_booking_id(booking_id, db_path):
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT utbildning FROM bookings WHERE id = %s", (booking_id,))
            row = cursor.fetchone()
            return row[0] if row else None

def get_booking_by_id(booking_id, db_path):
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,))
            return cursor.fetchone()

def get_bookings_by_email(email, db_path):
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM bookings WHERE email = %s", (email,))
            rows = cursor.fetchall()
            return rows if rows else None

def get_bookings_by_status(status, db_path):
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM bookings WHERE status = %s", (status,))
            rows = cursor.fetchall()
            return rows if rows else None


def ensure_test_user(email, password, db_path):
    # Ensure that a default test user exists in the database.
    # Create a default test user if it does not already exist.
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (name, email, password, organization_number, role) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
                ("Admin", email, password, "0000000000", "admin"),
            )




def login_user(email, password, role, db_path):
    # Authenticate a user based on email and password.
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, name, organization_number FROM users "
                "WHERE email = %s AND password = %s AND role = %s",
                (email, password, role),
            )
            return cursor.fetchone()

def get_booking_by_name(name, db_path):
    # Retrieve a booking from the database based on its ID.
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM bookings WHERE name = %s", (name,))
            return cursor.fetchall()

def get_all_bookings(db_path):
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM bookings ORDER BY lower(utbildning) ASC NULLS LAST, id ASC"
            )
            return cursor.fetchall()


def create_database(db_path):
    with psycopg.connect(db_path) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS bookings (
                    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    organization_number TEXT,
                    utbildning TEXT,
                    antal INTEGER,
                    ort TEXT,
                    lokal TEXT,
                    datum TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                )"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    name TEXT NOT NULL,
                    organization_number TEXT,
                    email TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL,
                    role TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                )"""
            )
