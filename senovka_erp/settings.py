"""
Django settings for senovka_erp project.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-CHANGE-ME-BEFORE-DEPLOYING-senovka-erp"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = [
    "senovkaplastics.cloud",
    "www.senovkaplastics.cloud",
    "72.61.174.119",
    "127.0.0.1",
    "localhost",
]


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third party
    "crispy_forms",
    "crispy_tailwind",
    # Local
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Stashes the current request on a thread-local so audit-log signal
    # handlers know which user fired the save. Must come after
    # AuthenticationMiddleware so `request.user` is populated by then.
    "core.audit.CurrentRequestMiddleware",
]

ROOT_URLCONF = "senovka_erp.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.current_role",
                "core.context_processors.notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "senovka_erp.wsgi.application"
ASGI_APPLICATION = "senovka_erp.asgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Colombo"

USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Authentication -------------------------------------------------------
# Login/logout only. There is no registration flow; users are seeded via
# the `seed_users` management command or the Django admin.

AUTH_USER_MODEL = "core.User"

LOGIN_URL = "login"  # -> /login/
LOGIN_REDIRECT_URL = "core:dashboard"  # -> /dashboard/
# Land signed-out users on the public marketing page rather than kicking them
# straight back to the sign-in form — the landing page introduces the product
# and carries its own "Sign in" button.
LOGOUT_REDIRECT_URL = "core:landing"  # -> /


# --- Session expiry -------------------------------------------------------
# Every session is a hard one-day login. A shared shop terminal that someone
# forgot to sign out of must not stay signed in indefinitely, so the cookie
# is stamped with a 24-hour life at login and is NOT refreshed on activity
# (SESSION_SAVE_EVERY_REQUEST stays False) — that makes it a fixed expiry
# from sign-in, not a rolling idle timeout. After 24 hours the cookie is
# dead, the session is gone, and the next request lands on the login page.
SESSION_COOKIE_AGE = 60 * 60 * 24  # 24 hours, in seconds
SESSION_SAVE_EVERY_REQUEST = False
# Survive a browser restart within the 24-hour window rather than dying the
# moment the tab closes — the expiry is time-based, not tab-based.
SESSION_EXPIRE_AT_BROWSER_CLOSE = False


# --- Crispy forms ---------------------------------------------------------

CRISPY_ALLOWED_TEMPLATE_PACKS = "tailwind"
CRISPY_TEMPLATE_PACK = "tailwind"


# --- Pagination -----------------------------------------------------------
# Rows per page on the list pages.
PAGINATE_BY = 25

# Ledgers and reports are read as a run of figures rather than scanned for one
# row, so they take fewer, longer pages.
PAGINATE_BY_REPORTS = 50


# --- Stock ----------------------------------------------------------------
# A product with `qty <= LOW_STOCK_THRESHOLD` (and > 0) is flagged low on
# every UI that shows stock — the bill card grid, the product list, the
# dashboard alert. Zero and negative are handled separately (out of stock /
# oversold). Adjust here rather than per template.
LOW_STOCK_THRESHOLD = 1000
