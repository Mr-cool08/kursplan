from flask import Flask, render_template, request, session, redirect, url_for
import sqlite3
from datetime import datetime, timedelta
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import time
import database
from itertools import combinations
import subprocess
import urllib.parse

db_path = os.getenv("db_path")
    if not db_path:
        raise ValueError("Database path is not set in the .env file.")

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("app_secret_key")  # Use the secret key from the .env file

utbildningar = [
    "HLR",
    "Heta arbeten",
    "Liftutbildning",
    "Ställningsutbildning",
    "Säkra lyft",
    "Arbetsmiljöutbildning",
    "Första hjälpen",
    "Brandskyddsutbildning"
]

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/')
def index():
    return render_template('index.html')




@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        organization_number = request.form['organization_number']
        if database.check_user_exists(email, db_path):
            return render_template('signup.html', error='A user with this email already exists.')
        user = database.create_user(name, email, password, organization_number, db_path)
        session['user_id'] = user[0]
        session['user_name'] = user[1]
        session['organization_number'] = user[2]
        session['user_email'] = user[3]
        return redirect(url_for('login'))
    else:
        return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        if 'user_id' in session:
            
            return redirect(url_for('dashboard'))
    elif request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        if not email or not password:
            return render_template('login.html', error='Please enter both email and password.')
        user = database.login_user(email, password, db_path)
        if user:
            session['user_id'] = user[0]
            session['user_name'] = user[1]
            session['organization_number'] = user[2]
            session['user_email'] = email
            return redirect(url_for('dashboard'))

        return render_template('login.html', error='Invalid email or password.')
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    name = session.get('user_name')
    organization_number = session.get('organization_number')
    user_id = session.get('user_id')
    
    
    return render_template('dashboard.html', name=name, organization_number=organization_number)

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint used by deployment platforms."""
    return "OK", 200



@app.route('/booking', methods=['GET', 'POST'])
def booking():
    if request.method == 'POST':
        if 'user_id' not in session:
            return redirect(url_for('login'))
        name = session.get('user_name')
        email = session.get('user_email')
        organization_number = session.get('organization_number')
        utbildning = request.form['utbildning']
        antal = request.form['antal']
        ort = request.form['ort']
        lokal = request.form['lokal']
        if lokal == 'Egen':
            lokal = request.form['adress']
        datum = request.form['datum']
        database.create_booking(name, email, organization_number, utbildning, antal, ort, lokal, datum, db_path)
        return redirect(url_for('dashboard'))
    if request.method == 'GET':
        if 'user_id' not in session:
            return redirect(url_for('login'))
        
        return render_template('book.html', utbildningar=utbildningar)


@app.route('/fix_later') # The page to display the list of jobs
def get_jobs():
    if 'authenticated' not in session or session['authenticated'] == False:
        return render_template('login.html')

    # Connect to the database
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    # Retrieve pending jobs from the database
    cursor.execute("SELECT * FROM bookings WHERE status='pending' ORDER BY id ASC")

    jobs = cursor.fetchall()

    # Close the database connection
    cursor.close()
    conn.close()

    # Render the bookings.html template with the job data
    return render_template('bookings.html', jobs=jobs)

@app.route('/jobs/<int:job_id>', methods=['POST']) # The action to accept the job
def accept_job(job_id):
    print(job_id)
    # Check if the user is authenticated
    if 'authenticated' not in session:
        return render_template('login.html')

    # Connect to the database
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    # Retrieve job information from the database
    cursor.execute("SELECT name, email, phone, language, time_start, time_end, organization_number, billing_address, email_billing_address, marking, avtalskund_marking, reference FROM bookings WHERE id = ?", (job_id,))
    job_data = cursor.fetchone()

    # Mark the job as accepted instead of deleting
    cursor.execute("UPDATE bookings SET status='accepted' WHERE id = ?", (job_id,))
    conn.commit()

    # Close the database connection
    cursor.close()
    conn.close()
    def send_kursplan_email():
        # Making the email body and subject
        email_subject = f"Translation Application - Job ID: {job_id}"
        email_body = f"""
        Kära Herr/Fru,

        Här är bekräftelsen för jobb-ID: {job_id}.

        Namn: {job_data[0]}
        E-post: {job_data[1]}
        Telefonnummer: {job_data[2]}
        Önskat språk: {job_data[3]}
        Starttid: {job_data[4]}
        Sluttid: {job_data[5]}

        Vänligen granska sökandens kvalifikationer och referenser. Om du behöver ytterligare information eller har några frågor, vänligen kontakta sökanden direkt via det angivna e-postadressen eller telefonnumret.

        Tack för att du valde vår tjänst. Vi uppskattar ditt stöd.

        Med vänliga hälsningar,
        kursplan-teamet"""   
        # Prepare email message
        msg = MIMEMultipart()
        msg['From'] = os.getenv("email") # Retrieving the email from the session
        msg['To'] = session.get('kursplan_email', '')  # Retrieve the email from the session
        msg['Subject'] = email_subject # Getting the subject
        msg.attach(MIMEText(email_body, 'plain'))
        smtp_username = os.getenv("email")# Getting the email for the sender
        smtp_password = os.getenv('Email_password') # Getting the password for the sender
        msg['Bcc'] = smtp_username # Adding the secret email to send to self.
        smtp_server = os.getenv("smtp_server_address")  # Connect to the SMTP server
        smtp_port = os.getenv("smtp_port")# Connect to the SMTP server
        recipient_email = session.get('kursplan_email', '')  # Retrieve the recipient email from the session

        with smtplib.SMTP(smtp_server, smtp_port) as server: # Sending the email
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(smtp_username, [recipient_email, msg['Bcc']], msg.as_string())
            
            
            
    def send_user_email():
        # Making the email body and subject
        email_subject = f"Translation Application - Job ID: {job_id}"
        email_body = f"""
                Kära Herr/Fru,

        Din ansökan för jobb-ID: {job_id} har blivit accepterad.
        
        Om du inte har använt vår tjänst, vänligen kontakta oss.
        Här är din information:   
        Namn: {job_data[0]}
        Önskat språk: {job_data[3]}
        Starttid: {job_data[4]}
        Sluttid: {job_data[5]}
        
        
        Tack för att du valde vår tjänst. Vi uppskattar ditt stöd.
        
        
        Med vänliga hälsningar,
        kursplan-teamet"""
        msg = MIMEMultipart()
        msg['From'] = os.getenv("email") # Retrieving the email from the .env file
        msg['To'] = job_data[1] # Retrieving the email from the database
        msg['Subject'] = email_subject # Getting the subject
        msg.attach(MIMEText(email_body, 'plain')) 
        smtp_username = os.getenv("email")# Getting the email for the sender
        smtp_password = os.getenv('Email_password')# Getting the password for the sender
        msg['Bcc'] = smtp_username # Adding the secret email to send to self.
        smtp_server = os.getenv("smtp_server_address")  # Connect to the SMTP server
        smtp_port = os.getenv("smtp_port")# Connect to the SMTP server
        recipient_email = job_data[1] # Retrieve the recipient email from the database
        with smtplib.SMTP(smtp_server, smtp_port) as server: # Sending the email
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(smtp_username, [recipient_email, msg['Bcc']], msg.as_string()) #Sending the mail
    send_kursplan_email() # Running the function to send to the translation company
    send_user_email() # Running the function to send to the user
    return 'Job accepted and email sent'


@app.route('/cancel_booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    if not session.get('user_id'):
        return redirect(url_for('user_login'))
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    user_email = session.get('user_email')
    cursor.execute(
        "UPDATE bookings SET status='cancelled' WHERE id=? AND email=? AND status='pending'",
        (booking_id, user_email),
    )
    conn.commit()
    conn.close()
    return redirect(url_for('home'))

@app.route('/submit', methods=['GET', 'POST'])
def submit():
    if request.method == 'GET':
        if session.get('user_id'):
            return redirect(url_for('confirmation'))
        return redirect(url_for('billing'))
    if session.get("submitted"):
        return render_template("error.html", message="You have already submitted")


    language = request.form['language']
    time_start_str = request.form['starttime']
    time_end_minutes = int(request.form['endtime'])
    time_start = datetime.strptime(time_start_str, '%Y-%m-%dT%H:%M')
    time_end = time_start + timedelta(minutes=time_end_minutes)
    time_start_str_trimmed = time_start.strftime('%Y-%m-%d %H:%M')
    time_end_str_trimmed = time_end.strftime('%Y-%m-%d %H:%M')

    if session.get('user_id'):
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute(
            'SELECT name, phone, organization_number, billing_address, email_billing_address FROM users WHERE id = ?',
            (session['user_id'],),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return redirect(url_for('user_login'))
        name, phone, organization_number, billing_address, email_billing_address = row
        email = session.get('user_email')
        if database.booking_exists(name, email, phone, language, time_start, time_end):
            return render_template('error.html', message='This booking already exists.', error_name='409')
        session.update(
            {
                'name': name,
                'email': email,
                'phone': phone,
                'language': language,
                'time_start': time_start_str_trimmed,
                'time_end': time_end_str_trimmed,
                'organization_number': organization_number,
                'billing_address': billing_address,
                'email_billing_address': email_billing_address,
                'submitted': True,
            }
        )
        return redirect(url_for('confirmation'))

    name = request.form['name']
    email = request.form['email']
    phone = request.form['phone']
    if database.booking_exists(name, email, phone, language, time_start, time_end):
        return render_template('error.html', message='This booking already exists.', error_name='409')
    session.update(
        {
            'name': name,
            'email': email,
            'phone': phone,
            'language': language,
            'time_start': time_start_str_trimmed,
            'time_end': time_end_str_trimmed,
        }
    )
    return redirect(url_for('billing'))






@app.errorhandler(404)
def page_not_found(e):
    return (
        render_template(
            'error.html',
            message='Detta var inte vad du letade efter.',
            error_name='404',
        ),
        404,
    )
if __name__ == '__main__':
    db_path = os.getenv("db_path")
    if not db_path:
        raise ValueError("Database path is not set in the .env file.")
    database.create_database(db_path)  # Ensure the database and tables are created before running the app



    # Ensure a default test account exists for easier manual testing
    email = os.getenv("test_email")
    password = os.getenv("test_password")
    database.ensure_test_user(email=email, password=password, db_path=db_path)

    port = int(os.environ.get("app_port", 80))
    host = os.environ.get("app_host", "0.0.0.0")
    debug = os.environ.get("app_debug", "False") == "True" or "true"
    app.run(port=port, host=host, debug=debug, use_reloader=False)
