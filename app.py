from multiprocessing.util import debug

from flask import render_template, request, session, redirect, url_for
import sqlite3
from datetime import datetime, timedelta
import smtplib
import os
import signal
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from tempfile import NamedTemporaryFile

from dotenv import load_dotenv
from psycopg import OperationalError, ProgrammingError
from psycopg.conninfo import make_conninfo
import database
from dev_accounts import load_dev_accounts
from email_templates import render_email
from users import user_page
from admin import admin_page
from logging_config import LoggedFlask, configure_logging, register_request_logging


load_dotenv(Path(__file__).with_name(".env"))
dev_mode = os.getenv("DEV_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

app = LoggedFlask(__name__)
configure_logging(app)
register_request_logging(app)
app.logger.info("Applikationen startas: dev_mode=%s.", dev_mode)
app.secret_key = os.getenv("app_secret_key")  # Use the secret key from the .env file
app.config["DEV_MODE"] = dev_mode
app.config["DB_PATH"] = os.getenv("db_path")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=60)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True

if dev_mode:
    app.secret_key = app.secret_key or database.generate_secret_key()
elif not app.config["DB_PATH"]:
    app.logger.critical("Appstart avbruten: databasanslutning saknas i konfigurationen.")
    raise ValueError("Database path is not set in the .env file.")

app.register_blueprint(user_page)
app.register_blueprint(admin_page)


@app.before_request
def keep_authenticated_session_permanent():
    # Befintliga inloggningar får också 60 dagar från senaste besöket.
    if session.get("user_id") and not session.permanent:
        session.permanent = True


def initialize_database():
    app.logger.info("Databasen initieras.")
    db_path = app.config["DB_PATH"]
    database.create_database(db_path)

    if dev_mode:
        accounts_path = Path(__file__).with_name(".dev-accounts.json")
        accounts = load_dev_accounts(accounts_path)
        database.ensure_dev_users(
            accounts["admin"]["email"], accounts["admin"]["password"],
            accounts["user"]["email"], accounts["user"]["password"], db_path,
        )
        booking_ids = database.ensure_dev_bookings(accounts["user"]["email"], db_path)
        history_ids = database.ensure_dev_booking_history(accounts["admin"]["email"], db_path)
        workflow_ids = database.ensure_dev_workflow_bookings(
            accounts["user"]["email"], accounts["admin"]["email"], db_path,
        )
        app.logger.debug("Utvecklingsläge: skapade %s exempelbokningar.", len(booking_ids))
        app.logger.debug("Utvecklingsläge: skapade %s exempel med bokningsflöden.", len(workflow_ids))
        app.logger.debug("Utvecklingsläge: lade till exempelhistorik på %s bokningar.", len(history_ids))
        app.logger.debug("Utvecklingskontonas inloggningsuppgifter finns i %s", accounts_path)
    else:
        database.ensure_test_user(
            email=os.getenv("test_email"), password=os.getenv("test_password"), db_path=db_path,
        )
    app.logger.info("Databasen är initierad.")


def initialize_dev_database():
    if app.config["DB_PATH"]:
        try:
            app.config["DB_PATH"] = make_conninfo(
                app.config["DB_PATH"], connect_timeout=5
            )
        except ProgrammingError:
            # Invalid connection settings also allow the local fallback.
            app.logger.warning("Utvecklingsdatabasens anslutningsinställningar är ogiltiga.")
        else:
            try:
                initialize_database()
            except OperationalError:
                app.logger.warning("Anslutning till utvecklingsdatabasen misslyckades.")
            else:
                app.logger.debug(
                    "Utvecklingsläge: använder den konfigurerade PostgreSQL-databasen."
                )
                return None

    app.logger.warning(
        "Utvecklingsläge: den konfigurerade databasen saknas eller kan inte nås. "
        "Använder en temporär SQLite-fil som tas bort vid normalt avslut."
    )
    temporary_file = NamedTemporaryFile(
        prefix="kursplan-dev-", suffix=".db",
        dir=Path(__file__).resolve().parent, delete_on_close=False,
    )
    app.config["DB_PATH"] = Path(temporary_file.name)
    initialize_database()
    app.logger.debug("Temporär SQLite-databas: %s", app.config["DB_PATH"])
    return temporary_file


if dev_mode:
    try:
        dev_database_file = initialize_dev_database()
    except Exception:
        app.logger.critical("Appstart avbruten: utvecklingsdatabasen kunde inte initieras.", exc_info=True)
        raise


