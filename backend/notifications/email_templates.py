"""
Friends Turf — Official Branded Responsive HTML Email Templates
Adheres strictly to Friends Turf Brand Design Guidelines (Emerald #059669, Slate #0F172A).
"""
from django.conf import settings
from accounts.settings_helper import BusinessSettingsHelper


def get_brand_assets():
    comp = BusinessSettingsHelper.get_company_settings()
    return {
        "logo_url": comp.get("logo_url", "https://friendsturf.com/logo.png"),
        "brand_name": comp.get("name", "Friends Turf"),
        "tagline": comp.get("tagline", "PLAY HARD. BOOK DIRECT. OWN THE PITCH."),
        "company_address": comp.get("address", "Near Sirupooluvapatti, Kamatchepuram, Tiruppur, TN 641603 (RTO Office Backside)"),
        "support_email": comp.get("support_email", comp.get("email", "support@friendsturf.com")),
        "support_phone": comp.get("phone", "+91 93619 89494"),
        "website_url": comp.get("website", "https://friendsturf.com"),
        "primary_color": "#059669",
        "primary_hover": "#047857",
        "dark_color": "#0F172A",
        "bg_subtle": "#F8FAFC",
    }


# Fallback dictionary for backwards compatibility
BRAND_ASSETS = get_brand_assets()


