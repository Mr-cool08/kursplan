import os
import json
from smtplib import SMTP
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv



load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)


def _environment_value(name):
    value = os.environ.get(name)
    if value is None:
        return None
    return value.strip().strip("\"'") or None

def read_utbildningar_from_json():
    
    file_path = os.path.join(os.path.dirname(__file__), 'utbildningar.json')  # Adjust the path as needed
    # Read utbildningar from a JSON file.
    if not os.path.exists(file_path):
        return []  # Return an empty list if the file doesn't exist

    with open(file_path, 'r', encoding='utf-8') as file:
        data = json.load(file)
        return data.get('utbildningar', [])  # Return the list of utbildningar or an empty list if not found
    
    
def send_booked_mail(email, booking_date, ort, booking_id, utbildning):
    if os.environ.get("send_mail", "1") == "0":
        return False
    else:
        send_mail = _environment_value("smtp_email")
        smtp_server = _environment_value("smtp_server_address")
        smtp_port = _environment_value("smtp_port")
        smtp_password = _environment_value("smtp_password")

        if (send_mail is None or smtp_server is None or
                not smtp_port or smtp_password is None):
            print("SMTP is not configured; booking confirmation was not sent.")
            return False

        try:
            smtp_port_number = int(smtp_port)
        except ValueError:
            print("SMTP port is invalid; booking confirmation was not sent.")
            return False

        message = MIMEMultipart()
        message["From"] = send_mail
        message["To"] = email
        message["Subject"] = "Bokningsbekräftelse"
        message.attach(MIMEText(f"Din bokning är bekräftad för {utbildning} i {ort} på {booking_date}. Din bokningsnummer är {booking_id}.", "plain"))

        with SMTP(smtp_server, smtp_port_number) as smtp:
            smtp.set_debuglevel(int(os.environ.get("DEBUG", "0")))
            smtp.starttls()
            smtp.login(send_mail, smtp_password)
            smtp.sendmail(send_mail, email, message.as_string())

        return True
