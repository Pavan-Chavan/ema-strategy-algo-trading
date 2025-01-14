import smtplib
import os
import traceback

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from constants import Env
from datetime import datetime as dt
from mail import html_template as ht
from gsheet.environ import GOOGLE_SHEET_ENVIRON


def send_email(subject, body, body_content_type="plain"):
    if not GOOGLE_SHEET_ENVIRON.send_email:
        return
    email_address = os.environ.get(Env.EMAIL_ADDRESS)
    password = os.environ.get(Env.EMAIL_PASSWORD)
    recipients = os.environ.get(Env.EMAIL_RECIPIENTS)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject + " - " + dt.now().strftime("%d %b %Y, %I:%M:%S %p")
    msg["From"] = email_address
    msg["To"] = recipients
    msg.attach(MIMEText(body, body_content_type))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(email_address, password)
        smtp.send_message(msg)


def send_error_email(e):
    error_details = {"type": type(e).__name__, "message": str(e)}
    traceback_details = traceback.format_exc()
    subject = f"Error Report: {error_details['type']} in your script"
    body = ht.error_template(error_details, traceback_details)
    send_email(subject, body, "html")


def send_trading_stop_email():
    subject = "Algo Trading - Trading Stopped"
    body = ht.trading_stop()
    send_email(subject, body, "html")


def send_trading_started_email(args):
    subject = f"Algo Trading - Trading Started"
    body = ht.multiple_table(args, "Trading Started")
    send_email(subject, body, "html")


def send_order_status_email(kwargs, subject):
    body = ht.table_with_two_columns(kwargs, subject)
    send_email(f"Algo Trading - {subject}", body, "html")
