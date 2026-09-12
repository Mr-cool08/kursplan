import sqlite3
import secrets
import os
import hashlib
import time

def generate_secret_key():
    # Generate a random 32-byte key using secrets module.
    return secrets.token_hex(32)




    
def create_booking(name, email, organization_number, utbildning, antal, ort, lokal, datum, db_path):
    # Create a new booking in the database.
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO bookings (name, email, organization_number, utbildning, antal, ort, lokal, datum) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, email, organization_number, utbildning, antal, ort, lokal, datum),
    )
    booking_id = cursor.lastrowid  # Get the ID of the newly created booking
    conn.commit()
    conn.close()
    return booking_id  # Return the ID of the newly created booking


def check_user_exists(email, db_path):
    # Check if a user with the given email exists in the database.
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    conn.commit()
    conn.close()
    return user is not None




def create_user(name, email, password, organization_number, role, db_path):
    # Create a new user in the database.
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (name, email, password, organization_number, role) VALUES (?, ?, ?, ?, ?)",
        (name, email, password, organization_number, role),
    )
    cursor.execute("SELECT id, name, organization_number, email FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    conn.commit()
    conn.close()
    return user

def update_status(booking_id, status, db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE bookings SET status = ? WHERE id = ?",
        (status, booking_id)
    )
    conn.commit()
    conn.close()

def get_email_by_booking_id(booking_id, db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def book_bookings(booking_ids, booking_date, ort, db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in booking_ids)
    cursor.execute(
        f"UPDATE bookings SET datum = ?, ort = ?, status = 'accepted' WHERE status = 'pending' AND id IN ({placeholders})",
        (booking_date, ort, *booking_ids)
    )
    conn.commit()
    conn.close()

def remove_booking(booking_id, db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    id = booking_id
    cursor.execute("DELETE FROM bookings WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    
    
def get_utbildning_by_booking_id(booking_id, db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT utbildning FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def get_booking_by_id(booking_id, db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()
    conn.close()
    return row if row else None

def get_bookings_by_email(email, db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE email = ?", (email,))
    rows = cursor.fetchall()
    conn.close()
    return rows if rows else None

def get_bookings_by_status(status, db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE status = ?", (status,))
    rows = cursor.fetchall()
    conn.close()
    return rows if rows else None


def ensure_test_user(email, password, db_path):
    # Ensure that a default test user exists in the database.
    # Create a default test user if it does not already exist.
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT email FROM users WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        return


    cursor.execute(
        "INSERT INTO users (name, email, password, organization_number, role) VALUES (?, ?, ?, ?, ?)",
        ("Admin", email, password, "0000000000", "admin"),
    )
    conn.commit()
    conn.close()




def login_user(email, password, role, db_path):
    # Authenticate a user based on email and password.
    print(email, password, role)
    print("EMAIL:", repr(email))
    print("PASSWORD:", repr(password))
    print("ROLE:", repr(role))
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, organization_number FROM users WHERE email = ? AND password = ? AND role = ?", (email, password, role))
    user = cursor.fetchone()
    print(user)
    conn.commit()
    conn.close()
    return user # Returns None if no matching user is found

def get_booking_by_name(name, db_path):
    # Retrieve a booking from the database based on its ID.
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE name = ?", (name,))
    booking = cursor.fetchall()
    conn.commit()
    conn.close()
    return booking  # Returns None if no matching booking is found

def get_all_bookings(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings ORDER BY utbildning COLLATE NOCASE ASC, id ASC")
    bookings = cursor.fetchall()
    print(bookings)
    conn.close()

    return bookings


def create_database(db_path):
        
        conn = sqlite3.connect(db_path) 
        cursor = conn.cursor()
    
        # Create the 'bookings' table
        #Contains:
        # Name
        # Email
        #orginasation nummer
        # antal
        # ort
        # lokal
        # datum
        # status (default pending and obly changed by admin)
        cursor.execute('''CREATE TABLE IF NOT EXISTS bookings
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        email TEXT NOT NULL,
                        organization_number TEXT,
                        utbildning TEXT,
                        antal INTEGER,
                        ort TEXT,
                        lokal TEXT,
                        datum TEXT,
                        status TEXT NOT NULL DEFAULT "pending")''')

        
        
        # Create the 'users' table
        cursor.execute('''CREATE TABLE IF NOT EXISTS users
                                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                name TEXT NOT NULL,
                                organization_number TEXT,
                                email TEXT NOT NULL UNIQUE,
                                password TEXT NOT NULL,
                                role TEXT,
                                status TEXT NOT NULL DEFAULT "pending")''')
        conn.commit()
        conn.close()
