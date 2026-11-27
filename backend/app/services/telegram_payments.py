from __future__ import annotations

import base64
import asyncio
import logging
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)
_API_BASE = "https://api.telegram.org"
_payment_claims: set[str] = set()


def _subject_token(subject: str) -> str:
    return base64.urlsafe_b64encode(subject.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_subject(token: str) -> str:
    padding = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + padding).decode("utf-8")


def parse_callback(callback_data: str) -> tuple[str, str] | None:
    try:
        action, payment_id = callback_data.split("|", 1)
        if action not in {"APPROVE", "REJECT"} or not payment_id:
            return None
        return action, payment_id
    except ValueError:
        return None


async def send_payment_proof(
    subject: str,
    email: str,
    months: int,
    image_bytes: bytes,
    filename: str,
    amount_inr: float,
    payment_id: str,
    utr: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    token = settings.telegram_bot_token.strip()
    chat_id = settings.telegram_admin_chat_id.strip()
    channel_id = getattr(settings, "telegram_admin_channel_id", "").strip()
    if not token or (not chat_id and not channel_id):
        raise RuntimeError("Telegram payment bridge is not configured")

    if len(f"APPROVE|{payment_id}") > 64:
        raise RuntimeError("Payment callback identifier is too long")

    utr_line = f"\nUTR/URN: {utr}" if utr else ""
    caption = (
        "\u2705 ARAF subscription payment proof\n"
        f"User Email: {email}\n"
        f"User ID: {subject}\n"
        f"Months: {months}\n"
        f"Amount: INR {amount_inr:.2f}{utr_line}"
    )
    data = {
        "chat_id": channel_id if channel_id else chat_id,
        "caption": caption,
        "reply_markup": {
            "inline_keyboard": [[
                {
                    "text": f"APPROVE {email} {months}",
                    "callback_data": f"APPROVE|{payment_id}",
                },
                {
                    "text": f"REJECT {email}",
                    "callback_data": f"REJECT|{payment_id}",
                },
            ]]
        },
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{_API_BASE}/bot{token}/sendPhoto",
            data={"chat_id": channel_id if channel_id else chat_id, "caption": caption},
            files={"photo": (filename, image_bytes, "application/octet-stream")},
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram rejected the payment proof")

        message = payload["result"]
        edit_response = await client.post(
            f"{_API_BASE}/bot{token}/editMessageReplyMarkup",
            json={
                "chat_id": channel_id if channel_id else chat_id,
                "message_id": message["message_id"],
                "reply_markup": data["reply_markup"],
            },
        )
        edit_response.raise_for_status()
        edited = edit_response.json()
        if not edited.get("ok"):
            raise RuntimeError("Telegram rejected payment approval buttons")
    return {"message_id": message["message_id"]}


async def telegram_callback_worker(app) -> None:
    settings = get_settings()
    token = settings.telegram_bot_token.strip()
    chat_id = settings.telegram_admin_chat_id.strip()
    channel_id = getattr(settings, "telegram_admin_channel_id", "").strip()
    if not token or (not chat_id and not channel_id):
        logger.info("Telegram payment polling is disabled: credentials are not configured")
        return

    allowed_chat_ids = {chat_id} if chat_id else set()
    if channel_id:
        allowed_chat_ids.add(channel_id)

    offset = None
    async with httpx.AsyncClient(timeout=25) as client:
        while True:
            try:
                params = {"timeout": 20}
                if offset is not None:
                    params["offset"] = offset
                response = await client.post(
                    f"{_API_BASE}/bot{token}/getUpdates",
                    json=params,
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok"):
                    await asyncio.sleep(5)
                    continue

                for update in payload.get("result", []):
                    offset = int(update["update_id"]) + 1
                    callback = update.get("callback_query")
                    if not callback:
                        continue
                    callback_chat = str(
                        callback.get("message", {}).get("chat", {}).get("id", "")
                    )
                    if callback_chat not in allowed_chat_ids:
                        continue
                    parsed = parse_callback(str(callback.get("data", "")))
                    if not parsed:
                        await client.post(
                            f"{_API_BASE}/bot{token}/answerCallbackQuery",
                            json={
                                "callback_query_id": callback["id"],
                                "text": "Invalid or unsupported payment action.",
                                "show_alert": True,
                            },
                        )
                        continue

                    action, payment_id = parsed
                    try:
                        redis_claim_key = f"telegram:payment:claim:{payment_id}"
                        claimed = True
                        if app.state.redis.enabled:
                            claimed = await app.state.redis.set_if_absent(
                                redis_claim_key,
                                action,
                                ttl_seconds=86400,
                            )
                        elif payment_id in _payment_claims:
                            claimed = False
                        else:
                            _payment_claims.add(payment_id)
                        if not claimed:
                            message = "This payment request was already processed."
                        else:
                            payment = await app.state.database.claim_subscription_payment(
                                payment_id,
                                action,
                            )
                            if payment is None:
                                message = "This payment request was already processed."
                            else:
                                subject = payment.user_id
                                months = int(payment.months)
                                if action == "APPROVE":
                                    from ..routers.ghost_admin import activate_subject_by_months

                                    await activate_subject_by_months(
                                        app.state.database,
                                        app.state.risk_guardian,
                                        subject,
                                        months,
                                    )
                                    # Send push notification for instant unlock
                                    try:
                                        if hasattr(app.state, "firebase") and app.state.firebase.enabled:
                                            await app.state.firebase.send_to_subject(
                                                subject,
                                                {
                                                    "title": "Subscription Approved ✅",
                                                    "body": f"Your {months}-month Pro subscription is now active. Tap to open Pro Desk.",
                                                    "data": {"type": "subscription_approved", "months": str(months)},
                                                }
                                            )
                                    except Exception:
                                        logger.warning("Failed to send push notification for subscription approval")
                                    message = f"Approved: {months}-month subscription activated."
                                else:
                                    user = await app.state.database.get_app_user(subject)
                                    if user is not None:
                                        user.is_active = False
                                        await app.state.database.upsert_app_user(
                                            external_subject=user.external_subject,
                                            provider=user.provider,
                                            email=user.email,
                                            display_name=user.display_name,
                                            trader_uid=user.trader_uid,
                                            role=user.role,
                                            kyc_status=user.kyc_status,
                                            is_active=False,
                                            is_vip_free=user.is_vip_free,
                                            is_subscribed=user.is_subscribed,
                                            subscription_expires_at=user.subscription_expires_at,
                                        )
                                        app.state.risk_guardian.sync_user_access(subject, False)
                                    message = "Payment proof rejected."
                    except Exception:
                        logger.exception("Telegram payment callback could not be processed")
                        message = "Approval failed. Check server logs and retry."

                    await client.post(
                        f"{_API_BASE}/bot{token}/answerCallbackQuery",
                        json={
                            "callback_query_id": callback["id"],
                            "text": message,
                            "show_alert": action == "APPROVE",
                        },
                    )
            except (httpx.HTTPError, httpx.TimeoutException, RuntimeError):
                logger.exception("Telegram payment polling failed")
            await asyncio.sleep(2)
