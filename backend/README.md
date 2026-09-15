# ⚽ Friends Turf - Backend API & Services

RESTful Backend and Business Engines for **Friends Turf** Turf Booking & Operations Management System, built with **Django 6.1.1**, **Django REST Framework**, and **MongoDB**.

---

## 🚀 Key Modules & Architecture

The backend is composed of 14 modular applications:

- **`accounts`**: Custom User model, JWT authentication, Customer/Staff profiles, RBAC permissions (`IsAdmin`, `IsStaffOrAdmin`).
- **`turfs`**: Turf venues (*The Champions Arena*, *Legends Box Cricket*, *Strikers Dome*), amenities, and time slots.
- **`bookings`**: Booking engine with 5-minute temporary slot reservation lock, multi-slot bookings, atomic state machine, cancellation with auto-refund to wallet, and rescheduling.
- **`payments`**: Payment records, refund records, and simulated payment gateway with full, 50% partial advance, and pay-at-venue models.
- **`pricing`**: Dynamic Pricing Engine calculating surge hours, morning discounts, and weekend rates.
- **`promotions`**: Discount coupons (Percentage & Flat cash), referral tracking, and invite bonuses.
- **`memberships`**: Silver, Gold, and Platinum tier plans with booking priority and discounts.
- **`wallet`**: Customer wallet ledger and loyalty points converter (1 point per ₹10 spent, 1 point = ₹1).
- **`qr_system`**: Tamper-proof JWT QR ticket generator and staff scanner validation service with duplicate entry prevention.
- **`reviews`**: Customer ratings with ground and staff breakdown, feedback moderation, and manager replies.
- **`notifications`**: In-app notification dispatching.
- **`maintenance`**: Pitch maintenance scheduler with automatic slot blocking.
- **`reports`**: Admin KPIs, 7-day revenue trend, peak hours analysis, turf utilization, and daily audit summary.
- **`audit`**: System security and action audit trail.

---

## 🛠️ Installation & Setup

### Prerequisites
- Python 3.12+
- MongoDB 7.0+ (running locally on port `27017`)

### 1. Virtual Environment & Dependencies
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
venv\Scripts\activate      # On Windows
# source venv/bin/activate # On macOS / Linux

# Install dependencies
pip install -r requirements.txt
```

### 2. Database Migrations
```bash
python manage.py migrate
```

### 3. Seed Database
```bash
python manage.py seed_data
```
This populates:
- 3 Turfs with 8 facilities and 8 days of time slots.
- Dynamic pricing rules (Weekend surge, night floodlight, morning discount).
- Active coupons (`WELCOME100`, `TURF20`, `FRIENDS10`).
- Membership tiers (Silver, Gold, Platinum).
- Pre-configured demo accounts.

### 4. Start Server
```bash
python manage.py runserver 127.0.0.1:8000
```

API Root: `http://127.0.0.1:8000/api/`

---

## 🔑 Demo Credentials

| Role | Email | Password |
|---|---|---|
| **Admin** | `admin@friendsturf.com` | `admin123` |
| **Staff** | `staff@friendsturf.com` | `staff123` |
| **Customer** | `customer@friendsturf.com` | `customer123` |
| **Customer 2** | `rahul@friendsturf.com` | `rahul123` |

---

## 📡 API Endpoint Overview

- `POST /api/auth/register/` - Register new user
- `POST /api/auth/login/` - JWT login (returns access + refresh tokens)
- `GET /api/turfs/` - List all active turfs
- `GET /api/turfs/{id}/availability/?date=YYYY-MM-DD` - Dynamic pricing slot matrix
- `POST /api/bookings/lock/` - 5-min temporary slot lock
- `POST /api/bookings/book/` - Finalize booking & generate QR match pass
- `POST /api/qr/scan/` - Staff QR match ticket validation & gate check-in
- `POST /api/bookings/{id}/cancel/` - Cancel booking with instant wallet credit
- `GET /api/reports/dashboard/` - Admin KPI metrics & revenue charts
- `GET /api/audit/` - System audit logs
