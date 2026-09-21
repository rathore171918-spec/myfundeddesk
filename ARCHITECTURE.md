# MyFundedDesk Architecture Documentation

## Tech Stack

### Language & Framework
- **Python 3.11** - Core language
- **FastAPI** (0.141.1) - Modern, fast web framework for building APIs
- **Uvicorn** (0.52.4) - ASGI server

### Database & ORM
- **SQLAlchemy 2.0.52** - ORM and database toolkit
- **SQLite** - Default database (file-based, `propfirm.db`)
- **PostgreSQL** - Supported via `DATABASE_URL` environment variable
- **pg8000** - PostgreSQL driver for SQLAlchemy
- Migrations handled via raw `ALTER TABLE` statements at startup

### Templates & Rendering
- **Jinja2** - Template engine for HTML responses
- Custom Jinja filter `inr_format` for Indian numbering system

### Authentication & Security
- **Bcrypt** (5.0.0) - Password hashing
- **Custom HMAC-based session tokens** - Signed cookies for user sessions
- **Google OAuth** - SSO integration
- **Session concurrency control** - `session_version` field to prevent token reuse attacks

### Payments
- **Razorpay** (2.0.1) - Payment gateway for Indian Rupees
- HMAC SHA256 signature verification for payment callbacks

### Email Services
- **Resend** - Transactional email service
- Fallback: prints to console if no API key configured

### Market Data & Trading Engine
- **yfinance** (0.2.44) - Yahoo Finance API for historical data
- **smartapi-python** - Angel One live data feed
- **pyotp** - TOTP for Angel One authentication
- **websocket-client** - WebSocket connectivity
- Custom **Black-Scholes option pricing engine**
- Proprietary rule evaluation engine (daily loss, max drawdown, profit targets)

### Utilities
- **python-multipart** - Form data handling
- **logzero** - Logging simplification
- **httpx** - Async HTTP client

---

## Folder Structure

```
/myfundeddesk/
├── app/                          # Main FastAPI application
│   ├── __init__.py               # Empty package init
│   ├── config.py                 # Environment variables & defaults
│   ├── database.py               # SQLAlchemy engine, session, Base
│   ├── main.py                   # Entry point: FastAPI app, startup events, routers
│   ├── models.py                 # SQLAlchemy model definitions
│   ├── routers/                  # API route modules
│   │   ├── __init__.py
│   │   ├── auth.py               # Login, register, verification, Google OAuth
│   │   ├── challenges.py         # Account listing & evaluation detail
│   │   ├── trading.py            # Trade execution, PnL, account state
│   │   ├── dashboard.py          # User dashboard, payouts, notifications
│   │   ├── billing.py            # Orders & payment history
│   │   ├── certificates.py       # View earned certificates
│   │   ├── admin_sim.py          # Admin simulator for testing accounts
│   │   ├── admin_dashboard.py    # Admin UI & API actions
│   │   ├── profile.py            # User profile & deletion requests
│   │   ├── store.py              # Buy challenges, Razorpay payment flow
│   │   ├── features.py           # Feature widgets, chat, dynamic pages
│   │   ├── landing.py            # Landing page, contact forms, dynamic routes
│   │   └── affiliate.py          # Referral program dashboard
│   ├── engine/                   # Business logic engines
│   │   ├── __init__.py
│   │   ├── prop_rules.py         # Core evaluation rules (daily/max drawdown, profit targets)
│   │   ├── market_data.py        # Market prices, candles, PnL calculation
│   │   ├── options_engine.py     # Black-Scholes option pricing
│   │   └── razorpay_client.py    # Razorpay API wrapper
│   └── email_service.py          # Resend email sending
├── tests/                        # Unit tests
├── data/                         # Static data (pages.json)
├── requirements.txt              # Python dependencies
├── Procfile                      # Render deployment config
├── render.yaml                   # Render.com deployment config
└── .gitignore
```

### Key Router Modules

| Router | Purpose |
|--------|---------|
| `auth` | Authentication (login/register/verify/forgot/reset), Google OAuth |
| `challenges` | Challenge package listings, account evaluation details |
| `trading` | Live trade execution, PnL calculation, account state |
| `dashboard` | User dashboard, payout requests, notifications, chat |
| `billing` | Order history, payment receipts |
| `certificates` | View earned challenge pass certificates |
| `admin_sim` | Admin simulator (pass/breach/reset accounts) |
| `admin_dashboard` | Full admin UI, user/account management, settings |
| `profile` | User profile updates, account deletion requests |
| `store` | Buy challenge packages, Razorpay payment flow |
| `features` | Feature widgets (chat, leaderboard, support, coupons) |
| `landing` | Landing page, contact forms, dynamic page routes |
| `affiliate` | Referral program dashboard |

