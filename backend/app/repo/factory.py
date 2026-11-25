from ..config import Settings
from ..firebase import init_firebase
from .firestore import FirestoreRepo
from .memory import MemoryRepo


def build_repo(settings: Settings):
    if settings.repo_backend == "memory":
        return MemoryRepo()

    init_firebase(settings)
    from firebase_admin import firestore

    return FirestoreRepo(firestore.client())
