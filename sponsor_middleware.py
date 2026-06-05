"""Middleware for checking sponsor subscriptions (PiarFlow + manual channels)."""
import time
from typing import Callable, Dict, Any, Awaitable, Optional
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
import logging

from config import Config

logger = logging.getLogger(__name__)

# How long (seconds) a successful subscription check stays valid before re-checking.
# Prevents hammering the PiarFlow API (which rate-limits) on every button press.
CHECK_CACHE_TTL = 300  # 5 minutes


class SponsorCheckMiddleware(BaseMiddleware):
    """Gate access behind sponsor subscriptions before any handler runs."""

    def __init__(self, sponsor_manager, piarflow_manager=None):
        super().__init__()
        self.sponsor_manager = sponsor_manager
        self.piarflow_manager = piarflow_manager
        # Cache: user_id -> timestamp when they last passed all checks
        self._passed_cache: Dict[int, float] = {}
        # Cache the bot's own id so we don't call get_me() on every update
        self._bot_id: Optional[int] = None
        # Callbacks that must always be allowed (the "I subscribed" buttons)
        self.exempt_callbacks = {"check_subscription", "check_piarflow"}

    async def _get_bot_id(self, bot) -> int:
        if self._bot_id is None:
            me = await bot.get_me()
            self._bot_id = me.id
        return self._bot_id

    def _mark_passed(self, user_id: int):
        self._passed_cache[user_id] = time.monotonic()

    def _recently_passed(self, user_id: int) -> bool:
        ts = self._passed_cache.get(user_id)
        return ts is not None and (time.monotonic() - ts) < CHECK_CACHE_TTL

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None and isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
        user_id = user.id if user else None
        bot = data.get("bot")

        # No user context (channel posts etc.) -> just pass through
        if user_id is None or bot is None:
            return await handler(event, data)

        # Admin is never gated (check before Arabic block so admin is never locked out)
        if user_id == Config.ADMIN_ID:
            return await handler(event, data)

        # Block Arabic accounts entirely
        from verification import is_arabic_account, reject_arabic
        if is_arabic_account(user):
            if isinstance(event, CallbackQuery):
                await event.answer("🚫 Доступ запрещён", show_alert=True)
            else:
                await reject_arabic(bot, user_id)
            return None

        # Greet EVERYONE on /start, before the subscription wall or captcha.
        if isinstance(event, Message) and event.text:
            first_token = event.text.split()[0].split("@")[0]
            if first_token == "/start":
                from verification import send_welcome
                await send_welcome(bot, user_id, user.first_name or "")

        # Banned users are blocked from everything
        db = data.get("db")
        if db is not None and await db.is_user_banned(user_id):
            await self._show_banned(event)
            return None

        # Phone-number country gate: block until the user shares an allowed phone.
        # /start (to trigger the request) and the shared contact itself must pass through.
        if Config.PHONE_GATE_ENABLED and db is not None:
            is_start = (
                isinstance(event, Message) and bool(event.text)
                and event.text.split()[0].split("@")[0] == "/start"
            )
            is_contact = isinstance(event, Message) and event.contact is not None
            if not is_start and not is_contact and not await db.is_phone_verified(user_id):
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        "📱 Сначала подтвердите номер телефона — нажмите /start",
                        show_alert=True,
                    )
                else:
                    from verification import request_phone
                    await request_phone(bot, user_id)
                return None

        # The "check subscription" buttons must always reach their handler
        if isinstance(event, CallbackQuery) and event.data in self.exempt_callbacks:
            return await handler(event, data)

        # If user passed recently, skip re-checking (avoids API rate limits)
        if self._recently_passed(user_id):
            return await handler(event, data)

        # Run the gate
        passed = await self._run_gate(event, data, bot, user_id)
        if passed:
            self._mark_passed(user_id)
            return await handler(event, data)
        # Not passed: gate already replied to the user, stop here
        return None

    async def _run_gate(self, event, data, bot, user_id: int) -> bool:
        """Return True if the user may proceed, otherwise reply with the wall and return False."""

        # ---- 1. PiarFlow tasks ----
        if self.piarflow_manager:
            uncompleted = await self._get_piarflow_uncompleted(bot, user_id)
            if uncompleted:
                await self._show_wall(
                    event,
                    user_id,
                    links=[t["link"] for t in uncompleted],
                    check_callback="check_piarflow",
                )
                return False

        # ---- 2. Manual sponsor channels ----
        all_subscribed, unsubscribed = await self.sponsor_manager.check_all_subscriptions(bot, user_id)
        if not all_subscribed:
            await self._show_wall(
                event,
                user_id,
                links=[s["channel_url"] for s in unsubscribed],
                labels=[s["channel_name"] for s in unsubscribed],
                check_callback="check_subscription",
            )
            return False

        await self.sponsor_manager.log_subscription_check(user_id, True)
        return True

    async def _get_piarflow_uncompleted(self, bot, user_id: int):
        """Get user's uncompleted PiarFlow tasks, fetching new ones if needed. Fail-open on API errors."""
        try:
            pending = await self.piarflow_manager._get_user_pending_tasks(user_id)

            # No tasks stored yet -> ask PiarFlow for a fresh batch
            if not pending:
                bot_id = await self._get_bot_id(bot)
                sponsors = await self.piarflow_manager.get_user_sponsors(
                    user_id, bot_id, Config.PIARFLOW_MAX_SPONSORS
                )
                if sponsors:
                    logger.info(f"Assigned {len(sponsors)} PiarFlow tasks to user {user_id}")
                # If PiarFlow has nothing for this user, that's fine -> no wall
                pending = await self.piarflow_manager._get_user_pending_tasks(user_id)

            if not pending:
                return []

            all_completed, uncompleted = await self.piarflow_manager.check_user_subscriptions(user_id)
            return uncompleted if not all_completed else []

        except Exception as e:
            # API down / rate-limited: do NOT lock the user out, just skip PiarFlow this time
            logger.warning(f"PiarFlow check skipped for user {user_id}: {e}")
            return []

    async def _show_wall(self, event, user_id: int, links, check_callback, labels=None):
        """Render the subscription wall. No Markdown -> links with '+' won't break parsing."""
        await self.sponsor_manager.log_subscription_check(user_id, False)

        keyboard_buttons = []
        for i, link in enumerate(links, 1):
            label = labels[i - 1] if labels else f"Спонсор {i}"
            keyboard_buttons.append([InlineKeyboardButton(text=f"📢 {label}", url=link)])

        keyboard_buttons.append([
            InlineKeyboardButton(text="✅ Я подписался — проверить", callback_data=check_callback)
        ])
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        text = (
            "🔒 Чтобы пользоваться ботом, подпишитесь на спонсоров\n\n"
            "Подпишитесь на все каналы по кнопкам ниже, "
            "затем нажмите «Я подписался — проверить».\n\n"
            f"📋 Осталось подписок: {len(links)}\n\n"
            "⚠️ Не отписывайтесь — иначе доступ закроется."
        )

        try:
            if isinstance(event, CallbackQuery):
                try:
                    await event.message.edit_text(text, reply_markup=keyboard)
                except TelegramBadRequest:
                    # Message not modified or can't edit -> send fresh
                    await event.message.answer(text, reply_markup=keyboard)
                await event.answer("❌ Вы подписались не на все каналы!", show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Failed to show sponsor wall to {user_id}: {e}")

    async def _show_banned(self, event):
        """Tell a banned user they're blocked."""
        text = "🚫 Вы заблокированы и не можете пользоваться ботом."
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
        except Exception as e:
            logger.error(f"Failed to show banned notice: {e}")

    def invalidate(self, user_id: int):
        """Drop the pass cache for a user (called after a successful manual re-check)."""
        self._passed_cache.pop(user_id, None)
