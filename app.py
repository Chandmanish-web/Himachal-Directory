#!/usr/bin/env python3
"""Himachal Industrial Directory - a business directory built on the Python standard library.

Run:  python app.py
No third-party packages required.

The server is organized into four parts:
1. Database and validation helpers.
2. API handlers that read or update directory data.
3. HTTP request handling for API calls and static files.
4. main(), which starts the server.
"""
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import config

STATIC_DIR = os.path.join(config.BASE_DIR, "static")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    pw_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slug TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    city TEXT NOT NULL,
    description TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    reject_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_biz_status ON businesses(status, created_at);
CREATE INDEX IF NOT EXISTS idx_biz_owner ON businesses(owner_id);
CREATE TABLE IF NOT EXISTS enquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seed_imports (
    source TEXT NOT NULL,
    digest TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (source, digest)
);
"""

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
PHONE_RE = re.compile(r"^[+0-9][0-9 \-()]{6,18}$")
SEED_LISTINGS_PATH = os.path.join(config.BASE_DIR, "data", "industrial-listings.json")
SEED_OWNER_EMAIL = "industrial-data-import@himachaldirectory.invalid"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
class HttpError(Exception):
    """An expected error that should be returned to the browser as JSON."""

    def __init__(self, status, message, fields=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.fields = fields or {}


def now_iso():
    """Return the current UTC time in the format used by the database."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect():
    """Open the directory database and return rows that can be read by name."""
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    db = sqlite3.connect(config.DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    """Create database tables and import bundled listings when needed."""
    db = connect()
    try:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA)
        seed_bundled_listings(db)
        db.commit()
    finally:
        db.close()


def seed_bundled_listings(db):
    """Import or update the read-only starter listings from the JSON file."""
    with open(SEED_LISTINGS_PATH, "rb") as f:
        source = f.read()
    digest = hashlib.sha256(source).hexdigest()
    db.execute("BEGIN IMMEDIATE")
    if db.execute(
        "SELECT 1 FROM seed_imports WHERE source = ? AND digest = ?",
        (SEED_LISTINGS_PATH, digest),
    ).fetchone():
        return

    listings = json.loads(source)
    if not isinstance(listings, list):
        raise ValueError(f"Bundled listings in {SEED_LISTINGS_PATH} must be a JSON array.")

    owner = db.execute("SELECT id FROM users WHERE email = ?", (SEED_OWNER_EMAIL,)).fetchone()
    if owner:
        owner_id = owner["id"]
    else:
        owner_id = db.execute(
            "INSERT INTO users (name, email, pw_hash, is_admin, created_at) VALUES (?, ?, ?, 0, ?)",
            ("Industrial Visit Data Import", SEED_OWNER_EMAIL,
             hash_password(secrets.token_urlsafe(32)), now_iso()),
        ).lastrowid

    imported = 0
    for index, listing in enumerate(listings, start=1):
        if not isinstance(listing, dict):
            raise ValueError(f"Bundled listing {index} in {SEED_LISTINGS_PATH} must be an object.")
        try:
            data = clean_business(listing, require_phone=False)
        except HttpError as exc:
            raise ValueError(f"Invalid bundled listing {index}: {exc.message}") from exc

        exists = db.execute(
            "SELECT id FROM businesses WHERE lower(name) = lower(?) AND address = ?",
            (data["name"], data["address"]),
        ).fetchone()
        if exists:
            continue

        timestamp = now_iso()
        cursor = db.execute(
            "INSERT INTO businesses (owner_id, name, category, city, description, address, phone, email, website, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)",
            (owner_id, data["name"], data["category"], data["city"], data["description"], data["address"],
             data["phone"], data["email"], data["website"], timestamp, timestamp),
        )
        db.execute(
            "UPDATE businesses SET slug = ? WHERE id = ?",
            (f"{slugify(data['name'])}-{cursor.lastrowid}", cursor.lastrowid),
        )
        imported += 1

    db.execute(
        "INSERT INTO seed_imports (source, digest, imported_at) VALUES (?, ?, ?)",
        (SEED_LISTINGS_PATH, digest, now_iso()),
    )
    print(f"Loaded {imported} bundled industrial listings.")