### Engine Sub-modules

| Module | Purpose |
|--------|---------|
| `prop_rules.py` | Core prop firm evaluation rules (daily loss, max drawdown, profit targets, phase advancement) |
| `market_data.py` | Market price engine with live feeds, candles, options pricing |
| `options_engine.py` | Black-Scholes option pricing model |
| `razorpay_client.py` | Razorpay API integration (order creation, signature verification) |

---

## Key Features/Modules

### Authentication & Onboarding
- Email/password login with bcrypt hashing
- Email verification via 6-digit OTP (Resend)
- Password reset flow with OTP
- Google OAuth Single Sign-On
- Account deletion request flow with cancellation

### Challenge Evaluation System
- **1-Step, 2-Step, and Instant evaluation models**
- Configurable profit targets (8%/10%), daily max loss (3%/4%), total max loss (6%/8%/10%)
- Trailing daily loss & trailing max drawdown tracking
- Minimum trading days requirement before profit target can be hit
- Automatic phase advancement (Phase 1 → Phase 2 → Funded)
- Certificate generation on phase pass/full certification

### Trading Engine
- Live trade execution with margin checks
- Position stacking limits (max 3 open same-direction trades)
- Stop loss & take profit enforcement
- Floating PnL calculation with fees (STT + charges)
- Weekend holding rule enforcement
- Inactivity detection (21-day timeout)
- Minimum trade duration check (60 seconds)

### Payment & Billing
- Razorpay integration for INR payments
- Coupon code system (SAVE20, FUNDEDDESK20, TRADER20, LAUNCH50, HALFPRICE, LAUNCH10, WELCOME10)
- Order records with Razorpay payment links
- Payout requests from funded accounts

### Admin System
- Separate admin login with OTP verification
- User management (create/delete)
- Account management (reset, pass phase, force breach)
- Settings customization (admin credentials, landing page settings)
- Broadcast notifications to all users
- Package management (create/update pricing)

### Features & Widgets
- Market heatmap, news, economic calendar
- Leaderboard (top funded traders)
- Affiliate dashboard with referral links
- Support chat system
- Customizable coupon codes
- Giveaway participation
- Account comparison tables
- Privacy settings

---

## API Routes & Endpoints

### Auth Routes
- `GET /login` - Login page
- `POST /login` - Handle login
- `GET /register` - Register page
- `POST /register` - Handle registration
- `GET /logout` - Logout
- `GET /verify-email` - Verification page
- `POST /verify-email` - Handle verification
- `GET /forgot-password` - Forgot password page
- `POST /forgot-password` - Handle forgot password
- `GET /reset-password` - Reset password page
- `POST /reset-password` - Handle reset password
- `GET /auth/google/login` - Google OAuth start
- `GET /auth/google/callback` - Google OAuth callback

### Trading Routes
- `GET /trading` - Trading terminal page
- `POST /api/trade/open` - Open a new trade
- `POST /api/trade/close/{trade_id}` - Close a trade
- `GET /api/account/{account_id}/state` - Get account state + positions + history
- `GET /api/market/prices` - Get live market prices
- `GET /api/market/candles/{symbol}` - Get candle data
- `GET /api/options/{symbol}` - Get options chain

### Dashboard Routes
- `GET /dashboard` - User dashboard
- `POST /api/payout/request` - Request payout (funded accounts only)
- `POST /api/notifications/{notif_id}/dismiss` - Dismiss notification
- `GET /api/notifications` - Get user notifications
- `GET /accounts/{account_type}` - Filter accounts by type

### Billing Routes
- `GET /orders` / `GET /billing` - Order history page
- `GET /api/orders/{order_id}/receipt` - View payment receipt

### Store Routes
- `GET /buy-challenge` - Buy challenge page
- `POST /api/payment/create-order` - Create Razorpay order
- `POST /api/payment/verify` - Verify Razorpay payment callback
- `POST /buy-challenge/checkout` - Checkout flow

