import os
import json
import logging
import ssl
from smtplib import SMTP, SMTPException
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from urllib.parse import urlencode


logger = logging.getLogger(__name__)


def _environment_value(name):
    value = os.environ.get(name)
    if value is None:
        return None
    return value.strip().strip("\"'") or None

def read_utbildningar_from_json():
    
    file_path = os.path.join(os.path.dirname(__file__), 'utbildningar.json')  # Adjust the path as needed
    # Read utbildningar from a JSON file.
    if not os.path.exists(file_path):
        logger.warning("Utbildningskatalogen saknas; en tom katalog används.")
        return []  # Return an empty list if the file doesn't exist

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            courses = data.get('utbildningar', [])
    except (OSError, ValueError, AttributeError) as exc:
        logger.error("Utbildningskatalogen kunde inte läsas; feltyp=%s.", type(exc).__name__)
        raise
    logger.debug("Utbildningskatalogen lästes in.")
    return courses  # Return the list of utbildningar or an empty list if not found
    
    
def send_booked_mail(email, booking_date, ort, booking_id, utbildning):
    if os.environ.get("send_mail", "1") == "0":
        logger.info("Bokningsbekräftelse skickades inte; e-post är avstängd.")
        return False
    else:
        send_mail = _environment_value("smtp_email")
        smtp_server = _environment_value("smtp_server_address")
        smtp_port = _environment_value("smtp_port")
        smtp_password = _environment_value("smtp_password")

        if (send_mail is None or smtp_server is None or
                not smtp_port or smtp_password is None):
            logger.warning("Bokningsbekräftelse skickades inte; SMTP-inställningar saknas.")
            return False

        try:
            smtp_port_number = int(smtp_port)
        except ValueError:
            logger.warning("Bokningsbekräftelse skickades inte; ogiltig SMTP-port.")
            return False

        message = MIMEMultipart()
        message["From"] = send_mail
        message["To"] = email
        message["Subject"] = "Bokningsbekräftelse"
        message.attach(MIMEText(f"Din bokning är bekräftad för {utbildning} i {ort} på {booking_date}. Din bokningsnummer är {booking_id}.", "plain"))

        logger.debug("Bokningsbekräftelse: ansluter till SMTP.")
        try:
            with SMTP(smtp_server, smtp_port_number) as smtp:
                # Protocol traces expose credentials and entire mail contents.
                smtp.set_debuglevel(0)
                smtp.starttls()
                logger.debug("Bokningsbekräftelse: TLS har startats.")
                smtp.login(send_mail, smtp_password)
                logger.debug("Bokningsbekräftelse: SMTP-autentisering lyckades.")
                smtp.sendmail(send_mail, email, message.as_string())
        except (OSError, SMTPException) as exc:
            logger.error("Bokningsbekräftelse misslyckades; feltyp=%s.", type(exc).__name__)
            raise
        logger.info("Bokningsbekräftelse skickades via SMTP.")
        return True


def send_proposal_mail(booking, proposal, booking_url):
    """Send a proposal, never a confirmation; report delivery truthfully."""
    if os.environ.get("send_mail", "1").strip() == "0":
        logger.info("Datumförslag skickades inte; e-post är avstängd.")
        return "disabled"
    sender = _environment_value("smtp_email")
    server = _environment_value("smtp_server_address")
    password = _environment_value("smtp_password")
    try:
        port = int(_environment_value("smtp_port") or "")
    except ValueError:
        logger.warning("Datumförslag skickades inte; SMTP-port saknas eller är ogiltig.")
        return "failed"
    if not sender or not server or not password or not 1 <= port <= 65535:
        logger.warning("Datumförslag skickades inte; SMTP-inställningar saknas eller är ogiltiga.")
        return "failed"
    recipient = booking["email"]
    if any("\r" in value or "\n" in value for value in (sender, recipient)):
        logger.warning("Datumförslag skickades inte; otillåtna tecken i e-posthuvud.")
        return "failed"
    choices = (
        ("Acceptera förslaget", "accept"),
        ("Begär nytt datum", "new_date"),
        ("Avbryt bokningen", "cancel"),
    )
    links = [(label, booking_url + "?" + urlencode({"proposal_id": proposal["id"], "action": action}))
             for label, action in choices]
    details = (
        ("Bokningsnummer", f"#{booking['id']}"),
        ("Utbildning", booking["utbildning"]),
        ("Antal deltagare", str(booking["antal"])),
        ("Föreslaget datum", proposal["proposed_date"]),
        ("Föreslagen ort", proposal["proposed_city"]),
        ("Föreslagen lokal", proposal["proposed_venue"]),
    )
    introduction = "Du har fått ett datumförslag för din utbildning. Logga in på Kursplan.se och svara på förslaget."
    explanation = "Bokningen blir bekräftad först när du har accepterat förslaget. Länkarna öppnar bokningen; du bekräftar ditt val efter inloggning."
    plain = introduction + "\n\n" + "\n".join(f"{label}: {value}" for label, value in details)
    plain += "\n\n" + explanation + "\n\n" + "\n\n".join(f"{label}:\n{link}" for label, link in links)
    plain += "\n\nMed vänliga hälsningar,\nKursplan.se"
    html = "<!doctype html><html lang='sv'><body>" + f"<h1>Datumförslag för bokning #{booking['id']}</h1><p>{escape(introduction)}</p><dl>"
    html += "".join(f"<dt>{escape(label)}</dt><dd>{escape(str(value))}</dd>" for label, value in details)
    html += f"</dl><p>{escape(explanation)}</p>"
    html += "".join(f'<p><a href="{escape(link, quote=True)}">{escape(label)}</a></p>' for label, link in links)
    html += "<p>Med vänliga hälsningar,<br>Kursplan.se</p></body></html>"
    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = f"Datumförslag för bokning #{booking['id']} – Kursplan.se"
    message.attach(MIMEText(plain, "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))
    accepted_by_server = False
    logger.debug("Datumförslag: ansluter till SMTP.")
    try:
        with SMTP(server, port, timeout=20) as smtp:
            # SMTP protocol debugging can expose credentials and personal data.
            smtp.starttls(context=ssl.create_default_context())
            logger.debug("Datumförslag: TLS har startats.")
            smtp.login(sender, password)
            logger.debug("Datumförslag: SMTP-autentisering lyckades.")
            refused = smtp.sendmail(sender, recipient, message.as_string())
            if refused:
                logger.error("Datumförslag kunde inte levereras; SMTP-servern avvisade mottagaren.")
                return "failed"
            accepted_by_server = True
    except (OSError, SMTPException) as exc:
        # No exception details: server responses may contain personal data.
        # A failed QUIT cannot undo accepted DATA; retrying would duplicate mail.
        if accepted_by_server:
            logger.warning("Datumförslag accepterades av SMTP-servern men anslutningen avslutades med fel; feltyp=%s.",
                           type(exc).__name__)
        else:
            logger.error("Datumförslag kunde inte skickas; feltyp=%s.", type(exc).__name__)
        return "sent" if accepted_by_server else "failed"
    logger.info("Datumförslag skickades via SMTP.")
    return "sent"
