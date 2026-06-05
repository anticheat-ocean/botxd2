"""Sponsor channels management and subscription checking."""
from typing import List, Optional, Dict
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
import logging

from config import Config

logger = logging.getLogger(__name__)


class SponsorManager:
    """Manage sponsor channels and check subscriptions."""

    def __init__(self, db):
        self.db = db
        self._channel_meta_cache: Dict[str, Dict] = {}

    def _configured_owner_url(self, index: int) -> str:
        return Config.OWNER_CHANNEL_URLS[index] if index < len(Config.OWNER_CHANNEL_URLS) else ""

    def _configured_owner_name(self, index: int) -> str:
        return Config.OWNER_CHANNEL_NAMES[index] if index < len(Config.OWNER_CHANNEL_NAMES) else ""

    async def _resolve_channel_meta(self, bot: Bot, channel_id: str, index: int, *, owner: bool = False) -> Dict:
        """Resolve a channel title and join URL, falling back gracefully."""
        cache_key = f"{'owner' if owner else 'sponsor'}:{channel_id}:{index}"
        cached = self._channel_meta_cache.get(cache_key)
        if cached:
            return cached

        title = self._configured_owner_name(index) if owner else ""
        url = self._configured_owner_url(index) if owner else ""
        fallback_title = f"Наш канал {index + 1}" if owner else f"Спонсор {index + 1}"

        try:
            chat = await bot.get_chat(channel_id)
            title = title or getattr(chat, "title", None) or getattr(chat, "full_name", None) or fallback_title
            username = getattr(chat, "username", None)
            if not url and username:
                url = f"https://t.me/{username}"
            if not url:
                invite_link = getattr(chat, "invite_link", None)
                if invite_link:
                    url = invite_link
            if not url:
                try:
                    invite = await bot.create_chat_invite_link(
                        chat_id=channel_id,
                        name="Bot access check"
                    )
                    url = invite.invite_link
                except Exception as e:
                    logger.warning(f"Could not create invite link for {channel_id}: {e}")
        except Exception as e:
            logger.warning(f"Could not resolve channel metadata for {channel_id}: {e}")

        meta = {
            "channel_id": str(channel_id),
            "channel_name": title or fallback_title,
            "channel_url": url,
            "has_join_link": bool(url),
        }
        self._channel_meta_cache[cache_key] = meta
        return meta

    async def get_owner_channels(self, bot: Bot) -> List[Dict]:
        """Get configured project-owned channels with display metadata."""
        channels = []
        for i, channel_id in enumerate(Config.OWNER_CHANNEL_IDS):
            channels.append(await self._resolve_channel_meta(bot, channel_id, i, owner=True))
        return channels

    async def add_sponsor(self, channel_id: str, channel_name: str, channel_url: str) -> bool:
        """Add sponsor channel."""
        try:
            await self.db.conn.execute(
                """INSERT INTO sponsors (channel_id, channel_name, channel_url, is_active)
                   VALUES (?, ?, ?, 1)""",
                (channel_id, channel_name, channel_url)
            )
            await self.db.conn.commit()
            logger.info(f"Added sponsor channel: {channel_name} ({channel_id})")
            return True
        except Exception as e:
            logger.error(f"Failed to add sponsor: {e}")
            return False

    async def remove_sponsor(self, channel_id: str) -> bool:
        """Remove sponsor channel."""
        try:
            await self.db.conn.execute(
                "DELETE FROM sponsors WHERE channel_id = ?",
                (channel_id,)
            )
            await self.db.conn.commit()
            logger.info(f"Removed sponsor channel: {channel_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove sponsor: {e}")
            return False

    async def toggle_sponsor(self, channel_id: str) -> bool:
        """Toggle sponsor active status."""
        try:
            await self.db.conn.execute(
                """UPDATE sponsors
                   SET is_active = NOT is_active
                   WHERE channel_id = ?""",
                (channel_id,)
            )
            await self.db.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to toggle sponsor: {e}")
            return False

    async def get_active_sponsors(self) -> List[Dict]:
        """Get all active sponsor channels."""
        try:
            async with self.db.conn.execute(
                """SELECT channel_id, channel_name, channel_url
                   FROM sponsors
                   WHERE is_active = 1
                   ORDER BY id ASC"""
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get sponsors: {e}")
            return []

    async def get_all_sponsors(self) -> List[Dict]:
        """Get all sponsor channels (active and inactive)."""
        try:
            async with self.db.conn.execute(
                """SELECT channel_id, channel_name, channel_url, is_active
                   FROM sponsors
                   ORDER BY id ASC"""
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get all sponsors: {e}")
            return []

    async def check_user_subscription(self, bot: Bot, user_id: int, channel_id: str) -> bool:
        """Check if user is subscribed to a channel."""
        try:
            # Get user's status in the channel
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)

            # Check if user is subscribed (member, administrator, or creator)
            subscribed_statuses = [
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR
            ]

            is_subscribed = member.status in subscribed_statuses
            logger.debug(f"User {user_id} subscription to {channel_id}: {is_subscribed} (status: {member.status})")
            return is_subscribed

        except Exception as e:
            logger.error(f"Failed to check subscription for user {user_id} in {channel_id}: {e}")
            # If we can't check (bot not admin, channel doesn't exist, etc.), assume not subscribed
            return False

    async def check_all_subscriptions(self, bot: Bot, user_id: int) -> tuple[bool, List[Dict]]:
        """
        Check if user is subscribed to all active sponsors.
        Returns: (all_subscribed, list_of_unsubscribed_channels)
        """
        sponsors = await self.get_active_sponsors()

        if not sponsors:
            # No sponsors configured, allow access
            return True, []

        unsubscribed = []

        for sponsor in sponsors:
            is_subscribed = await self.check_user_subscription(bot, user_id, sponsor['channel_id'])
            if not is_subscribed:
                unsubscribed.append(sponsor)

        all_subscribed = len(unsubscribed) == 0
        return all_subscribed, unsubscribed

    async def check_owner_subscriptions(self, bot: Bot, user_id: int) -> tuple[bool, List[Dict]]:
        """
        Check project-owned channels before paid sponsor tasks.
        Returns: (all_subscribed, list_of_unsubscribed_channels)
        """
        channels = await self.get_owner_channels(bot)
        if not channels:
            return True, []

        unsubscribed = []
        for channel in channels:
            is_subscribed = await self.check_user_subscription(bot, user_id, channel["channel_id"])
            if not is_subscribed:
                unsubscribed.append(channel)

        return len(unsubscribed) == 0, unsubscribed

    async def log_subscription_check(self, user_id: int, passed: bool):
        """Log subscription check result."""
        try:
            await self.db.log_activity(
                user_id,
                "subscription_check",
                f"Subscription check: {'passed' if passed else 'failed'}"
            )
            await self.db.conn.commit()
        except Exception as e:
            logger.error(f"Failed to log subscription check: {e}")