### Admin Routes
- `GET /admin/login` - Admin login page
- `POST /admin/login` - Handle admin login
- `GET /admin/otp` - Admin OTP verification page
- `POST /admin/otp` - Handle OTP verification
- `GET /admin/logout` - Admin logout
- `GET /admin` - Admin dashboard
- Various `/admin/api/*` endpoints for account/user/position/settings management

### Feature Routes
- `GET /feature/{name}` - Feature widget page
- `POST /api/chat` - Send chat message
- `POST /api/chat/clear` - Clear chat history
- `GET /api/chat` - Get chat messages
- `GET /api/chat/messages` - Get messages for user

### Landing Routes
- `GET /` - Landing page
- `GET /rules` - Rules page
- `GET /{slug}` - Dynamic page route
- `POST /api/contact` - Contact form submission

### Affiliate Routes
- `GET /ib-program` - Referral program dashboard

---

## Database Schema & Models

### Core Models

| Model | Key Fields |
|-------|-----------|
| **User** | `id`, `username`, `email`, `hashed_password`, `plain_password`, `full_name`, `is_email_verified`, `verification_code`, `is_super_admin`, `avatar_text`, `referral_code`, `deletion_requested`, `deletion_reason`, `deletion_requested_at`, `session_version`, `created_at` |
| **ChallengePackage** | `id`, `name`, `model_type` ("1-Step"/"2-Step"/"Instant"), `account_size`, `profit_target_p1`, `profit_target_p2`, `max_daily_loss`, `max_total_loss`, `min_trading_days`, `leverage`, `price`, `profit_split`, `description`, `is_popular` |
| **TradingAccount** | `id`, `account_number`, `user_id`, `package_id`, `model_type`, `platform`, `initial_balance`, `current_balance`, `current_equity`, `daily_starting_equity`, `highest_recorded_equity`, `highest_daily_equity`, `highest_account_equity`, `soft_breaches_stacking`, `soft_breaches_duration`, `soft_breaches_sl`, `last_trade_time`, `payout_cycle_start`, `profit_days_count`, `total_payout_profit`, `best_day_profit`, `phase` ("Phase 1"/"Phase 2"/"Funded"), `status` ("ACTIVE"/"PASSED"/"BREACHED"), `breach_reason` |
| **TradePosition** | `id`, `ticket`, `account_id`, `symbol`, `order_type`, `volume_lots`, `open_price`, `close_price`, `current_price`, `stop_loss`, `take_profit`, `sl_penalized`, `pnl`, `status` ("OPEN"/"CLOSED"), `open_time`, `close_time` |
| **Order** | `id`, `order_id`, `user_id`, `package_name`, `account_size`, `model_type`, `platform`, `amount_paid`, `payment_method`, `razorpay_order_id`, `razorpay_payment_id`, `status`, `created_at` |
| **Certificate** | `id`, `cert_id`, `user_id`, `account_id`, `trader_name`, `account_size`, `challenge_type`, `phase_passed`, `profit_achieved`, `issue_date`, `verification_hash` |
| **AffiliateReferral** | `id`, `referrer_id`, `referred_name`, `referred_email`, `challenge_purchased`, `order_amount`, `commission_earned`, `status` ("PAID"/"PENDING"), `created_at` |
| **Notification** | `id`, `user_id`, `message`, `type` ("info"), `is_read`, `created_at` |
| **AppSetting** | `id`, `key`, `value` (for landing page customization) |
| **ChatMessage** | `id`, `user_id`, `is_admin`, `message`, `created_at` |
| **DynamicPage** | `id`, `slug`, `title`, `content`, `is_published` |
| **ContactMessage** | `id`, `name`, `email`, `subject`, `message`, `created_at` |

---

## Environment Variables (.env Structure)

Create a `.env` file in the project root (or set these in your deployment environment):

```
# Database
DATABASE_URL=sqlite:///propfirm.db     # or postgres://user:pass@host/db

# Security
SECRET_KEY=myfundeddesk_super_secure_jwt_session_secret_2026    # Random secure key for session tokens
SESSION_COOKIE_NAME=myfundeddesk_session

# Razorpay Payments
RAZORPAY_KEY_ID=rzp_test_TSjqPvwaeUHguA
RAZORPAY_KEY_SECRET=DxYT3GsAqCAN4JIzpcvjU7fN

# Email Service (Resend)
RESEND_API_KEY=                                # Required for actual emails
RESEND_FROM_EMAIL=MyFundedDesk <auth@myfundeddesk.com>

# Google OAuth
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://myfundeddesk.com/auth/google/callback

# Angel One Live Data (Optional)
ANGEL_API_KEY=3EWlZO4e
ANGEL_CLIENT_CODE=G140240
ANGEL_PIN=5012
ANGEL_TOTP_KEY=FMKOE2BD2DHDRUPAI4AV3BWNKU
```

