"""Transactional security state. No access to business collections."""
import copy
from threading import RLock

from fastapi import HTTPException
from firebase_admin import firestore

SECURITY_COLLECTIONS = {"auth_sessions", "auth_limits"}


class MemorySecurityStore:
    """Injected only by tests/preview. Never selected as a production fallback."""
    def __init__(self):
        self.items = {}
        self.lock = RLock()

    def atomic(self, callback):
        with self.lock:
            working = copy.deepcopy(self.items)

            class Access:
                def get(self, collection, id):
                    assert collection in SECURITY_COLLECTIONS
                    return copy.deepcopy(working.get((collection, id)))

                def set(self, collection, id, data):
                    assert collection in SECURITY_COLLECTIONS
                    working[(collection, id)] = copy.deepcopy(data)

                def delete(self, collection, id):
                    assert collection in SECURITY_COLLECTIONS
                    working.pop((collection, id), None)

            result = callback(Access())
            self.items = working
            return result


class FirestoreSecurityStore:
    def __init__(self, db):
        self.db = db

    def atomic(self, callback):
        try:
            transaction = self.db.transaction(max_attempts=3)

            @firestore.transactional
            def execute(tx):
                writes = []
                db = self.db

                class Access:
                    def reference(self, collection, id):
                        if collection not in SECURITY_COLLECTIONS:
                            raise ValueError("Invalid security collection")
                        return db.collection(collection).document(id)

                    def get(self, collection, id):
                        snapshot = self.reference(collection, id).get(transaction=tx, timeout=15, retry=None)
                        return snapshot.to_dict() if snapshot.exists else None

                    def set(self, collection, id, data):
                        writes.append(("set", self.reference(collection, id), data))

                    def delete(self, collection, id):
                        writes.append(("delete", self.reference(collection, id), None))

                result = callback(Access())
                # All reads complete before any transaction writes are queued.
                for operation, reference, data in writes:
                    if operation == "set":
                        tx.set(reference, data)
                    else:
                        tx.delete(reference)
                return result

            return execute(transaction)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "인증 저장소에 연결할 수 없습니다.") from None
