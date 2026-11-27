from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from ..config import get_settings

logger = logging.getLogger(__name__)
_API_BASE = "https://api.telegram.org"

# In-memory conversation state (in production, use Redis)
_user_conversations: dict[str, dict] = {}

# UTR pattern: alphanumeric, 10-64 chars
UTR_PATTERN = re.compile(r"^[A-Za-z0-9]{10,64}$")


class TelegramBotService:
    """User-facing Telegram bot for subscription payment proof submission."""

    def __init__(self, app: FastAPI):
        self.app = app
        self._session: Optional[httpx.AsyncClient] = None
        self._setup_routes()

    async def _get_session(self) -> httpx.AsyncClient:
        if self._session is None or self._session.is_closed:
            self._session = httpx.AsyncClient(
                timeout=httpx.Timeout(30),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                follow_redirects=True,
            )
        return self._session

    async def close(self):
        if self._session and not self._session.is_closed:
            await self._session.aclose()

    def _setup_routes(self):
        @self.app.post("/telegram/webhook")
        async def telegram_webhook(request: Request):
            return await self._handle_webhook(request)

        @self.app.get("/telegram/health")
        async def telegram_health():
            settings = get_settings()
            return {"status": "ok", "bot_configured": bool(settings.telegram_bot_token)}

    async def _handle_webhook(self, request: Request) -> JSONResponse:
        """Handle incoming Telegram webhook updates."""
        settings = get_settings()
        token = settings.telegram_bot_token.strip()
        if not token:
            return JSONResponse({"ok": False, "error": "Bot not configured"})

        try:
            update = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON"})

        message = update.get("message") or update.get("edited_message")
        if not message:
            return JSONResponse({"ok": True})

        user = message.get("from")
        if not user:
            return JSONResponse({"ok": True})

        user_id = str(user.get("id", ""))
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip()
        photo = message.get("photo")
        document = message.get("document")

        # Get or create conversation state
        conv = _user_conversations.setdefault(user_id, {"state": "idle", "data": {}})

        # Handle commands
        if text.startswith("/"):
            return await self._handle_command(user_id, chat_id, text, conv)

        # Handle conversation flow
        return await self._handle_conversation(user_id, chat_id, text, photo, document, conv)

    async def _handle_command(self, user_id: str, chat_id: str, text: str, conv: dict) -> JSONResponse:
        """Handle bot commands."""
        parts = text.split()
        cmd = parts[0].lower()

        if cmd == "/start":
            return await self._send_welcome(chat_id)
        elif cmd == "/submit_proof":
            return await self._start_proof_submission(user_id, chat_id, conv)
        elif cmd == "/status":
            return await self._check_status(user_id, chat_id)
        elif cmd == "/help":
            return await self._send_help(chat_id)
        elif cmd == "/cancel":
            conv["state"] = "idle"
            conv["data"] = {}
            return await self._send_message(chat_id, "❌ Operation cancelled. Use /submit_proof to start again.")
        else:
            return await self._send_message(chat_id, "Unknown command. Use /help for available commands.")

    async def _handle_conversation(
        self,
        user_id: str,
        chat_id: str,
        text: str,
        photo: list | None,
        document: dict | None,
        conv: dict,
    ) -> JSONResponse:
        """Handle conversation state machine."""
        state = conv["state"]

        if state == "awaiting_months":
            return await self._handle_months(user_id, chat_id, text, conv)
        elif state == "awaiting_utr":
            return await self._handle_utr(user_id, chat_id, text, conv)
        elif state == "awaiting_screenshot":
            return await self._handle_screenshot(user_id, chat_id, photo, document, conv)
        else:
            # Not in a conversation, ignore
            return JSONResponse({"ok": True})

    async def _send_welcome(self, chat_id: str) -> JSONResponse:
        """Send welcome message."""
        text = (
            "👋 Welcome to ARAF Intelligence Subscription Bot!\n\n"
            "This bot helps you submit subscription payment proofs directly via Telegram.\n\n"
            "Available commands:\n"
            "• /submit_proof - Submit a new payment proof\n"
            "• /status - Check your subscription status\n"
            "• /help - Show this help message\n"
            "• /cancel - Cancel current operation\n\n"
            "To get started, use /submit_proof"
        )
        return await self._send_message(chat_id, text)

    async def _send_help(self, chat_id: str) -> JSONResponse:
        """Send help message."""
        text = (
            "📋 <b>ARAF Subscription Bot Help</b>\n\n"
            "<b>How it works:</b>\n"
            "1. Use /submit_proof to start\n"
            "2. Enter subscription months (1-12)\n"
            "3. Enter UPI UTR/URN from your payment\n"
            "4. Upload payment screenshot\n"
            "5. Admin reviews and approves\n\n"
            "<b>Commands:</b>\n"
            "• /submit_proof - Submit payment proof\n"
            "• /status - Check subscription status\n"
            "• /help - Show this help\n"
            "• /cancel - Cancel current operation\n\n"
            "<b>Requirements:</b>\n"
            "• Valid UPI UTR/URN (10-64 alphanumeric)\n"
            "• Clear payment screenshot (JPG/PNG/WebP, max 5MB)\n"
            "• Valid KYC-approved account"
        )
        return await self._send_message(chat_id, text, parse_mode="HTML")

    async def _start_proof_submission(self, user_id: str, chat_id: str, conv: dict) -> JSONResponse:
        """Start the proof submission flow."""
        conv["state"] = "awaiting_months"
        conv["data"] = {}

        text = (
            "📝 <b>Subscription Payment Proof Submission</b>\n\n"
            "Step 1 of 3: How many months? (1-12)\n\n"
            "Reply with a number (e.g., <code>3</code> for 3 months)."
        )
        return await self._send_message(chat_id, text, parse_mode="HTML")

    async def _handle_months(self, user_id: str, chat_id: str, text: str, conv: dict) -> JSONResponse:
        """Handle months input."""
        try:
            months = int(text.strip())
            if not 1 <= months <= 12:
                raise ValueError
        except ValueError:
            return await self._send_message(
                chat_id,
                "❌ Invalid input. Please enter a number between 1 and 12.",
            )

        conv["data"]["months"] = months
        conv["state"] = "awaiting_utr"

        text = (
            f"✅ Months: <b>{months}</b>\n\n"
            "Step 2 of 3: Enter your UPI UTR/URN\n\n"
            "This is the transaction reference from your UPI app (e.g., <code>123456789012</code> or <code>AXI234567890</code>).\n\n"
            "Reply with your UTR/URN:"
        )
        return await self._send_message(chat_id, text, parse_mode="HTML")

    async def _handle_utr(self, user_id: str, chat_id: str, text: str, conv: dict) -> JSONResponse:
        """Handle UTR input."""
        utr = text.strip().upper()
        if not UTR_PATTERN.match(utr):
            return await self._send_message(
                chat_id,
                "❌ Invalid UTR/URN. Must be 10-64 alphanumeric characters.\n\nPlease try again:",
            )

        conv["data"]["utr"] = utr
        conv["state"] = "awaiting_screenshot"

        text = (
            f"✅ UTR/URN: <code>{utr}</code>\n\n"
            "Step 3 of 3: Upload payment screenshot\n\n"
            "Send a clear screenshot of your UPI payment confirmation.\n"
            "Supported formats: JPEG, PNG, WebP (max 5MB)"
        )
        return await self._send_message(chat_id, text, parse_mode="HTML")

    async def _handle_screenshot(
        self, user_id: str, chat_id: str, photo: list | None, document: dict | None, conv: dict
    ) -> JSONResponse:
        """Handle screenshot upload."""
        file_id = None
        filename = "payment-proof.jpg"
        mime_type = "image/jpeg"

        if photo:
            # Get largest photo
            largest = max(photo, key=lambda p: p.get("file_size", 0))
            file_id = largest["file_id"]
            mime_type = "image/jpeg"
            filename = "payment-proof.jpg"
        elif document:
            file_id = document["file_id"]
            filename = document.get("file_name", "payment-proof.jpg")
            mime_type = document.get("mime_type", "image/jpeg")
        else:
            return await self._send_message(
                chat_id,
                "❌ Please send a photo or document as your payment screenshot.",
            )

        # Validate file size and type
        if document and document.get("file_size", 0) > 5 * 1024 * 1024:
            return await self._send_message(chat_id, "❌ File too large. Max 5MB.")

        if document and not document.get("mime_type", "").startswith("image/"):
            return await self._send_message(chat_id, "❌ Unsupported file type. Use JPEG, PNG, or WebP.")

        # Download file from Telegram
        settings = get_settings()
        token = settings.telegram_bot_token.strip()
        file_path = await self._get_telegram_file_path(token, file_id)
        if not file_path:
            return await self._send_message(chat_id, "❌ Failed to download file. Please try again.")

        file_bytes = await self._download_telegram_file(token, file_path)
        if not file_bytes:
            return await self._send_message(chat_id, "❌ Failed to download file. Please try again.")

        # Submit to backend
        months = conv["data"].get("months", 1)
        utr = conv["data"].get("utr")
        subject = conv["data"].get("subject")
        email = conv["data"].get("email")

        if not subject or not email:
            return await self._send_message(
                chat_id,
                "❌ Session expired. Please start over with /submit_proof",
            )

        # Send to backend
        try:
            form = httpx.FormData()
            form.add_field("months", str(months))
            form.add_field("proof", (filename, file_bytes, mime_type))
            if utr:
                form.add_field("utr", utr)

            # We need user's auth token - for now, we'll need to store it or use a different approach
            # For now, we'll respond that manual upload is needed
            await self._send_message(
                chat_id,
                f"✅ Screenshot received! (UTR: <code>{utr}</code>)\n\n"
                f"Your payment proof for {conv['data']['months']} month(s) is ready.\n\n"
                f"⚠️ To complete submission, please use the web app to upload the screenshot.\n"
                f"The UTR <code>{utr}</code> has been saved to your session.\n\n"
                f"Open the web app and go to Settings → Subscription to complete the upload.",
                parse_mode="HTML",
            )

            # Clear conversation
            conv["state"] = "idle"
            conv["data"] = {}

        except Exception as e:
            logger.exception("Failed to process screenshot")
            return await self._send_message(chat_id, "❌ Failed to process. Please try again.")

        return JSONResponse({"ok": True})

    async def _check_status(self, user_id: str, chat_id: str) -> JSONResponse:
        """Check subscription status (placeholder)."""
        conv = _user_conversations.get(user_id, {})
        if conv.get("data"):
            data = conv["data"]
            text = (
                f"📊 <b>Current Session</b>\n\n"
                f"Months: {data.get('months', 'Not set')}\n"
                f"UTR: <code>{data.get('utr', 'Not set')}</code>\n\n"
                f"Use /submit_proof to continue or /cancel to reset."
            )
        else:
            text = (
                "📊 <b>Subscription Status</b>\n\n"
                "No active submission in progress.\n\n"
                "Use /submit_proof to start a new submission.\n"
                "For full status, check the web app."
)
        return await self._send_message(chat_id, text)

    async def _get_telegram_file_path(self, token: str, file_id: str) -> str | None:
        """Get file path from Telegram."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{_API_BASE}/bot{token}/getFile",
                    json={"file_id": file_id},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("ok") and data.get("result", {}).get("file_path"):
                    return data["result"]["file_path"]
        except Exception:
            logger.exception("Failed to get Telegram file path")
        return None

    async def _download_telegram_file(self, token: str, file_path: str) -> bytes | None:
        """Download file from Telegram."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{_API_BASE}/file/bot{token}/{file_path}")
                if resp.status_code == 200:
                    return resp.content
        except Exception:
            logger.exception("Failed to download Telegram file")
        return None

    async def _send_message(
        self, chat_id: str, text: str, parse_mode: str = None
    ) -> JSONResponse:
        """Send message via Telegram Bot API."""
        settings = get_settings()
        token = settings.telegram_bot_token.strip()
        if not token:
            return JSONResponse({"ok": False, "error": "Bot not configured"})

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                data = {"chat_id": chat_id, "text": text}
                if parse_mode:
                    data["parse_mode"] = parse_mode
                resp = await httpx.AsyncClient(timeout=10).post(
                    f"{_API_BASE}/bot{token}/sendMessage",
                    json=data,
                )
                resp.raise_for_status()
                return JSONResponse(resp.json())
        except Exception as e:
            logger.exception("Failed to send Telegram message")
            return JSONResponse({"ok": False, "error": str(e)})


# Global instance getter
_telegram_bot_service: Optional[TelegramBotService] = None


def get_telegram_bot_service(app: FastAPI) -> TelegramBotService:
    global _telegram_bot_service
    if _telegram_bot_service is None:
        _telegram_bot_service = TelegramBotService(app)
    return _telegram_bot_service