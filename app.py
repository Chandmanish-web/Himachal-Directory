#!/usr/bin/env python3
"""Himachal Directory - a business directory built on the Python standard library.

Run:  python app.py
No third-party packages required.
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
    def __init__(self, status, message, fields=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.fields = fields or {}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    db = sqlite3.connect(config.DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    db = connect()
    try:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA)
        seed_bundled_listings(db)
        db.commit()
    finally:
        db.close()


def seed_bundled_listings(db):
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
            data = clean_business(listing)
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
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        candidate = hash_password(password, bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(candidate, stored)
    except Exception:
        return False


DUMMY_HASH = hash_password("not-a-real-password")


def slugify(text):
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:50] or "business"


_hits = {}
_hits_lock = threading.Lock()


def rate_limit(key, limit, window_seconds, record=True):
    """Raise 429 if `key` has made `limit` hits in the window. record=False only checks."""
    now = time.time()
    with _hits_lock:
        recent = [t for t in _hits.get(key, []) if now - t < window_seconds]
        if len(recent) >= limit:
            _hits[key] = recent
            raise HttpError(429, "Too many attempts. Please wait a few minutes and try again.")
        if record:
            recent.append(now)
        _hits[key] = recent


def user_public(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "is_admin": bool(u["is_admin"])}


def business_public(row, include_status=False, include_owner=False):
    out = {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "category": row["category"],
        "city": row["city"],
        "description": row["description"],
        "address": row["address"],
        "phone": row["phone"],
        "email": row["email"],
        "website": row["website"],
        "created_at": row["created_at"],
    }
    if include_status:
        out["status"] = row["status"]
        out["reject_reason"] = row["reject_reason"]
        out["updated_at"] = row["updated_at"]
    if include_owner:
        out["owner_name"] = row["owner_name"]
        out["owner_email"] = row["owner_email"]
    return out


def like_escape(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def require(ctx, admin=False):
    if not ctx.user:
        raise HttpError(401, "Please sign in to continue.")
    if admin and not ctx.user["is_admin"]:
        raise HttpError(403, "You do not have access to this area.")


def text_field(data, key, label, minimum, maximum, errors, required=True):
    value = data.get(key, "")
    value = value.strip() if isinstance(value, str) else ""
    if not value and not required:
        return ""
    if len(value) < minimum:
        errors[key] = f"{label} must be at least {minimum} characters." if value else f"{label} is required."
    elif len(value) > maximum:
        errors[key] = f"{label} must be {maximum} characters or fewer."
    return value


def clean_business(data):
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
    phone = text_field(data, "phone", "Phone", 0, 20, errors, required=False)
    if phone and not PHONE_RE.match(phone):
        errors["phone"] = "Enter a valid phone number, for example +91 98765 43210."
    email = text_field(data, "email", "Email", 0, 254, errors, required=False)
    if email and not EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address."
    website = text_field(data, "website", "Website", 0, 200, errors, required=False)
    if website and not re.match(r"^https?://[^\s]+\.[^\s]+$", website):
        errors["website"] = "Website must start with http:// or https://"
    if not phone and not email and not website and "phone" not in errors:
        errors["phone"] = "Add at least one way for customers to reach you: phone, email or website."
    if errors:
        raise HttpError(400, "Please fix the highlighted fields.", errors)
    return {
        "name": name, "category": category, "city": city, "description": description,
        "address": address, "phone": phone, "email": email.lower(), "website": website,
    }


# --------------------------------------------------------------------------
# Request context
# --------------------------------------------------------------------------
class Ctx:
    def __init__(self, db, user, query, body, match, ip):
        self.db = db
        self.user = user
        self.query = query
        self.body = body
        self.match = match
        self.ip = ip
        self.cookies = []


def create_session(ctx, user_id):
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
    return {"categories": config.CATEGORIES, "cities": config.CITIES}


def api_me(ctx):
    return {"user": user_public(ctx.user) if ctx.user else None}


def api_register(ctx):
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
    cur = ctx.db.execute(
        "INSERT INTO users (name, email, pw_hash, is_admin, created_at) VALUES (?, ?, ?, 0, ?)",
        (name, email, hash_password(password), now_iso()),
    )
    create_session(ctx, cur.lastrowid)
    user = ctx.db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return 201, {"user": user_public(user)}


def api_login(ctx):
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
    token = ctx.cookie_token
    if token:
        ctx.db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
    ctx.cookies.append(cookie_header("sid", "", 0))
    return {"ok": True}


def api_businesses(ctx):
    q = (ctx.query.get("q") or "").strip()[:100]
    category = ctx.query.get("category") or ""
    city = ctx.query.get("city") or ""
    try:
        page = max(1, int(ctx.query.get("page", "1")))
    except ValueError:
        page = 1
    where, params = ["status = 'approved'"], []
    if category in config.CATEGORIES:
        where.append("category = ?")
        params.append(category)
    if city in config.CITIES:
        where.append("city = ?")
        params.append(city)
    if q:
        like = f"%{like_escape(q)}%"
        where.append(
            "(name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
            "OR category LIKE ? ESCAPE '\\' OR city LIKE ? ESCAPE '\\')"
        )
        params += [like] * 4
    clause = " AND ".join(where)
    total = ctx.db.execute(f"SELECT COUNT(*) FROM businesses WHERE {clause}", params).fetchone()[0]
    rows = ctx.db.execute(
        f"SELECT * FROM businesses WHERE {clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        params + [config.PAGE_SIZE, (page - 1) * config.PAGE_SIZE],
    ).fetchall()
    return {
        "items": [business_public(r) for r in rows],
        "total": total,
        "page": page,
        "pages": max(1, -(-total // config.PAGE_SIZE)),
    }


def api_latest(ctx):
    rows = ctx.db.execute(
        "SELECT * FROM businesses WHERE status = 'approved' ORDER BY created_at DESC, id DESC LIMIT 6"
    ).fetchall()
    return {"items": [business_public(r) for r in rows]}


def api_business_detail(ctx):
    slug = ctx.match.group(1)
    row = ctx.db.execute("SELECT * FROM businesses WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise HttpError(404, "Business not found.")
    can_see = row["status"] == "approved" or (
        ctx.user and (ctx.user["is_admin"] or ctx.user["id"] == row["owner_id"])
    )
    if not can_see:
        raise HttpError(404, "Business not found.")
    return {"item": business_public(row, include_status=bool(ctx.user and row["status"] != "approved"))}


def api_my_list(ctx):
    require(ctx)
    rows = ctx.db.execute(
        "SELECT * FROM businesses WHERE owner_id = ? ORDER BY updated_at DESC, id DESC", (ctx.user["id"],)
    ).fetchall()
    return {"items": [business_public(r, include_status=True) for r in rows]}


def api_my_create(ctx):
    require(ctx)
    count = ctx.db.execute("SELECT COUNT(*) FROM businesses WHERE owner_id = ?", (ctx.user["id"],)).fetchone()[0]
    if count >= config.MAX_LISTINGS_PER_USER:
        raise HttpError(400, f"You can have up to {config.MAX_LISTINGS_PER_USER} listings per account.")
    d = clean_business(ctx.body)
    ts = now_iso()
    cur = ctx.db.execute(
        "INSERT INTO businesses (owner_id, name, category, city, description, address, phone, email, website, "
        "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (ctx.user["id"], d["name"], d["category"], d["city"], d["description"], d["address"],
         d["phone"], d["email"], d["website"], ts, ts),
    )
    new_id = cur.lastrowid
    ctx.db.execute("UPDATE businesses SET slug = ? WHERE id = ?", (f"{slugify(d['name'])}-{new_id}", new_id))
    row = ctx.db.execute("SELECT * FROM businesses WHERE id = ?", (new_id,)).fetchone()
    return 201, {"item": business_public(row, include_status=True)}


def owned_business(ctx):
    require(ctx)
    row = ctx.db.execute("SELECT * FROM businesses WHERE id = ?", (int(ctx.match.group(1)),)).fetchone()
    if not row or (row["owner_id"] != ctx.user["id"] and not ctx.user["is_admin"]):
        raise HttpError(404, "Business not found.")
    return row


def api_my_update(ctx):
    row = owned_business(ctx)
    d = clean_business(ctx.body)
    # Admins editing keep their status; owners' edits go back to review.
    status = row["status"] if ctx.user["is_admin"] and row["owner_id"] != ctx.user["id"] else "pending"
    ctx.db.execute(
        "UPDATE businesses SET name=?, category=?, city=?, description=?, address=?, phone=?, email=?, website=?, "
        "status=?, reject_reason=NULL, updated_at=? WHERE id=?",
        (d["name"], d["category"], d["city"], d["description"], d["address"], d["phone"], d["email"],
         d["website"], status, now_iso(), row["id"]),
    )
    new = ctx.db.execute("SELECT * FROM businesses WHERE id = ?", (row["id"],)).fetchone()
    return {"item": business_public(new, include_status=True)}


def api_my_delete(ctx):
    row = owned_business(ctx)
    ctx.db.execute("DELETE FROM businesses WHERE id = ?", (row["id"],))
    return {"ok": True}


def api_admin_list(ctx):
    require(ctx, admin=True)
    status = ctx.query.get("status", "pending")
    if status not in ("pending", "approved", "rejected"):
        status = "pending"
    rows = ctx.db.execute(
        "SELECT b.*, u.name AS owner_name, u.email AS owner_email FROM businesses b "
        "JOIN users u ON u.id = b.owner_id WHERE b.status = ? ORDER BY b.updated_at DESC LIMIT 200",
        (status,),
    ).fetchall()
    counts = {r["status"]: r["n"] for r in ctx.db.execute(
        "SELECT status, COUNT(*) AS n FROM businesses GROUP BY status").fetchall()}
    return {
        "items": [business_public(r, include_status=True, include_owner=True) for r in rows],
        "counts": {s: counts.get(s, 0) for s in ("pending", "approved", "rejected")},
    }


def api_admin_review(ctx):
    require(ctx, admin=True)
    action = ctx.body.get("action")
    reason = str(ctx.body.get("reason", "")).strip()[:300]
    if action not in ("approve", "reject"):
        raise HttpError(400, "Action must be approve or reject.")
    if action == "reject" and len(reason) < 5:
        raise HttpError(400, "Give the owner a short reason so they can fix the listing.", {"reason": "Required."})
    bid = int(ctx.match.group(1))
    if not ctx.db.execute("SELECT 1 FROM businesses WHERE id = ?", (bid,)).fetchone():
        raise HttpError(404, "Business not found.")
    ctx.db.execute(
        "UPDATE businesses SET status = ?, reject_reason = ?, updated_at = ? WHERE id = ?",
        ("approved" if action == "approve" else "rejected", reason if action == "reject" else None, now_iso(), bid),
    )
    return {"ok": True}


def api_admin_enquiries(ctx):
    require(ctx, admin=True)
    rows = ctx.db.execute("SELECT * FROM enquiries ORDER BY id DESC LIMIT 200").fetchall()
    return {"items": [dict(r) for r in rows]}


def api_enquiry(ctx):
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
ROUTES = [(m, re.compile(p), fn) for m, p, fn in ROUTES]


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
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def client_ip(self):
        if config.TRUST_PROXY:
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0]

    def send_bytes(self, status, body, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, obj, cookies=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        extra = [("Cache-Control", "no-store")] + [("Set-Cookie", c) for c in (cookies or [])]
        self.send_bytes(status, body, "application/json; charset=utf-8", extra)

    def handle_any(self):
        method = self.command
        if method == "HEAD":
            method = "GET"
        parsed = urlparse(self.path)
        path = parsed.path
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
        except HttpError as e:
            self.send_json(e.status, {"error": e.message, "fields": e.fields})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "Something went wrong on our side. Please try again."})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = handle_any

    def handle_api(self, method, path, query, raw):
        matched, path_exists = None, False
        for m, pattern, fn in ROUTES:
            mo = pattern.match(path)
            if mo:
                path_exists = True
                if m == method:
                    matched = (fn, mo)
                    break
        if not matched:
            raise HttpError(405 if path_exists else 404, "Not found.")
        fn, mo = matched

        body = {}
        if method != "GET":
            if self.headers.get("X-Requested-With") != "fetch":
                raise HttpError(403, "Invalid request.")
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise HttpError(400, "Request body must be valid JSON.")
                if not isinstance(body, dict):
                    raise HttpError(400, "Request body must be a JSON object.")

        db = connect()
        try:
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
                user = db.execute(
                    "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                    "WHERE s.token_hash = ? AND s.expires_at > ?",
                    (hashlib.sha256(cookie_token.encode()).hexdigest(), int(time.time())),
                ).fetchone()
            ctx = Ctx(db, user, {k: v[0] for k, v in query.items()}, body, mo, self.client_ip())
            ctx.cookie_token = cookie_token
            result = fn(ctx)
            db.commit()
            status, payload = result if isinstance(result, tuple) else (200, result)
            self.send_json(status, payload, ctx.cookies)
        finally:
            db.close()

    def handle_static(self, method, path):
        if method != "GET":
            raise HttpError(405, "Method not allowed.")
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
        elif path in ("/favicon.svg", "/robots.txt"):
            rel = path.lstrip("/")
        else:
            rel = None  # Single-page app: let the browser router handle it.

        if rel is not None:
            full = os.path.realpath(os.path.join(STATIC_DIR, rel))
            if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
                raise HttpError(404, "File not found.")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
                ctype += "; charset=utf-8" if "charset" not in ctype else ""
            with open(full, "rb") as f:
                self.send_bytes(200, f.read(), ctype, [("Cache-Control", "public, max-age=300")])
            return

        with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
            self.send_bytes(200, f.read(), "text/html; charset=utf-8", [("Cache-Control", "no-cache")])


def main():
    init_db()
    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    print(f"Himachal Directory running at http://{config.HOST}:{config.PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