def hash_password(password, salt=None, iterations=200_000):
    """Turn a password into a salted hash so the plain password is never stored."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    """Check a password against the salted hash saved in the database."""
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        candidate = hash_password(password, bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(candidate, stored)
    except Exception:
        return False


DUMMY_HASH = hash_password("not-a-real-password")


def slugify(text):
    """Make a short, URL-friendly name for a business page."""
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:50] or "business"


_hits = {}
_hits_lock = threading.Lock()


def rate_limit(key, limit, window_seconds, record=True):
    """Stop a client after too many requests during a time window."""
    current_time = time.time()
    with _hits_lock:
        recent_hits = [
            hit_time for hit_time in _hits.get(key, [])
            if current_time - hit_time < window_seconds
        ]
        if len(recent_hits) >= limit:
            _hits[key] = recent_hits
            raise HttpError(429, "Too many attempts. Please wait a few minutes and try again.")
        if record:
            recent_hits.append(current_time)
        _hits[key] = recent_hits


def user_public(user_row):
    """Return only the account details that are safe to send to the browser."""
    return {
        "id": user_row["id"],
        "name": user_row["name"],
        "email": user_row["email"],
        "is_admin": bool(user_row["is_admin"]),
    }


def business_public(business_row, include_status=False, include_owner=False):
    """Return a business row as the public API format used by the browser."""
    out = {
        "id": business_row["id"],
        "slug": business_row["slug"],
        "name": business_row["name"],
        "category": business_row["category"],
        "city": business_row["city"],
        "description": business_row["description"],
        "address": business_row["address"],
        "phone": business_row["phone"] or None,
        "email": business_row["email"],
        "website": business_row["website"],
        "created_at": business_row["created_at"],
    }
    if include_status:
        out["status"] = business_row["status"]
        out["reject_reason"] = business_row["reject_reason"]
        out["updated_at"] = business_row["updated_at"]
    if include_owner:
        out["owner_name"] = business_row["owner_name"]
        out["owner_email"] = business_row["owner_email"]
    return out


def like_escape(value):
    """Escape special characters so a search is treated as plain text."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def require(ctx, admin=False):
    """Check that the request has a signed-in user (and optionally an admin)."""
    if not ctx.user:
        raise HttpError(401, "Please sign in to continue.")
    if admin and not ctx.user["is_admin"]:
        raise HttpError(403, "You do not have access to this area.")


def text_field(data, key, label, minimum, maximum, errors, required=True):
    """Trim and check a text field, adding a message to errors when invalid."""
    value = data.get(key, "")
    value = value.strip() if isinstance(value, str) else ""
    if not value and not required:
        return ""
    if len(value) < minimum:
        errors[key] = f"{label} must be at least {minimum} characters." if value else f"{label} is required."
    elif len(value) > maximum:
        errors[key] = f"{label} must be {maximum} characters or fewer."
    return value


def clean_business(data, require_phone=True):
    """Validate and tidy a listing before it is saved or imported."""
    errors = {}
    name = text_field(data, "name", "Business name", 2, 100, errors)
    description = text_field(data, "description", "Description", 20, 1200, errors)
    address = text_field(data, "address", "Address", 0, 200, errors, required=False)
    category = data.get("category", "")
    city = data.get("city", "")
    if category not in config.CATEGORIES:
        errors["category"] = "Choose a category from the list."
    if city not in config.CITIES:
        errors["city"] = "Choose a city from the list."
    phone = text_field(data, "phone", "Phone number", 1 if require_phone else 0, 20, errors, required=require_phone)
    if phone and not PHONE_RE.match(phone):
        errors["phone"] = "Enter a valid phone number, for example +91 98765 43210."
    email = text_field(data, "email", "Email", 0, 254, errors, required=False)
    if email and not EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address."
    website = text_field(data, "website", "Website", 0, 200, errors, required=False)
    if website and not re.match(r"^https?://[^\s]+\.[^\s]+$", website):
        errors["website"] = "Website must start with http:// or https://"
    if not require_phone and not phone and not email and not website and "phone" not in errors:
        errors["phone"] = "Add at least one way for customers to reach this business."
    if errors:
        raise HttpError(400, "Please fix the highlighted fields.", errors)
    return {
        "name": name, "category": category, "city": city, "description": description,
        "address": address, "phone": phone, "email": email.lower(), "website": website,
    }


