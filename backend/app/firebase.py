import logging
import os

from .config import Settings


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO, and the Realtime Database calls
    # carry their OAuth token in the query string.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def init_firebase(settings: Settings):
    """Initialise firebase-admin once and return the app."""
    import firebase_admin
    from firebase_admin import credentials
    from google.auth.credentials import AnonymousCredentials

    if firebase_admin._apps:  # already initialised (tests / reload)
        return firebase_admin.get_app()

    options: dict = {}
    if settings.project_id:
        options["projectId"] = settings.project_id

    if settings.credentials_file and os.path.exists(settings.credentials_file):
        cred = credentials.Certificate(settings.credentials_file)
    else:
        # Falls back to GOOGLE_APPLICATION_CREDENTIALS, or to the emulator
        # (FIREBASE_AUTH_EMULATOR_HOST / FIRESTORE_EMULATOR_HOST) where the
        # SDK accepts anonymous credentials. That class lives in google-auth,
        # not in firebase_admin.credentials.
        if os.environ.get("FIRESTORE_EMULATOR_HOST") or os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"):
            cred = AnonymousCredentials()
        else:
            cred = credentials.ApplicationDefault()

    return firebase_admin.initialize_app(cred, options or None)
