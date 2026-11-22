import os
import json
import logging
import ssl
from smtplib import SMTP, SMTPException
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlencode

from email_templates import render_email


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

        title = "Din bokning är bekräftad"
        introduction = "Din utbildning är bekräftad med uppgifterna nedan."
        details = (
            ("Bokningsnummer", f"#{booking_id}"),
            ("Utbildning", utbildning),
            ("Utbildningsdatum", booking_date),
            ("Ort", ort),
        )
        plain = f"{title}\n\n{introduction}\n\n"
        plain += "\n".join(f"{label}: {value}" for label, value in details)
        plain += "\n\nMed vänliga hälsningar,\nKursplan.se"
        html = render_email(
            title=title,
            preheader="Här finns uppgifterna för din bekräftade utbildning.",
            introduction=introduction,
            details=details,
            status="Bekräftad",
            tone="success",
        )
        message = MIMEMultipart("alternative")
        message["From"] = send_mail
        message["To"] = email
        message["Subject"] = "Bokningsbekräftelse"
        message.attach(MIMEText(plain, "plain", "utf-8"))
        message.attach(MIMEText(html, "html", "utf-8"))

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
        ("Status", "Inväntar ditt svar"),
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
    html = render_email(
        title=f"Datumförslag för bokning #{booking['id']}",
        preheader="Ett nytt datumförslag väntar på ditt svar i kundportalen.",
        introduction=introduction,
        details=details,
        explanation=explanation,
        actions=links,
        status="Inväntar ditt svar",
    )
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


