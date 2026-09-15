#!/usr/bin/env python3
"""
Test script to send sample emails using the new template system.
"""
import asyncio
import os
import sys
from uuid import uuid4

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load env before importing settings
from dotenv import load_dotenv
load_dotenv()

from app.core.email_templates import (
    render_otp_email,
    render_auth_otp_email,
    render_booking_confirmation_customer,
    render_booking_confirmation_admin,
    render_booking_cancellation,
    list_templates,
)
from app.core.email_delivery_service import send_email_with_failover
from app.core.config import settings


async def send_test_email(to_email: str, subject: str, body_html: str, channel: str = "otp"):
    """Send a test email using the delivery service."""
    org_id = uuid4()  # dummy org for testing
    await send_email_with_failover(
        org_id=org_id,
        channel=channel,
        to_email=to_email,
        subject=subject,
        body_html=body_html,
        request_id="test-script",
        attempt_count=1,
    )
    print(f"[OK] Sent")


async def send_test_email_direct(to_email: str, subject: str, body_html: str):
    """Send a test email directly via SMTP (for auth emails without org context)."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.EMAILS_FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(body_html, "html"))
    
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
    print(f"[OK] Sent (direct)")


async def main():
    recipient = "Priyanthakur3020@gmail.com"
    
    print("=" * 60)
    print("DotStark Email Template Test")
    print("=" * 60)
    print(f"Recipient: {recipient}")
    print(f"From: {settings.EMAILS_FROM_EMAIL}")
    print(f"SMTP: {settings.SMTP_HOST}:{settings.SMTP_PORT}")
    print()
    
    # List available templates
    print("Available templates:")
    for t in list_templates():
        print(f"  - {t}")
    print()
    
    # Test 1: OTP Email (booking flow)
    print("1. Sending OTP Email (booking flow)...")
    subject, body = render_otp_email("123456")
    await send_test_email(recipient, subject, body, channel="otp")
    
    # Test 2: Auth OTP - Register (sent directly, no delivery log)
    print("2. Sending Auth OTP - Register...")
    subject, body = render_auth_otp_email("789012", "register")
    await send_test_email_direct(recipient, subject, body)
    
    # Test 3: Auth OTP - Password Reset (sent directly, no delivery log)
    print("3. Sending Auth OTP - Password Reset...")
    subject, body = render_auth_otp_email("345678", "password_reset")
    await send_test_email_direct(recipient, subject, body)
    
    # Test 4: Auth OTP - Sign In (sent directly, no delivery log)
    print("4. Sending Auth OTP - Sign In...")
    subject, body = render_auth_otp_email("901234", "signin")
    await send_test_email_direct(recipient, subject, body)
    
    # Test 5: Booking Confirmation - Customer
    print("5. Sending Booking Confirmation (Customer)...")
    booking_details = {
        "name": "Priyan Thakur",
        "email": "Priyanthakur3020@gmail.com",
        "phone": "+91 98765 43210",
        "date": "2026-09-20",
        "time": "10:30 AM",
        "source_channel": "Web Widget",
        "meeting_type_snapshot": "ONLINE",
        "meeting_link_status": "READY",
        "meeting_link_url": "https://meet.google.com/abc-defg-hij",
        "meeting_link_provider": "Google Meet",
        "user_timezone": "Asia/Kolkata",
        "user_slot_start_at": "10:30 AM",
        "user_slot_end_at": "11:00 AM",
        "org_timezone_snapshot": "Asia/Kolkata",
        "org_slot_start_at": "10:30 AM",
        "org_slot_end_at": "11:00 AM",
        "booking_id": "bk_abc123",
    }
    subject, body = render_booking_confirmation_customer(booking_details)
    await send_test_email(recipient, subject, body, channel="booking_confirmation")
    
    # Test 6: Booking Confirmation - Admin
    print("6. Sending Booking Confirmation (Admin)...")
    subject, body = render_booking_confirmation_admin(booking_details, frontend_url="http://localhost:3002")
    await send_test_email(recipient, subject, body, channel="booking_confirmation")
    
    # Test 7: Booking Cancellation
    print("7. Sending Booking Cancellation...")
    subject, body = render_booking_cancellation("Priyan Thakur", booking_details)
    await send_test_email(recipient, subject, body, channel="booking_cancellation")
    
    print()
    print("=" * 60)
    print("All test emails sent!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())