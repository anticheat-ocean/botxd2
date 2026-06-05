"""PiarFlow API integration for sponsor management."""
import aiohttp
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class PiarFlowAPI:
    """PiarFlow API client for sponsor management."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://piarflow.ru/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    async def get_sponsors(self, user_id: int, chat_id: int, max_sponsors: Optional[int] = None) -> List[Dict]:
        """
        Get list of sponsor tasks for user.

        Args:
            user_id: Telegram user ID
            chat_id: Telegram chat ID (bot ID)
            max_sponsors: Maximum number of sponsors to return

        Returns:
            List of sponsors with fields: link, status, price
        """
        try:
            payload = {
                "user_id": user_id,
                "chat_id": chat_id
            }

            if max_sponsors:
                payload["max_sponsors"] = max_sponsors

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/sponsors",
                    headers=self.headers,
                    json=payload
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Sponsors are in the 'sponsors' field
                        sponsors = data.get("sponsors", [])
                        logger.info(f"Got {len(sponsors)} sponsors for user {user_id}")
                        return sponsors
                    elif response.status == 404:
                        # No sponsors available for this user (already completed all)
                        logger.info(f"No sponsors available for user {user_id}")
                        return []
                    else:
                        error_text = await response.text()
                        logger.error(f"PiarFlow API error: {response.status} - {error_text}")
                        return []

        except Exception as e:
            logger.error(f"Failed to get sponsors from PiarFlow: {e}")
            return []

    async def check_sponsors(self, user_id: int, links: List[str]) -> Dict[str, str]:
        """
        Check if user completed sponsor tasks.

        Args:
            user_id: Telegram user ID
            links: List of sponsor links to check

        Returns:
            Dict mapping link to status: 'subscribed', 'unsubscribed', 'not_counted'
        """
        try:
            payload = {
                "user_id": user_id,
                "links": links
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/sponsors/check",
                    headers=self.headers,
                    json=payload
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Response format: {"status": "ok", "sponsors": [{"link": ..., "status": ...}]}
                        sponsors = data.get("sponsors", [])
                        # Convert to dict: {link: status}
                        result = {s['link']: s['status'] for s in sponsors}
                        logger.info(f"Checked {len(links)} sponsors for user {user_id}")
                        return result
                    else:
                        error_text = await response.text()
                        logger.error(f"PiarFlow check error: {response.status} - {error_text}")
                        return {}

        except Exception as e:
            logger.error(f"Failed to check sponsors: {e}")
            return {}

    async def check_user(self, user_id: int) -> bool:
        """
        Check if user is not fake/blocked.

        Args:
            user_id: Telegram user ID

        Returns:
            True if user is valid, False if fake/blocked
        """
        try:
            payload = {"user_id": user_id}

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/users/check",
                    headers=self.headers,
                    json=payload
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        is_fake = data.get("is_fake", False)
                        logger.info(f"User {user_id} check: {'fake' if is_fake else 'valid'}")
                        return not is_fake
                    else:
                        error_text = await response.text()
                        logger.error(f"PiarFlow user check error: {response.status} - {error_text}")
                        return True  # Allow by default if check fails

        except Exception as e:
            logger.error(f"Failed to check user: {e}")
            return True  # Allow by default if check fails


class PiarFlowSponsorManager:
    """Manage PiarFlow sponsors integration."""

    def __init__(self, api: PiarFlowAPI, db):
        self.api = api
        self.db = db

    async def get_user_sponsors(self, user_id: int, bot_id: int, max_sponsors: int = 5) -> List[Dict]:
        """Get sponsor tasks for user from PiarFlow."""
        sponsors = await self.api.get_sponsors(user_id, bot_id, max_sponsors)

        # Save sponsors to database for tracking
        for sponsor in sponsors:
            await self._save_sponsor_task(user_id, sponsor)

        return sponsors

    async def check_user_subscriptions(self, user_id: int) -> tuple[bool, List[Dict]]:
        """
        Check if user completed all sponsor tasks.

        Returns:
            (all_completed, uncompleted_sponsors)
        """
        # Get user's pending sponsor tasks
        sponsors = await self._get_user_pending_tasks(user_id)

        # If no tasks, get new ones
        if not sponsors:
            return True, []

        # Extract links
        links = [s['link'] for s in sponsors]

        # Check with PiarFlow
        statuses = await self.api.check_sponsors(user_id, links)

        # Find uncompleted
        uncompleted = []
        for sponsor in sponsors:
            link = sponsor['link']
            status = statuses.get(link, 'unsubscribed')

            if status == 'subscribed':
                # Mark as completed
                await self._mark_task_completed(user_id, link)
            else:
                uncompleted.append({
                    'link': link,
                    'status': status,
                    'price': sponsor.get('price', 0)
                })

        all_completed = len(uncompleted) == 0
        return all_completed, uncompleted

    async def _save_sponsor_task(self, user_id: int, sponsor: Dict):
        """Save sponsor task to database."""
        try:
            await self.db.conn.execute(
                """INSERT OR IGNORE INTO piarflow_tasks
                   (user_id, link, price, status)
                   VALUES (?, ?, ?, 'pending')""",
                (user_id, sponsor['link'], sponsor.get('price', 0))
            )
            await self.db.conn.commit()
        except Exception as e:
            logger.error(f"Failed to save sponsor task: {e}")

    async def _get_user_pending_tasks(self, user_id: int) -> List[Dict]:
        """Get user's pending sponsor tasks."""
        try:
            async with self.db.conn.execute(
                """SELECT link, price FROM piarflow_tasks
                   WHERE user_id = ? AND status = 'pending'""",
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [{'link': row[0], 'price': row[1]} for row in rows]
        except Exception as e:
            logger.error(f"Failed to get pending tasks: {e}")
            return []

    async def _mark_task_completed(self, user_id: int, link: str):
        """Mark sponsor task as completed."""
        try:
            await self.db.conn.execute(
                """UPDATE piarflow_tasks
                   SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                   WHERE user_id = ? AND link = ?""",
                (user_id, link)
            )
            await self.db.conn.commit()
            logger.info(f"Marked task {link} as completed for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to mark task completed: {e}")