def send_booking_status_mail(booking, status, booking_url):
    """Notify the booking's customer after an acceptance or cancellation."""
    if status not in {"accepted", "cancelled"}:
        logger.warning("Statusmejl skickades inte; bokningsstatus stöds inte.")
        return "failed"
    if os.environ.get("send_mail", "1").strip() == "0":
        logger.info("Statusmejl skickades inte; e-post är avstängd.")
        return "disabled"
    sender = _environment_value("smtp_email")
    server = _environment_value("smtp_server_address")
    password = _environment_value("smtp_password")
    try:
        port = int(_environment_value("smtp_port") or "")
    except ValueError:
        logger.warning("Statusmejl skickades inte; SMTP-port saknas eller är ogiltig.")
        return "failed"
    if not sender or not server or not password or not 1 <= port <= 65535:
        logger.warning("Statusmejl skickades inte; SMTP-inställningar saknas eller är ogiltiga.")
        return "failed"
    recipient = booking["email"]
    if any(not isinstance(value, str) or not value.strip() or "\r" in value or "\n" in value
           for value in (sender, recipient)):
        logger.warning("Statusmejl skickades inte; ogiltig e-postadress.")
        return "failed"

    accepted = status == "accepted"
    title = "Utbildningen är accepterad" if accepted else "Bokningen är avbruten"
    introduction = (
        "Du har accepterat datumförslaget. Din utbildning är nu bekräftad med uppgifterna nedan."
        if accepted else
        "Din bokning har avbrutits. Utbildningen kommer inte att genomföras inom denna bokning."
    )
    explanation = (
        "Du kan se bokningens uppgifter och historik genom att logga in på Kursplan.se."
        if accepted else
        "Tidigare datumförslag kan inte längre accepteras. Bokningen och historiken finns kvar på ditt konto."
    )
    details = [
        ("Bokningsnummer", f"#{booking['id']}"),
        ("Status", "Accepterad" if accepted else "Avbruten"),
        ("Utbildning", booking["utbildning"]),
        ("Antal deltagare", booking["antal"]),
    ]
    if accepted:
        details.extend((
            ("Utbildningsdatum", booking.get("faststallt_datum") or "Inte angivet"),
            ("Ort", booking.get("faststalld_ort") or "Inte angiven"),
            ("Lokal", booking.get("faststalld_lokal") or "Inte angiven"),
        ))
    plain = f"{title}\n\n{introduction}\n\n"
    plain += "\n".join(f"{label}: {value}" for label, value in details)
    plain += f"\n\n{explanation}\n\nVisa bokningen:\n{booking_url}"
    plain += "\n\nMed vänliga hälsningar,\nKursplan.se"
    html = render_email(
        title=title,
        preheader=("Här finns uppgifterna för din bekräftade utbildning." if accepted
                   else "Din bokning är avbruten. Historiken finns kvar på ditt konto."),
        introduction=introduction,
        details=details,
        explanation=explanation,
        actions=(("Visa bokningen", booking_url),),
        status="Accepterad" if accepted else "Avbruten",
        tone="success" if accepted else "neutral",
    )
    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = f"{title} – bokning #{booking['id']} – Kursplan.se"
    message.attach(MIMEText(plain, "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))
    accepted_by_server = False
    try:
        with SMTP(server, port, timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(sender, password)
            if smtp.sendmail(sender, recipient, message.as_string()):
                logger.error("Statusmejl kunde inte levereras; SMTP-servern avvisade mottagaren.")
                return "failed"
            accepted_by_server = True
    except (OSError, SMTPException) as exc:
        # Error details can expose personal data. A failed QUIT after accepted
        # DATA does not mean delivery failed and must not prompt a duplicate.
        if accepted_by_server:
            logger.warning("Statusmejl accepterades av SMTP-servern men anslutningen avslutades med fel; feltyp=%s.",
                           type(exc).__name__)
        else:
            logger.error("Statusmejl kunde inte skickas; feltyp=%s.", type(exc).__name__)
        return "sent" if accepted_by_server else "failed"
    logger.info("Statusmejl skickades via SMTP.")
    return "sent"


def send_verification_mail(email, verification_url):
    """Send a verification link without exposing it in application logs."""
    if os.environ.get("send_mail", "1").strip() == "0":
        logger.info("Verifieringsmejl skickades inte; e-post är avstängd.")
        return "disabled"
    sender = _environment_value("smtp_email")
    server = _environment_value("smtp_server_address")
    password = _environment_value("smtp_password")
    try:
        port = int(_environment_value("smtp_port") or "")
    except ValueError:
        logger.warning("Verifieringsmejl skickades inte; SMTP-port saknas eller är ogiltig.")
        return "failed"
    if not sender or not server or not password or not 1 <= port <= 65535:
        logger.warning("Verifieringsmejl skickades inte; SMTP-inställningar saknas eller är ogiltiga.")
        return "failed"
    if not isinstance(email, str) or not email or any(
        "\r" in value or "\n" in value for value in (sender, email)
    ):
        logger.warning("Verifieringsmejl skickades inte; ogiltig e-postadress.")
        return "failed"

    introduction = "Tack för att du har skapat ett konto på Kursplan.se."
    explanation = (
        "Verifiera din e-postadress via länken nedan. När adressen är verifierad kan du logga in. "
        "Länken gäller i 24 timmar och kan bara användas en gång."
    )
    unsolicited = "Om du inte skapade kontot kan du ignorera det här mejlet."
    plain = (
        f"{introduction}\n\n{explanation}\n\nVerifiera e-postadress:\n{verification_url}"
        f"\n\n{unsolicited}\n\nMed vänliga hälsningar,\nKursplan.se"
    )
    html = render_email(
        title="Verifiera din e-postadress",
        preheader="Bekräfta din e-postadress för att komma igång. Länken gäller i 24 timmar.",
        introduction=introduction,
        explanation=explanation,
        actions=(("Verifiera e-postadress", verification_url),),
        notice=unsolicited,
        eyebrow="Ditt konto",
    )
    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = email
    message["Subject"] = "Verifiera din e-postadress – Kursplan.se"
    message.attach(MIMEText(plain, "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))
    accepted_by_server = False
    try:
        with SMTP(server, port, timeout=20) as smtp:
            # Protocol traces would expose the one-time verification link.
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(sender, password)
            if smtp.sendmail(sender, email, message.as_string()):
                logger.error("Verifieringsmejl kunde inte levereras; SMTP-servern avvisade mottagaren.")
                return "failed"
            accepted_by_server = True
    except (OSError, SMTPException) as exc:
        # A failed QUIT cannot undo accepted DATA; do not send duplicate mail.
        if accepted_by_server:
            logger.warning(
                "Verifieringsmejl accepterades av SMTP-servern men anslutningen avslutades med fel; feltyp=%s.",
                type(exc).__name__,
            )
        else:
            logger.error("Verifieringsmejl kunde inte skickas; feltyp=%s.", type(exc).__name__)
        return "sent" if accepted_by_server else "failed"
    logger.info("Verifieringsmejl skickades via SMTP.")
    return "sent"
