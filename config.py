"""Shared configuration for the Himachal Industrial Directory."""
import os

# Use the project folder as the starting point for local file paths.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# These can be overridden with environment variables when deploying the app.
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "directory.db"))

# Set SECURE_COOKIES=1 when serving over HTTPS.
SECURE_COOKIES = os.environ.get("SECURE_COOKIES") == "1"
# Set TRUST_PROXY=1 when running behind nginx/Caddy so client IPs come from X-Forwarded-For.
TRUST_PROXY = os.environ.get("TRUST_PROXY") == "1"

SESSION_DAYS = 14  # How long a user stays signed in.
MAX_BODY_BYTES = 64 * 1024  # Largest request body accepted by the server.
PAGE_SIZE = 12  # Number of businesses shown on one explore page.
MAX_LISTINGS_PER_USER = 10  # Maximum number of listings one account can create.

# These lists are used by both the search forms and server-side validation.
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
