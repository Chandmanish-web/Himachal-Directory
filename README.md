# Himachal Directory

A business directory for Himachal Pradesh. Python (standard library only) backend, vanilla JavaScript frontend, SQLite database. **No packages to install.**

Requires Python 3.9+.

## Run it

```bash
python app.py
```

Open http://127.0.0.1:8000

## First-time setup

The 307 complete, contactable industrial listings are added to a database automatically when the app starts; incomplete spreadsheet rows were omitted. The source data is in `data/industrial-listings.json`, and each dataset version is imported only once.

1. **Create your admin account** (needed to approve listings):
   ```bash
   python manage.py createadmin you@example.com
   ```
2. *(Optional)* Add a few clearly labelled sample listings so the site isn't empty while you test:
   ```bash
   python manage.py demo
   ```
3. Sign in at `/login`, then open `/admin` to review submissions.

## Features

- **Home**: hero, search by keyword / category / city, popular searches, categories, cities, everyday needs, ad-space placeholder, newest listings, trust section, call to action.
- **Explore** (`/explore`): filter by keyword, category and city, with pagination. Filters live in the URL, so results are shareable.
- **Business page** (`/business/<slug>`): details with phone, email and website links.
- **Accounts** (`/login`): register and sign in. Passwords are hashed with PBKDF2.
- **Dashboard** (`/dashboard`): owners add, edit and delete their own listings and see each one's status (pending, live, needs changes, with the reviewer's reason).
- **Admin review** (`/admin`): approve or reject submissions, and read advertising enquiries.
- **About** (`/about`) with an advertising enquiry form, and **Privacy & guidelines** (`/privacy`).

### How review works
- New listings start as **pending** and are only public once an admin approves them.
- Rejections require a short reason, which the owner sees on their dashboard.
- When an owner edits a listing it goes back to **pending** for another review.
- Pending and rejected listings are visible only to their owner and admins.

## Customising

- **Categories and cities**: edit the lists in `config.py`. Nothing else needs to change.
- **Colours and fonts**: the variables at the top of `static/css/style.css` (`--pine`, `--apple`, `--serif`, `--sans`). Fonts load from Google Fonts in `static/index.html`.
- **Text and pages**: `static/js/app.js`.

## Settings (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `127.0.0.1` | Use `0.0.0.0` to accept outside connections |
| `PORT` | `8000` | Port to listen on |
| `DB_PATH` | `data/directory.db` | SQLite file location |
| `SECURE_COOKIES` | off | Set to `1` when serving over HTTPS |
| `TRUST_PROXY` | off | Set to `1` behind nginx/Caddy so rate limiting sees real visitor IPs |

## Deploying

This is a Python process that must stay running, so you need hosting that can run one (a VPS, or a Python-capable app host). Typical shape:

1. Copy the folder to the server and run `python app.py` under a process manager (systemd, supervisor, etc.) with `HOST=127.0.0.1 PORT=8000 TRUST_PROXY=1 SECURE_COOKIES=1`.
2. Put nginx or Caddy in front for HTTPS and proxy to `127.0.0.1:8000`.
3. Run `python manage.py createadmin you@example.com` once.
4. Back up `data/directory.db` regularly.

Basic hardening is built in: CSRF protection (custom header plus SameSite cookies), HttpOnly session cookies, rate limiting on sign-in, registration and enquiries, input validation, parameterised SQL, output escaping, and a Content-Security-Policy.

### Deploy to Render

The included `render.yaml` configures a free Render web service. To deploy it, push this repository to GitHub, open the [Render Dashboard](https://dashboard.render.com), select **New > Blueprint**, connect this repository, and deploy the `Main` branch. Render will build and start the app using the Blueprint settings.

**Free Render services have ephemeral storage:** uploaded data and the SQLite database (including user accounts, new listings, and enquiries) are lost on restarts, redeploys, and spin-downs. The bundled industrial listings are re-created automatically at startup, but other database changes are not. Use a persistent database and paid persistent storage for a site that must retain user data.

## Project layout

```
app.py          server, API and database
manage.py       createadmin and demo commands
config.py       categories, cities and settings
render.yaml     Render free web service configuration
static/
  index.html    page shell
  css/style.css styles
  js/app.js     frontend app and router
  favicon.svg
data/           SQLite database is created here
  industrial-listings.json  bundled industrial business listings
```
