from __future__ import annotations

import hashlib
import json
from typing import Any

import firebase_admin
from firebase_admin import auth, credentials, firestore, messaging

from ..config import get_settings


class FirebaseHub:
    APP_NAME = "araf-fastapi"

    def __init__(self) -> None:
        settings = get_settings()
        self.app = None
        self.firestore = None
        if not settings.firebase_service_account_json:
            return

        payload = json.loads(settings.firebase_service_account_json)
        cred = credentials.Certificate(payload)
        project_id = settings.firebase_project_id or payload.get("project_id")
        try:
            self.app = firebase_admin.get_app(self.APP_NAME)
        except ValueError:
            options = {"projectId": project_id} if project_id else None
            self.app = firebase_admin.initialize_app(cred, options, name=self.APP_NAME)
        self.firestore = firestore.client(app=self.app)

    @property
    def enabled(self) -> bool:
        return self.app is not None

    def verify_id_token(self, token: str) -> dict[str, Any]:
        if not self.app:
            raise RuntimeError("Firebase Admin authentication is not configured")
        return auth.verify_id_token(token, app=self.app, check_revoked=True)

    async def register_device(self, subject: str, token: str) -> None:
        if self.firestore is None:
            raise RuntimeError("Firebase Firestore is not configured")
        from starlette.concurrency import run_in_threadpool

        token_id = hashlib.sha256(token.encode()).hexdigest()
        reference = self.firestore.collection("users").document(subject).collection("devices").document(token_id)
        await run_in_threadpool(reference.set, {"token": token, "updatedAt": firestore.SERVER_TIMESTAMP})

    async def upsert_preferences(self, subject: str, preferences: dict[str, Any]) -> None:
        if self.firestore is None:
            raise RuntimeError("Firebase Firestore is not configured")
        from starlette.concurrency import run_in_threadpool

        reference = (
            self.firestore.collection("users")
            .document(subject)
            .collection("settings")
            .document("terminal")
        )
        await run_in_threadpool(
            reference.set,
            {"preferences": preferences, "updatedAt": firestore.SERVER_TIMESTAMP},
            merge=True,
        )

    async def get_preferences(self, subject: str) -> dict[str, Any] | None:
        if self.firestore is None:
            raise RuntimeError("Firebase Firestore is not configured")
        from starlette.concurrency import run_in_threadpool

        reference = (
            self.firestore.collection("users")
            .document(subject)
            .collection("settings")
            .document("terminal")
        )
        snapshot = await run_in_threadpool(reference.get)
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        preferences = data.get("preferences")
        return preferences if isinstance(preferences, dict) else None

    async def publish_market_alert(self, item: dict[str, Any]) -> None:
        if self.firestore is None:
            raise RuntimeError("Firebase Firestore is not configured")
        from starlette.concurrency import run_in_threadpool

        await run_in_threadpool(
            self.firestore.collection("market_alerts").add,
            {
                "eventId": item["id"],
                "source": item["source"],
                "title": item["title"],
                "url": item["url"],
                "reasons": item["priorityReasons"],
                "createdAt": firestore.SERVER_TIMESTAMP,
            },
        )

    async def ping(self) -> bool:
        if not self.firestore:
            return False
        from starlette.concurrency import run_in_threadpool

        await run_in_threadpool(lambda: list(self.firestore.collections()))
        return True

    async def send_to_subject(
        self,
        subject: str,
        title: str,
        body: str,
        data: dict[str, str] | None = None,
    ) -> list[str]:
        if self.firestore is None:
            raise RuntimeError("Firebase Firestore is not configured")
        from starlette.concurrency import run_in_threadpool

        collection = self.firestore.collection("users").document(subject).collection("devices")
        documents = await run_in_threadpool(lambda: list(collection.stream()))
        tokens = [doc.get("token") for doc in documents if doc.get("token")]
        notification = {
            "title": title,
            "body": body,
            "data": data or {},
            "createdAt": firestore.SERVER_TIMESTAMP,
            "status": "queued" if tokens else "no_devices",
        }
        notification_collection = (
            self.firestore.collection("users").document(subject).collection("notifications")
        )
        _, notification_reference = await run_in_threadpool(
            notification_collection.add,
            notification,
        )
        if not tokens:
            return []

        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data=data or {},
            tokens=tokens,
        )
        response = await run_in_threadpool(messaging.send_each_for_multicast, message, app=self.app)
        message_ids = [
            result.message_id
            for result in response.responses
            if result.success and result.message_id
        ]
        await run_in_threadpool(
            notification_reference.update,
            {
                "status": "sent" if message_ids else "failed",
                "successCount": response.success_count,
                "failureCount": response.failure_count,
            },
        )
        return message_ids

    async def send(self, token: str, title: str, body: str, data: dict[str, str] | None = None) -> str | None:
        if not self.app:
            raise RuntimeError("Firebase Cloud Messaging is not configured")
        from starlette.concurrency import run_in_threadpool

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=data or {},
            token=token,
        )
        return await run_in_threadpool(messaging.send, message, app=self.app)

    async def delete_user(self, subject: str) -> bool:
        """Delete a Firebase Auth user by their subject (Firebase UID)."""
        if not self.app:
            raise RuntimeError("Firebase Admin SDK is not configured")
        try:
            # Try to get user by UID first
            from starlette.concurrency import run_in_threadpool
            await run_in_threadpool(firebase_admin.auth.delete_user, subject, app=self.app)
            return True
        except firebase_admin.auth.UserNotFoundError:
            # User already deleted
            return True
        except Exception as e:
            logger = __import__("logging").getLogger(__name__)
            logger.exception("Failed to delete Firebase user %s", subject)
            return False
