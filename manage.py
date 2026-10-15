#!/usr/bin/env python3
"""Management commands.

  python manage.py createadmin you@example.com     # prompts for a password
  python manage.py demo                            # adds clearly labelled sample listings
"""
import getpass
import sys

import app


def create_admin(email):
    """Create an admin account, or promote an existing account to admin."""
    email = email.strip().lower()
    if not app.EMAIL_RE.match(email):
        sys.exit("That does not look like a valid email address.")
    password = getpass.getpass("Password (min 8 characters): ")
    if len(password) < 8:
        sys.exit("Password must be at least 8 characters.")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords did not match.")
    app.init_db()
    database = app.connect()
    try:
        existing_user = database.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()
        if existing_user:
            database.execute(
                "UPDATE users SET is_admin = 1, pw_hash = ? WHERE id = ?",
                (app.hash_password(password), existing_user["id"]),
            )
            print(f"Updated existing user {email} and made them an admin.")
        else:
            database.execute(
                "INSERT INTO users (name, email, pw_hash, is_admin, created_at) VALUES (?, ?, ?, 1, ?)",
                ("Administrator", email, app.hash_password(password), app.now_iso()),
            )
            print(f"Created admin {email}.")
        database.commit()
    finally:
        database.close()


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
    """Add the clearly labelled sample listings used for testing the site."""
    app.init_db()
    database = app.connect()
    try:
        demo_owner = database.execute(
            "SELECT id FROM users WHERE email = 'demo-owner@example.com'"
        ).fetchone()
        if demo_owner:
            owner_id = demo_owner["id"]
        else:
            owner_id = database.execute(
                "INSERT INTO users (name, email, pw_hash, is_admin, created_at) VALUES (?, ?, ?, 0, ?)",
                ("Demo Owner", "demo-owner@example.com", app.hash_password(app.secrets.token_urlsafe(24)), app.now_iso()),
            ).lastrowid
        added = 0
        for name, category, city, description, phone in SAMPLES:
            if database.execute(
                "SELECT 1 FROM businesses WHERE name = ?", (name,)
            ).fetchone():
                continue
            created_at = app.now_iso()
            listing_id = database.execute(
                "INSERT INTO businesses (owner_id, name, category, city, description, phone, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'approved', ?, ?)",
                (owner_id, name, category, city, description, phone, created_at, created_at),
            ).lastrowid
            database.execute(
                "UPDATE businesses SET slug = ? WHERE id = ?",
                (f"{app.slugify(name)}-{listing_id}", listing_id),
            )
            added += 1
        database.commit()
        print(f"Added {added} sample listings (all names start with 'Sample - ').")
    finally:
        database.close()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "createadmin":
        create_admin(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "demo":
        demo()
    else:
        print(__doc__)
