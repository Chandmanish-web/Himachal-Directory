"""Shared configuration for the Himachal Industrial Directory."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "directory.db"))

# Set SECURE_COOKIES=1 when serving over HTTPS.
SECURE_COOKIES = os.environ.get("SECURE_COOKIES") == "1"
# Set TRUST_PROXY=1 when running behind nginx/Caddy so client IPs come from X-Forwarded-For.
TRUST_PROXY = os.environ.get("TRUST_PROXY") == "1"

SESSION_DAYS = 14
MAX_BODY_BYTES = 64 * 1024
PAGE_SIZE = 12
MAX_LISTINGS_PER_USER = 10

CATEGORIES = [
    "Manufacturers & factories",
    "Wholesalers & distributors",
    "Industrial machinery",
    "Retail & shopping",
    "Healthcare & medical",
    "Education & training",
    "Restaurants & food",
    "Hotels & travel",
    "Construction & real estate",
    "Professional services",
    "IT & software",
    "Transport & logistics",
    "Agriculture & orchards",
    "Home & repair",
    "Beauty & personal care",
]

CITIES = [
    "Shimla",
    "Dharamshala",
    "Solan",
    "Mandi",
    "Kullu",
    "Manali",
    "Kangra",
    "Palampur",
    "Hamirpur",
    "Una",
    "Baddi",
    "Nahan",
]