@app.route('/logout')
def logout():
    app.logger.info("Utloggning genomförd.")
    session.clear()
    return redirect(url_for('user_page.index'))  








@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint used by deployment platforms."""
    return {"status": "ok"}, 200





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

def accept_job(job_id):
    app.logger.debug("Accepting legacy job %s", job_id)
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
        email_subject = f"Bekräftelse för uppdrag #{job_id} – Kursplan.se"
        introduction = f"Här är bekräftelsen för uppdrag #{job_id}."
        details = (
            ("Namn", job_data[0]),
            ("E-post", job_data[1]),
            ("Telefonnummer", job_data[2]),
            ("Önskat språk", job_data[3]),
            ("Starttid", job_data[4]),
            ("Sluttid", job_data[5]),
        )
        explanation = (
            "Vänligen granska sökandens kvalifikationer och referenser. "
            "Om du behöver ytterligare information eller har frågor, kontakta "
            "sökanden via den angivna e-postadressen eller telefonnumret."
        )
        email_body = introduction + "\n\n" + "\n".join(
            f"{label}: {value}" for label, value in details
        ) + f"\n\n{explanation}\n\nMed vänliga hälsningar,\nKursplan.se"
        html_body = render_email(
            title=f"Bekräftelse för uppdrag #{job_id}",
            preheader="Här finns uppgifterna för det accepterade uppdraget.",
            introduction=introduction,
            details=details,
            explanation=explanation,
            eyebrow="Ditt uppdrag",
            status="Accepterat",
            tone="success",
        )
        # Prepare email message
        msg = MIMEMultipart("alternative")
        msg['From'] = os.getenv("email") # Retrieving the email from the session
        msg['To'] = session.get('kursplan_email', '')  # Retrieve the email from the session
        msg['Subject'] = email_subject # Getting the subject
        msg.attach(MIMEText(email_body, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
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
        email_subject = f"Din ansökan för uppdrag #{job_id} är accepterad – Kursplan.se"
        introduction = f"Din ansökan för uppdrag #{job_id} har blivit accepterad."
        details = (
            ("Namn", job_data[0]),
            ("Önskat språk", job_data[3]),
            ("Starttid", job_data[4]),
            ("Sluttid", job_data[5]),
        )
        notice = "Om du inte har använt vår tjänst, vänligen kontakta oss."
        email_body = introduction + "\n\n" + "\n".join(
            f"{label}: {value}" for label, value in details
        ) + f"\n\n{notice}\n\nMed vänliga hälsningar,\nKursplan.se"
        html_body = render_email(
            title="Din ansökan är accepterad",
            preheader="Här finns uppgifterna för din accepterade ansökan.",
            introduction=introduction,
            details=details,
            notice=notice,
            eyebrow="Ditt uppdrag",
            status="Accepterad",
            tone="success",
        )
        msg = MIMEMultipart("alternative")
        msg['From'] = os.getenv("email") # Retrieving the email from the .env file
        msg['To'] = job_data[1] # Retrieving the email from the database
        msg['Subject'] = email_subject # Getting the subject
        msg.attach(MIMEText(email_body, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
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
if not dev_mode:
    # These routes use the legacy translation-job database schema.
    app.add_url_rule('/fix_later', view_func=get_jobs)
    app.add_url_rule('/jobs/<int:job_id>', view_func=accept_job, methods=['POST'])
    app.add_url_rule('/submit', view_func=submit, methods=['GET', 'POST'])

if __name__ == '__main__':
    def stop_on_signal(signum, frame):
        app.logger.info("Stoppsignal mottagen: signal=%s.", signum)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop_on_signal)
    try:
        if not dev_mode:
            initialize_database()
        debug = os.environ.get("flask_debug", "").strip().lower() in {"1", "true", "yes", "on"}
        if debug:
                app.logger.info("Applikationen körs i felsökningsläge.")
                debug = True
        else:
                app.logger.info("Applikationen körs i produktionsläge.")
                debug = False
        port = int(os.environ.get("app_port", 80))
        host = os.environ.get("app_host", "0.0.0.0")
        app.logger.info("Webbservern startas: port=%s.", port)
        app.run(port=port, host=host, use_reloader=debug, debug=debug)
    except SystemExit as error:
        if error.code:
            app.logger.critical("Applikationen stoppades med en felkod under serverstart.")
        raise
    except Exception:
        app.logger.critical("Applikationen kunde inte köras.", exc_info=True)
        raise
    finally:
        app.logger.info("Webbservern har stoppats.")
