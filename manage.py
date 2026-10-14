#!/usr/bin/env python3
"""Management commands.

  python manage.py createadmin you@example.com     # prompts for a password
  python manage.py demo                            # adds clearly labelled sample listings
"""
import getpass
import sys

import app


def create_admin(email):
    email = email.strip().lower()
    if not app.EMAIL_RE.match(email):
        sys.exit("That does not look like a valid email address.")
    password = getpass.getpass("Password (min 8 characters): ")
    if len(password) < 8:
        sys.exit("Password must be at least 8 characters.")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords did not match.")
    app.init_db()
    db = app.connect()
    try:
        row = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            db.execute("UPDATE users SET is_admin = 1, pw_hash = ? WHERE id = ?",
                       (app.hash_password(password), row["id"]))
            print(f"Updated existing user {email} and made them an admin.")
        else:
            db.execute(
                "INSERT INTO users (name, email, pw_hash, is_admin, created_at) VALUES (?, ?, ?, 1, ?)",
                ("Administrator", email, app.hash_password(password), app.now_iso()),
            )
            print(f"Created admin {email}.")
        db.commit()
    finally:
        db.close()


SAMPLES = [
    ("Sample - Deodar Timber Works", "Manufacturers & factories", "Baddi",
     "Sample listing for testing. Furniture and timber products made to order for homes and hotels.", "+91 98000 00001"),
    ("Sample - Ridge View Homestay", "Hotels & travel", "Shimla",
     "Sample listing for testing. Family-run homestay with mountain views and home-cooked Himachali meals.", "+91 98000 00002"),
    ("Sample - Kangra Valley Tea Traders", "Wholesalers & distributors", "Palampur",
     "Sample listing for testing. Wholesale supply of Kangra tea to cafes, hotels and retailers.", "+91 98000 00003"),
    ("Sample - Apple Belt Cold Storage", "Agriculture & orchards", "Kullu",
     "Sample listing for testing. Cold storage and grading services for orchard growers.", "+91 98000 00004"),
    ("Sample - Hill Route Cargo", "Transport & logistics", "Solan",
     "Sample listing for testing. Goods transport between Himachal and the plains with tracked deliveries.", "+91 98000 00005"),
    ("Sample - Mandi Code Studio", "IT & software", "Mandi",
     "Sample listing for testing. Websites and mobile apps for local businesses.", "+91 98000 00006"),
]


def demo():
    app.init_db()
    db = app.connect()
    try:
        row = db.execute("SELECT id FROM users WHERE email = 'demo-owner@example.com'").fetchone()
        if row:
            owner_id = row["id"]
        else:
            owner_id = db.execute(
                "INSERT INTO users (name, email, pw_hash, is_admin, created_at) VALUES (?, ?, ?, 0, ?)",
                ("Demo Owner", "demo-owner@example.com", app.hash_password(app.secrets.token_urlsafe(24)), app.now_iso()),
            ).lastrowid
        added = 0
        for name, cat, city, desc, phone in SAMPLES:
            if db.execute("SELECT 1 FROM businesses WHERE name = ?", (name,)).fetchone():
                continue
            ts = app.now_iso()
            bid = db.execute(
                "INSERT INTO businesses (owner_id, name, category, city, description, phone, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'approved', ?, ?)",
                (owner_id, name, cat, city, desc, phone, ts, ts),
            ).lastrowid
            db.execute("UPDATE businesses SET slug = ? WHERE id = ?", (f"{app.slugify(name)}-{bid}", bid))
            added += 1
        db.commit()
        print(f"Added {added} sample listings (all names start with 'Sample - ').")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "createadmin":
        create_admin(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "demo":
        demo()
    else:
        print(__doc__)