# --------------------------------------------------------------------------
# Request context
# --------------------------------------------------------------------------
class RequestContext:
    """Information shared with one API handler while it processes a request."""

    def __init__(
        self,
        database_connection,
        signed_in_user,
        query_parameters,
        request_body,
        route_match,
        client_ip,
    ):
        self.db = database_connection
        self.user = signed_in_user
        self.query = query_parameters
        self.body = request_body
        self.match = route_match
        self.ip = client_ip
        self.cookies = []


def create_session(ctx, user_id):
    """Create a sign-in session and add its cookie to the response."""
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + config.SESSION_DAYS * 86400
    ctx.db.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (hashlib.sha256(token.encode()).hexdigest(), user_id, expires),
    )
    ctx.cookies.append(cookie_header("sid", token, config.SESSION_DAYS * 86400))


def cookie_header(name, value, max_age):
    parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
    if config.SECURE_COOKIES:
        parts.append("Secure")
    return "; ".join(parts)


# --------------------------------------------------------------------------
# API handlers
# --------------------------------------------------------------------------
def api_meta(ctx):
    """Give the browser the category and city options used in forms."""
    return {"categories": config.CATEGORIES, "cities": config.CITIES}


def api_me(ctx):
    """Tell the browser which user is signed in, if any."""
    return {"user": user_public(ctx.user) if ctx.user else None}


def api_register(ctx):
    """Validate a new account, save it, and sign the new user in."""
    rate_limit(("register", ctx.ip), 10, 3600)
    errors = {}
    name = text_field(ctx.body, "name", "Name", 2, 80, errors)
    email = text_field(ctx.body, "email", "Email", 3, 254, errors).lower()
    password = ctx.body.get("password", "")
    if email and not EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address."
    if not isinstance(password, str) or not (8 <= len(password) <= 128):
        errors["password"] = "Password must be 8 to 128 characters."
    if errors:
        raise HttpError(400, "Please fix the highlighted fields.", errors)
    if ctx.db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise HttpError(409, "An account with this email already exists. Try signing in.", {"email": "Already registered."})
    insert_result = ctx.db.execute(
        "INSERT INTO users (name, email, pw_hash, is_admin, created_at) VALUES (?, ?, ?, 0, ?)",
        (name, email, hash_password(password), now_iso()),
    )
    create_session(ctx, insert_result.lastrowid)
    user = ctx.db.execute(
        "SELECT * FROM users WHERE id = ?", (insert_result.lastrowid,)
    ).fetchone()
    return 201, {"user": user_public(user)}


def api_login(ctx):
    """Check login details and create a session for a valid user."""
    rate_limit(("login", ctx.ip), 10, 600, record=False)  # only failures are recorded below
    email = str(ctx.body.get("email", "")).strip().lower()
    password = str(ctx.body.get("password", ""))
    user = ctx.db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    ok = verify_password(password, user["pw_hash"] if user else DUMMY_HASH)
    if not user or not ok:
        rate_limit(("login", ctx.ip), 10, 600)
        raise HttpError(401, "Email or password is incorrect.")
    create_session(ctx, user["id"])
    return {"user": user_public(user)}