*Note: All variables have defaults in `config.py`. The `.env` file is excluded from git via `.gitignore`.*

---

## Incomplete Features, TODOs, & Known Issues

### Code Comments & TODOs Found

1. **`app/main.py:196`** - `ALTER TABLE users ADD COLUMN plain_password VARCHAR(255)` - Runs every startup, indicates column may be missing from older databases

2. **`app/main.py:218-225`** - Auto-delete users after 15 days with `deletion_requested=True` - Background cleanup task

3. **`app/engine/market_data.py:189-284`** - Angel One live data worker requires API credentials; currently uses hardcoded defaults

4. **`app/engine/market_data.py:204-207`** - Angel One auth defaults: `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_KEY`

5. **`app/routers/admin_dashboard.py:16-18`** - Hardcoded admin credentials: `ADMIN_USERNAME = "myfundeddesk@gmail.com"`, `ADMIN_PASSWORD = "Myfundeddesk@2004"`

6. **`app/routers/admin_dashboard.py:283-291`** - Admin settings update rewrites the source file directly (not ideal)

7. **`app/routers/auth.py:72-73`** - Fallback: creates user without `verification_code`/`avatar_text` on exception

8. **`app/engine/prop_rules.py:126-127`** - Weekend rule logic has complex timezone handling that may have edge cases

9. **`app/engine/prop_rules.py:44-49`** - Instant fund SL penalty: only triggers after 60 seconds, marks `sl_penalized=True`

10. **`app/engine/market_data.py:321-356`** - Options candle generation relies on `calculate_option_price_live()` which may return `None`

11. **`app/main.py:295-298`** - Development server runs with `reload=False`; production may need reload config

12. **`app/data/pages.json`** - Empty `{}` by default; dynamic pages require manual population

13. **`app/main.py:93-95`** - Global exception handler returns full tracebacks to client (information leak in production)

14. **`app/engine/prop_rules.py:85-87`** - Returns early if breached during trade loop, but `db.commit()` happens after

15. **`app/routers/admin_dashboard.py:554-565`** - Bulk settings update has minimal auth check (just checks for JSON payload)

### Known Issues
- Admin credentials are hardcoded and stored in source code
- Angel One live data requires valid API credentials; falls back to simulated data
- Google OAuth requires proper client ID/secret configuration
- Resend API key needed for actual email delivery (falls back to console printing)
- The `hashed_password` column may be `NULL` for users created before the migration
- Global exception handler exposes tracebacks (debug mode only)
- No database migration system (Alembic not used); schema changes via raw ALTER TABLE
- Some trade symbols (e.g., `XAUINR`) not in `INSTRUMENTS` dict but handled via special logic
- No rate limiting on API endpoints
- Chat system stores messages in memory (`admin_notifications` list) and SQLite; not persistent across restarts without proper setup

---

## Entry Point & Local Development

### How to Run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment variables (copy .env.example or create .env)
cp .env.example .env  # or create manually

# 3. Run the development server
python app/main.py
# or
uvicorn app.main:app

# 4. Access the application
# - Landing page: http://localhost:8080
# - Dashboard: http://localhost:8080/dashboard
# - Admin: http://localhost:8080/admin/login

# Default port: 8080 (override with PORT env var)
```

### Deployment

- **Render.com**: Configured via `render.yaml`
  - Build: `pip install -r requirements.txt`
  - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  - Environment variables set in Render dashboard

- **Procfile**: `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`

- **Production notes**:
  - Set `DEBUG=False` or ensure `SECRET_KEY` is random
  - Configure `DATABASE_URL` for PostgreSQL
  - Set `RESEND_API_KEY` for email functionality
  - Set proper `ALLOWED_HOSTS` or rely on `CORS_ALLOW_ALL = True` (currently `["*"]`)
  - Generate new `SECRET_KEY` for production deployment