def _base_email_wrapper(title: str, preheader: str, content_html: str) -> str:
    """Universal branded email wrapper with header logo and compliance footer."""
    assets = get_brand_assets()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{ margin: 0; padding: 0; background-color: #F1F5F9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; }}
    table {{ border-collapse: collapse; }}
    img {{ border: 0; outline: none; text-decoration: none; }}
  </style>
</head>
<body style="margin: 0; padding: 24px 0; background-color: #F1F5F9;">
  <span style="display: none !important; visibility: hidden; mso-hide: all; font-size: 1px; color: #F1F5F9; line-height: 1px; max-height: 0px; max-width: 0px; opacity: 0; overflow: hidden;">
    {preheader}
  </span>

  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
    <tr>
      <td align="center">
        <!-- Main Email Container (600px Max) -->
        <table role="presentation" width="100%" style="max-width: 600px; background-color: #FFFFFF; border-radius: 20px; overflow: hidden; box-shadow: 0 4px 20px rgba(15, 23, 42, 0.08);" border="0" cellspacing="0" cellpadding="0">
          
          <!-- Header Banner -->
          <tr>
            <td style="background-color: {assets['primary_color']}; padding: 32px 36px; text-align: center;">
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td align="center">
                    <div style="display: inline-block; background-color: rgba(255, 255, 255, 0.2); padding: 8px 16px; border-radius: 12px; margin-bottom: 8px;">
                      <span style="color: #FFFFFF; font-weight: 900; font-size: 20px; letter-spacing: 1px;">{assets['brand_name'].upper()}</span>
                    </div>
                    <div style="color: #ECFDF5; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px;">
                      FIFA Pro Sports Complex • Tiruppur
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Dynamic Body Content -->
          <tr>
            <td style="padding: 36px 36px 28px 36px;">
              {content_html}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color: {assets['bg_subtle']}; padding: 28px 36px; border-top: 1px solid #E2E8F0; text-align: center;">
              <p style="margin: 0 0 6px 0; font-size: 12px; font-weight: 700; color: {assets['dark_color']};">
                {assets['brand_name']} Sports Arena
              </p>
              <p style="margin: 0 0 12px 0; font-size: 11px; color: #64748B; line-height: 1.5;">
                {assets['company_address']}<br>
                Support: {assets['support_phone']} | {assets['support_email']}
              </p>
              <p style="margin: 0; font-size: 10px; color: #94A3B8;">
                © 2026 {assets['brand_name']} Arena LLP. All rights reserved. Optical gate passes are cryptographically verified.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def render_booking_confirmation_email(booking, qr_image_src: str = "cid:match_pass_qr", frontend_url: str = "") -> str:
    """
    Branded Match Pass & Booking Confirmation HTML email.
    Includes high-res QR code, match details, player name, venue specs, and directions.
    """
    if not frontend_url:
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:5173").rstrip("/")

    match_date = booking.date.strftime("%A, %d %b %Y")
    timings = f"{booking.start_time.strftime('%I:%M %p')} - {booking.end_time.strftime('%I:%M %p')}"
    booking_id = booking.booking_id
    turf_name = booking.turf.name
    turf_location = booking.turf.location
    surface_spec = getattr(booking.turf, "surface_spec", "FIFA Certified Pro Turf")
    lighting_spec = getattr(booking.turf, "lighting_spec", "500 Lux Anti-Glare LED")
    customer_name = booking.customer.full_name or booking.customer.email or "Squad Lead"
    amount_paid = float(booking.amount_paid or 0)
    balance_due = float(booking.balance_due or 0)
    total_amount = float(booking.final_amount or booking.total_amount or (amount_paid + balance_due))

    has_balance = balance_due > 0
    img_src = qr_image_src or "cid:match_pass_qr"

    if has_balance:
        qr_section_html = f"""
        <div style="text-align: center; margin-bottom: 24px; padding: 20px; background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 16px;">
          <div style="display: inline-block; background-color: #FFFBEB; border: 1px solid #FDE68A; color: #B45309; font-size: 10px; font-weight: 800; text-transform: uppercase; padding: 3px 10px; border-radius: 9999px; letter-spacing: 0.5px; margin-bottom: 12px;">
            Advance Deposit Confirmed • ₹{balance_due:,.2f} Due at Venue
          </div>
          <span style="font-size: 11px; font-weight: 800; text-transform: uppercase; color: #0F172A; display: block; letter-spacing: 1px; margin-bottom: 10px;">
            OPTICAL GATE SCANNER PASS
          </span>
          <img src="{img_src}" alt="Match Pass QR #{booking_id}" width="190" height="190" style="display: block; margin: 0 auto; border-radius: 12px; border: 1px solid #E2E8F0; background-color: #FFFFFF; padding: 6px;" />
          <span style="font-size: 12px; font-weight: 800; color: #059669; display: block; margin-top: 10px; font-family: monospace; letter-spacing: 1px;">
            PASS CODE: {booking_id}
          </span>
          <span style="font-size: 10px; color: #64748B; display: block; margin-top: 4px;">
            Present at venue reception desk to settle remaining balance & activate turnstile admission
          </span>
        </div>
        """
        cta_button_text = f"Settle ₹{balance_due:,.2f} Online & View Pass →"
    else:
        qr_section_html = f"""
        <div style="text-align: center; margin-bottom: 24px; padding: 20px; background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 16px;">
          <span style="font-size: 11px; font-weight: 800; text-transform: uppercase; color: #0F172A; display: block; letter-spacing: 1px; margin-bottom: 12px;">
            OPTICAL GATE SCANNER CODE
          </span>
          <img src="{img_src}" alt="Match Pass QR #{booking_id}" width="190" height="190" style="display: block; margin: 0 auto; border-radius: 12px; border: 1px solid #E2E8F0; background-color: #FFFFFF; padding: 6px;" />
          <span style="font-size: 12px; font-weight: 800; color: #059669; display: block; margin-top: 10px; font-family: monospace; letter-spacing: 1px;">
            PASS CODE: {booking_id}
          </span>
          <span style="font-size: 10px; color: #94A3B8; display: block; margin-top: 4px;">
            Hold 4-6 inches from turnstile scanner for gate admission
          </span>
        </div>
        """
        cta_button_text = "Open Digital Match Pass →"

    payment_status_badge = (
        '<span style="display: inline-block; background-color: #ECFDF5; border: 1px solid #A7F3D0; color: #059669; font-size: 11px; font-weight: 800; text-transform: uppercase; padding: 4px 10px; border-radius: 6px;">100% Fully Settled</span>'
        if not has_balance
        else f'<span style="display: inline-block; background-color: #FFFBEB; border: 1px solid #FDE68A; color: #B45309; font-size: 11px; font-weight: 800; text-transform: uppercase; padding: 4px 10px; border-radius: 6px;">₹{balance_due:,.2f} Due at Venue</span>'
    )

    content = f"""
      <div style="text-align: center; margin-bottom: 24px;">
        <span style="background-color: #ECFDF5; border: 1px solid #A7F3D0; color: #059669; font-size: 11px; font-weight: 800; text-transform: uppercase; padding: 4px 14px; border-radius: 9999px; letter-spacing: 1px;">
          ✓ Booking Confirmed & Locked
        </span>
        <h1 style="color: #0F172A; font-size: 26px; font-weight: 900; margin: 12px 0 4px 0; letter-spacing: -0.5px;">
          Official Match Pass
        </h1>
        <p style="color: #64748B; font-size: 13px; margin: 0;">
          Reserved for <strong style="color: #0F172A;">{customer_name}</strong> • Present this pass at the gate turnstile scanner.
        </p>
      </div>

      <!-- Match Details Box -->
      <table role="presentation" width="100%" style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 16px; margin-bottom: 20px;" border="0" cellspacing="0" cellpadding="16">
        <tr>
          <td style="border-bottom: 1px solid #E2E8F0;">
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
              <tr>
                <td>
                  <span style="font-size: 10px; font-weight: 800; text-transform: uppercase; color: #94A3B8; display: block; letter-spacing: 0.5px;">PITCH ARENA</span>
                  <span style="font-size: 18px; font-weight: 900; color: #0F172A; display: block; margin-top: 2px;">{turf_name}</span>
                  <span style="font-size: 12px; color: #64748B; display: block; margin-top: 3px;">📍 {turf_location}</span>
                </td>
                <td align="right" valign="top">
                  <span style="font-family: monospace; font-size: 13px; font-weight: 800; color: #059669; background-color: #ECFDF5; padding: 4px 8px; border-radius: 6px; border: 1px solid #A7F3D0;">
                    #{booking_id}
                  </span>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="border-bottom: 1px solid #E2E8F0;">
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
              <tr>
                <td width="50%" valign="top">
                  <span style="font-size: 10px; font-weight: 800; text-transform: uppercase; color: #94A3B8; display: block; letter-spacing: 0.5px;">MATCH DATE</span>
                  <span style="font-size: 14px; font-weight: 800; color: #0F172A; display: block; margin-top: 2px;">📅 {match_date}</span>
                </td>
                <td width="50%" valign="top">
                  <span style="font-size: 10px; font-weight: 800; text-transform: uppercase; color: #94A3B8; display: block; letter-spacing: 0.5px;">SLOT TIME</span>
                  <span style="font-size: 14px; font-weight: 800; color: #0F172A; display: block; margin-top: 2px;">⏰ {timings}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
              <tr>
                <td width="50%" valign="top">
                  <span style="font-size: 10px; font-weight: 800; text-transform: uppercase; color: #94A3B8; display: block; letter-spacing: 0.5px;">SPECS</span>
                  <span style="font-size: 12px; font-weight: 600; color: #475569; display: block; margin-top: 2px;">⚡ {lighting_spec}</span>
                  <span style="font-size: 12px; font-weight: 600; color: #475569; display: block; margin-top: 1px;">🌱 {surface_spec}</span>
                </td>
                <td width="50%" valign="top">
                  <span style="font-size: 10px; font-weight: 800; text-transform: uppercase; color: #94A3B8; display: block; letter-spacing: 0.5px;">PAYMENT STATUS</span>
                  <div style="margin-top: 4px;">
                    {payment_status_badge}
                  </div>
                  <span style="font-size: 11px; color: #64748B; display: block; margin-top: 4px;">Paid: <strong>₹{amount_paid:,.2f}</strong> / Total: <strong>₹{total_amount:,.2f}</strong></span>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>

      <!-- Turnstile QR Pass Section -->
      {qr_section_html}

      <!-- Action Buttons Row -->
      <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 16px;">
        <tr>
          <td align="center">
            <table role="presentation" border="0" cellspacing="0" cellpadding="0">
              <tr>
                <td align="center" style="border-radius: 12px; background-color: #059669;">
                  <a href="{frontend_url}/confirmation/{booking_id}" target="_blank" style="display: inline-block; background-color: #059669; color: #FFFFFF; font-weight: 800; font-size: 14px; text-decoration: none; padding: 14px 28px; border-radius: 12px; box-shadow: 0 4px 14px rgba(5, 150, 105, 0.35);">
                    {cta_button_text}
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>

      <!-- Secondary Links -->
      <div style="text-align: center; margin-top: 12px;">
        <a href="{frontend_url}/print/pass/{booking_id}?autoprint=true" target="_blank" style="font-size: 12px; font-weight: 700; color: #059669; text-decoration: none; margin: 0 10px;">
          🖨️ Print / Save PDF
        </a>
        <span style="color: #CBD5E1;">•</span>
        <a href="{frontend_url}/print/receipt/{booking_id}?autoprint=true" target="_blank" style="font-size: 12px; font-weight: 700; color: #0F172A; text-decoration: none; margin: 0 10px;">
          🧾 GST Tax Invoice
        </a>
        <span style="color: #CBD5E1;">•</span>
        <a href="https://maps.google.com/?q={turf_name}+{turf_location}" target="_blank" style="font-size: 12px; font-weight: 700; color: #475569; text-decoration: none; margin: 0 10px;">
          📍 Directions
        </a>
      </div>
    """

    return _base_email_wrapper(
        title=f"Match Pass Confirmed #{booking_id} — Friends Turf",
        preheader=f"Your pitch at {turf_name} is confirmed for {match_date} ({timings}). Pass #{booking_id}.",
        content_html=content,
    )



def render_payment_receipt_email(receipt_data: dict) -> str:
    """Branded GST Tax Invoice & Cash Receipt HTML email."""
    receipt_no = receipt_data.get("receipt_number", "REC-26-0000")
    booking_id = receipt_data.get("booking", {}).get("booking_id", "")
    customer_name = receipt_data.get("customer", {}).get("name", "Valued Player")
    turf_name = receipt_data.get("booking", {}).get("turf_name", "Turf Arena")
    fin = receipt_data.get("financial_summary", {})
    final_amount = fin.get("final_amount", 0.0)
    tax_amount = fin.get("gst_amount", 0.0)
    base_amount = fin.get("base_subtotal", 0.0)

    content = f"""
      <div style="text-align: center; margin-bottom: 24px;">
        <span style="background-color: #ECFDF5; border: 1px solid #A7F3D0; color: #059669; font-size: 11px; font-weight: 800; text-transform: uppercase; padding: 4px 12px; border-radius: 9999px;">
          Official Tax Invoice
        </span>
        <h1 style="color: #0F172A; font-size: 24px; font-weight: 900; margin: 12px 0 4px 0;">
          Payment Received: ₹{final_amount:,.2f}
        </h1>
        <p style="color: #64748B; font-size: 13px; margin: 0;">
          Invoice No: <strong style="font-family: monospace; color: #0F172A;">{receipt_no}</strong> | Booking: <strong>{booking_id}</strong>
        </p>
      </div>

      <!-- Financial Table -->
      <table role="presentation" width="100%" style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 16px; margin-bottom: 24px;" border="0" cellspacing="0" cellpadding="12">
        <tr style="border-bottom: 1px solid #E2E8F0;">
          <td style="font-size: 12px; color: #64748B;">Match Slot Reservation ({turf_name})</td>
          <td align="right" style="font-size: 13px; font-weight: 700; color: #0F172A;">₹{base_amount:,.2f}</td>
        </tr>
        <tr style="border-bottom: 1px solid #E2E8F0;">
          <td style="font-size: 12px; color: #64748B;">GST (18% - SAC 999651)</td>
          <td align="right" style="font-size: 13px; font-weight: 700; color: #0F172A;">₹{tax_amount:,.2f}</td>
        </tr>
        <tr>
          <td style="font-size: 14px; font-weight: 900; color: #0F172A;">Total Paid</td>
          <td align="right" style="font-size: 16px; font-weight: 900; color: #059669;">₹{final_amount:,.2f}</td>
        </tr>
      </table>

      <!-- CTA Button -->
      <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
        <tr>
          <td align="center">
            <a href="https://friendsturf.com/print/receipt/{booking_id}?autoprint=true" style="display: inline-block; background-color: #0F172A; color: #FFFFFF; font-weight: 800; font-size: 13px; text-decoration: none; padding: 12px 28px; border-radius: 12px;">
              Download Official GST Tax Invoice PDF →
            </a>
          </td>
        </tr>
      </table>
    """

    return _base_email_wrapper(
        title=f"GST Tax Invoice {receipt_no} — Friends Turf",
        preheader=f"Official receipt for payment of ₹{final_amount:,.2f} on Booking #{booking_id}.",
        content_html=content,
    )


def render_otp_verification_email(
    user, otp_code: str, valid_minutes: int = 10, ip_address: str = ""
) -> str:
    """
    Branded responsive HTML Email for Password Reset OTP.
    Includes prominent 6-digit code box, stadium security badge, and expiration timer.
    """
    player_name = user.full_name or user.email or "Player"
    # Format OTP with spacing e.g. "8 4 9 • 2 0 1"
    spaced_otp = " &nbsp; ".join(list(otp_code))

    content = f"""
      <div style="text-align: center; margin-bottom: 24px;">
        <span style="background-color: #ECFDF5; border: 1px solid #A7F3D0; color: #059669; font-size: 11px; font-weight: 800; text-transform: uppercase; padding: 4px 14px; border-radius: 9999px; letter-spacing: 1px;">
          🔒 Player Account Security
        </span>
        <h1 style="color: #0F172A; font-size: 26px; font-weight: 900; margin: 12px 0 4px 0; letter-spacing: -0.5px;">
          Password Reset Verification
        </h1>
        <p style="color: #64748B; font-size: 13px; margin: 0;">
          Hello <strong style="color: #0F172A;">{player_name}</strong>, use the one-time password below to verify your password reset request.
        </p>
      </div>

      <!-- High-Impact 6-Digit OTP Box -->
      <div style="text-align: center; margin-bottom: 24px; padding: 24px; background: linear-gradient(135deg, #F8FAFC 0%, #ECFDF5 100%); border: 2px solid #A7F3D0; border-radius: 20px;">
        <span style="font-size: 11px; font-weight: 800; text-transform: uppercase; color: #059669; display: block; letter-spacing: 1.5px; margin-bottom: 10px;">
          ONE-TIME PASSCODE (OTP)
        </span>
        <div style="font-family: 'Courier New', Courier, monospace; font-size: 36px; font-weight: 900; color: #0F172A; letter-spacing: 8px; background-color: #FFFFFF; display: inline-block; padding: 12px 28px; border-radius: 14px; border: 1px solid #CBD5E1; box-shadow: 0 4px 12px rgba(5, 150, 105, 0.1);">
          {otp_code}
        </div>
        <span style="font-size: 12px; font-weight: 700; color: #64748B; display: block; margin-top: 12px;">
          ⏳ Valid for <strong>{valid_minutes} minutes</strong> only
        </span>
      </div>

      <!-- Security Guidance Card -->
      <table role="presentation" width="100%" style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 16px; margin-bottom: 24px;" border="0" cellspacing="0" cellpadding="16">
        <tr>
          <td>
            <span style="font-size: 12px; font-weight: 800; color: #0F172A; display: block; margin-bottom: 6px;">
              🛡️ Security Advice & Guard Protocol:
            </span>
            <ul style="margin: 0; padding-left: 18px; font-size: 12px; color: #64748B; line-height: 1.6;">
              <li>Never share this OTP with anyone. Friends Turf staff will <strong>never</strong> ask for your verification code.</li>
              <li>If you did not initiate this password reset request, please ignore this email or contact our campus help desk immediately.</li>
              {f'<li>Request IP Origin: <code style="font-family: monospace; color: #0F172A;">{ip_address}</code></li>' if ip_address else ''}
            </ul>
          </td>
        </tr>
      </table>

      <!-- Campus Support -->
      <div style="text-align: center; font-size: 11px; color: #94A3B8;">
        Friends Turf Arena • Tiruppur, TN • 24/7 Security Hotline: +91 93619 89494
      </div>
    """

    return _base_email_wrapper(
        title=f"Your OTP Code: {otp_code} — Friends Turf",
        preheader=f"Your Friends Turf one-time password is {otp_code}. Valid for {valid_minutes} minutes.",
        content_html=content,
    )