def api_logout(ctx):
    """End the current session and clear the browser's sign-in cookie."""
    token = ctx.cookie_token
    if token:
        ctx.db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
    ctx.cookies.append(cookie_header("sid", "", 0))
    return {"ok": True}


def api_businesses(ctx):
    """Find approved listings that match the requested search filters."""
    search_text = (ctx.query.get("q") or "").strip()[:100]
    category = ctx.query.get("category") or ""
    city = ctx.query.get("city") or ""
    try:
        page_number = max(1, int(ctx.query.get("page", "1")))
    except ValueError:
        page_number = 1
    conditions = ["status = 'approved'"]
    query_values = []
    if category in config.CATEGORIES:
        conditions.append("category = ?")
        query_values.append(category)
    if city in config.CITIES:
        conditions.append("city = ?")
        query_values.append(city)
    if search_text:
        search_pattern = f"%{like_escape(search_text)}%"
        conditions.append(
            "(name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
            "OR category LIKE ? ESCAPE '\\' OR city LIKE ? ESCAPE '\\')"
        )
        query_values += [search_pattern] * 4
    where_clause = " AND ".join(conditions)
    total = ctx.db.execute(
        f"SELECT COUNT(*) FROM businesses WHERE {where_clause}", query_values
    ).fetchone()[0]
    business_rows = ctx.db.execute(
        f"SELECT * FROM businesses WHERE {where_clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        query_values + [config.PAGE_SIZE, (page_number - 1) * config.PAGE_SIZE],
    ).fetchall()
    return {
        "items": [business_public(business_row) for business_row in business_rows],
        "total": total,
        "page": page_number,
        "pages": max(1, -(-total // config.PAGE_SIZE)),
    }


def api_latest(ctx):
    """Return the six newest approved listings for the home page."""
    business_rows = ctx.db.execute(
        "SELECT * FROM businesses WHERE status = 'approved' ORDER BY created_at DESC, id DESC LIMIT 6"
    ).fetchall()
    return {"items": [business_public(business_row) for business_row in business_rows]}


def api_business_detail(ctx):
    """Return one listing, while keeping private listings private."""
    slug = ctx.match.group(1)
    business_row = ctx.db.execute(
        "SELECT * FROM businesses WHERE slug = ?", (slug,)
    ).fetchone()
    if not business_row:
        raise HttpError(404, "Business not found.")
    can_view_listing = business_row["status"] == "approved" or (
        ctx.user and (ctx.user["is_admin"] or ctx.user["id"] == business_row["owner_id"])
    )
    if not can_view_listing:
        raise HttpError(404, "Business not found.")
    return {
        "item": business_public(
            business_row,
            include_status=bool(ctx.user and business_row["status"] != "approved"),
        )
    }


def api_my_list(ctx):
    """Return the signed-in user's own listings, including their review status."""
    require(ctx)
    business_rows = ctx.db.execute(
        "SELECT * FROM businesses WHERE owner_id = ? ORDER BY updated_at DESC, id DESC", (ctx.user["id"],)
    ).fetchall()
    return {
        "items": [
            business_public(business_row, include_status=True)
            for business_row in business_rows
        ]
    }


def api_my_create(ctx):
    """Save a new listing as pending review."""
    require(ctx)
    listing_count = ctx.db.execute(
        "SELECT COUNT(*) FROM businesses WHERE owner_id = ?", (ctx.user["id"],)
    ).fetchone()[0]
    if listing_count >= config.MAX_LISTINGS_PER_USER:
        raise HttpError(400, f"You can have up to {config.MAX_LISTINGS_PER_USER} listings per account.")
    listing_data = clean_business(ctx.body)
    created_at = now_iso()
    insert_result = ctx.db.execute(
        "INSERT INTO businesses (owner_id, name, category, city, description, address, phone, email, website, "
        "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (ctx.user["id"], listing_data["name"], listing_data["category"], listing_data["city"],
         listing_data["description"], listing_data["address"], listing_data["phone"],
         listing_data["email"], listing_data["website"], created_at, created_at),
    )
    listing_id = insert_result.lastrowid
    ctx.db.execute(
        "UPDATE businesses SET slug = ? WHERE id = ?",
        (f"{slugify(listing_data['name'])}-{listing_id}", listing_id),
    )
    business_row = ctx.db.execute(
        "SELECT * FROM businesses WHERE id = ?", (listing_id,)
    ).fetchone()
    return 201, {"item": business_public(business_row, include_status=True)}


def owned_business(ctx):
    """Find a listing and confirm the signed-in user is allowed to manage it."""
    require(ctx)
    business_row = ctx.db.execute(
        "SELECT * FROM businesses WHERE id = ?", (int(ctx.match.group(1)),)
    ).fetchone()
    if not business_row or (
        business_row["owner_id"] != ctx.user["id"] and not ctx.user["is_admin"]
    ):
        raise HttpError(404, "Business not found.")
    return business_row


def api_my_update(ctx):
    """Save listing edits; send owner-edited listings back for review."""
    business_row = owned_business(ctx)
    listing_data = clean_business(ctx.body)
    # Admins editing keep their status; owners' edits go back to review.
    is_admin_editing_someone_elses_listing = (
        ctx.user["is_admin"] and business_row["owner_id"] != ctx.user["id"]
    )
    status = business_row["status"] if is_admin_editing_someone_elses_listing else "pending"
    ctx.db.execute(
        "UPDATE businesses SET name=?, category=?, city=?, description=?, address=?, phone=?, email=?, website=?, "
        "status=?, reject_reason=NULL, updated_at=? WHERE id=?",
        (listing_data["name"], listing_data["category"], listing_data["city"],
         listing_data["description"], listing_data["address"], listing_data["phone"],
         listing_data["email"], listing_data["website"], status, now_iso(), business_row["id"]),
    )
    updated_business = ctx.db.execute(
        "SELECT * FROM businesses WHERE id = ?", (business_row["id"],)
    ).fetchone()
    return {"item": business_public(updated_business, include_status=True)}


def api_my_delete(ctx):
    """Delete a listing owned by the signed-in user."""
    business_row = owned_business(ctx)
    ctx.db.execute("DELETE FROM businesses WHERE id = ?", (business_row["id"],))
    return {"ok": True}


def api_admin_list(ctx):
    """Return listings for an admin review tab and the counts for each tab."""
    require(ctx, admin=True)
    status = ctx.query.get("status", "pending")
    if status not in ("pending", "approved", "rejected"):
        status = "pending"
    business_rows = ctx.db.execute(
        "SELECT b.*, u.name AS owner_name, u.email AS owner_email FROM businesses b "
        "JOIN users u ON u.id = b.owner_id WHERE b.status = ? ORDER BY b.updated_at DESC LIMIT 200",
        (status,),
    ).fetchall()
    status_counts = {
        count_row["status"]: count_row["n"]
        for count_row in ctx.db.execute(
            "SELECT status, COUNT(*) AS n FROM businesses GROUP BY status"
        ).fetchall()
    }
    return {
        "items": [
            business_public(business_row, include_status=True, include_owner=True)
            for business_row in business_rows
        ],
        "counts": {
            status: status_counts.get(status, 0)
            for status in ("pending", "approved", "rejected")
        },
    }


def api_admin_review(ctx):
    """Approve a listing or reject it with an explanation for its owner."""
    require(ctx, admin=True)
    action = ctx.body.get("action")
    reason = str(ctx.body.get("reason", "")).strip()[:300]
    if action not in ("approve", "reject"):
        raise HttpError(400, "Action must be approve or reject.")
    if action == "reject" and len(reason) < 5:
        raise HttpError(400, "Give the owner a short reason so they can fix the listing.", {"reason": "Required."})
    business_id = int(ctx.match.group(1))
    if not ctx.db.execute(
        "SELECT 1 FROM businesses WHERE id = ?", (business_id,)
    ).fetchone():
        raise HttpError(404, "Business not found.")
    ctx.db.execute(
        "UPDATE businesses SET status = ?, reject_reason = ?, updated_at = ? WHERE id = ?",
        ("approved" if action == "approve" else "rejected",
         reason if action == "reject" else None, now_iso(), business_id),
    )
    return {"ok": True}


def api_admin_enquiries(ctx):
    """Return customer enquiries for the admin page."""
    require(ctx, admin=True)
    rows = ctx.db.execute("SELECT * FROM enquiries ORDER BY id DESC LIMIT 200").fetchall()
    return {"items": [dict(r) for r in rows]}


def api_enquiry(ctx):
    """Validate and save a message sent from the About page."""
    rate_limit(("enquiry", ctx.ip), 5, 3600)
    errors = {}
    name = text_field(ctx.body, "name", "Name", 2, 80, errors)
    email = text_field(ctx.body, "email", "Email", 3, 254, errors)
    message = text_field(ctx.body, "message", "Message", 10, 1500, errors)
    if email and not EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address."
    if errors:
        raise HttpError(400, "Please fix the highlighted fields.", errors)
    ctx.db.execute(
        "INSERT INTO enquiries (name, email, message, created_at) VALUES (?, ?, ?, ?)",
        (name, email, message, now_iso()),
    )
    return 201, {"ok": True}


ROUTES = [
    ("GET", r"^/api/meta$", api_meta),
    ("GET", r"^/api/me$", api_me),
    ("POST", r"^/api/register$", api_register),
    ("POST", r"^/api/login$", api_login),
    ("POST", r"^/api/logout$", api_logout),
    ("GET", r"^/api/businesses$", api_businesses),
    ("GET", r"^/api/businesses/latest$", api_latest),
    ("GET", r"^/api/businesses/([a-z0-9-]+)$", api_business_detail),
    ("GET", r"^/api/my/businesses$", api_my_list),
    ("POST", r"^/api/my/businesses$", api_my_create),
    ("PUT", r"^/api/my/businesses/(\d+)$", api_my_update),
    ("DELETE", r"^/api/my/businesses/(\d+)$", api_my_delete),
    ("GET", r"^/api/admin/businesses$", api_admin_list),
    ("POST", r"^/api/admin/businesses/(\d+)/review$", api_admin_review),
    ("GET", r"^/api/admin/enquiries$", api_admin_enquiries),
    ("POST", r"^/api/enquiry$", api_enquiry),
]
# Compile each route once when the server starts instead of on every request.
ROUTES = [(method, re.compile(path_pattern), handler) for method, path_pattern, handler in ROUTES]


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'"
    ),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HimachalDirectory/1.0"

    def log_message(self, fmt, *args):
        """Keep standard server access logs on the console."""
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def client_ip(self):
        """Use the proxy's visitor address only when proxy trust is enabled."""
        if config.TRUST_PROXY:
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0]

    def send_bytes(self, status, body, content_type, extra=None):
        """Send a response body with the site's standard security headers."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for header_name, header_value in SECURITY_HEADERS.items():
            self.send_header(header_name, header_value)
        for header_name, header_value in (extra or []):
            self.send_header(header_name, header_value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, obj, cookies=None):
        """Convert a Python object to JSON and send it to the browser."""
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        extra = [("Cache-Control", "no-store")] + [("Set-Cookie", c) for c in (cookies or [])]
        self.send_bytes(status, body, "application/json; charset=utf-8", extra)

    def handle_any(self):
        """Route each incoming HTTP request to the API or static-file handler."""
        method = self.command
        if method == "HEAD":
            method = "GET"
        parsed = urlparse(self.path)
        path = parsed.path
        # Read the request body once, and reject unusually large requests early.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > config.MAX_BODY_BYTES:
            self.close_connection = True
            return self.send_json(413, {"error": "Request too large."})
        raw = self.rfile.read(length) if length else b""
        try:
            if path.startswith("/api/"):
                self.handle_api(method, path, parse_qs(parsed.query), raw)
            else:
                self.handle_static(method, unquote(path))
        except HttpError as error:
            self.send_json(error.status, {"error": error.message, "fields": error.fields})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "Something went wrong on our side. Please try again."})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = handle_any

    def handle_api(self, method, path, query, raw):
        """Find the API route, load its request data, and call its handler."""
        matched_route = None
        route_path_exists = False
        for route_method, route_pattern, route_handler in ROUTES:
            route_match = route_pattern.match(path)
            if route_match:
                route_path_exists = True
                if route_method == method:
                    matched_route = (route_handler, route_match)
                    break
        if not matched_route:
            raise HttpError(405 if route_path_exists else 404, "Not found.")
        route_handler, route_match = matched_route

        request_body = {}
        if method != "GET":
            if self.headers.get("X-Requested-With") != "fetch":
                raise HttpError(403, "Invalid request.")
            if raw:
                try:
                    request_body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise HttpError(400, "Request body must be valid JSON.")
                if not isinstance(request_body, dict):
                    raise HttpError(400, "Request body must be a JSON object.")

        # A database connection is opened for this request and always closed below.
        database = connect()
        try:
            # Read the signed-in user from the session cookie, if one was sent.
            cookie_token = None
            raw_cookie = self.headers.get("Cookie")
            if raw_cookie:
                jar = SimpleCookie()
                try:
                    jar.load(raw_cookie)
                    if "sid" in jar:
                        cookie_token = jar["sid"].value
                except Exception:
                    pass
            user = None
            if cookie_token:
                user = database.execute(
                    "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                    "WHERE s.token_hash = ? AND s.expires_at > ?",
                    (hashlib.sha256(cookie_token.encode()).hexdigest(), int(time.time())),
                ).fetchone()
            query_parameters = {
                parameter_name: values[0]
                for parameter_name, values in query.items()
            }
            request_context = RequestContext(
                database, user, query_parameters, request_body, route_match, self.client_ip()
            )
            request_context.cookie_token = cookie_token
            result = route_handler(request_context)
            database.commit()
            # API handlers may return just data (200) or (status code, data).
            status, payload = result if isinstance(result, tuple) else (200, result)
            self.send_json(status, payload, request_context.cookies)
        finally:
            database.close()

    def handle_static(self, method, path):
        """Serve a requested file or the single-page app's starting HTML."""
        if method != "GET":
            raise HttpError(405, "Method not allowed.")
        if path.startswith("/static/"):
            relative_path = path[len("/static/"):]
        elif path in ("/favicon.svg", "/robots.txt"):
            relative_path = path.lstrip("/")
        else:
            relative_path = None  # Let the browser router handle app pages.

        if relative_path is not None:
            file_path = os.path.realpath(os.path.join(STATIC_DIR, relative_path))
            static_root = os.path.realpath(STATIC_DIR) + os.sep
            if not file_path.startswith(static_root) or not os.path.isfile(file_path):
                raise HttpError(404, "File not found.")
            content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in (
                "application/javascript", "image/svg+xml"
            ):
                if "charset" not in content_type:
                    content_type += "; charset=utf-8"
            with open(file_path, "rb") as static_file:
                self.send_bytes(
                    200, static_file.read(), content_type,
                    [("Cache-Control", "public, max-age=300")],
                )
            return

        index_path = os.path.join(STATIC_DIR, "index.html")
        with open(index_path, "rb") as index_file:
            self.send_bytes(
                200, index_file.read(), "text/html; charset=utf-8",
                [("Cache-Control", "no-cache")],
            )


def main():
    init_db()
    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    print(f"Himachal Industrial Directory running at http://{config.HOST}:{config.PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
