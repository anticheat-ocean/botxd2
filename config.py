"""Configuration module for the referral bot."""
import os
from typing import List
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Bot configuration class."""

    # Telegram settings
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))
    # Channel where withdrawal requests are posted for moderation.
    # Bot must be an admin of this channel. Falls back to the admin's private chat if unset.
    ADMIN_CHANNEL_ID: int = int(os.getenv("ADMIN_CHANNEL_ID", "0") or "0")

    @classmethod
    def admin_destination(cls) -> int:
        """Where withdrawal notifications go: the admin channel if set, else admin's DM."""
        return cls.ADMIN_CHANNEL_ID if cls.ADMIN_CHANNEL_ID else cls.ADMIN_ID

    # Reward settings. Locked to the requested payout so stale hosting env vars
    # cannot accidentally keep the old value.
    REWARD_PER_REFERRAL: float = 2.6
    WITHDRAW_AMOUNTS: List[int] = [
        int(x.strip()) for x in os.getenv("WITHDRAW_AMOUNTS", "15,25,50,100").split(",")
    ]
    DAILY_BONUS_AMOUNT: float = float(os.getenv("DAILY_BONUS_AMOUNT", "0.2"))
    DAILY_STREAK_BONUS_DAYS: int = int(os.getenv("DAILY_STREAK_BONUS_DAYS", "7"))
    DAILY_STREAK_BONUS_AMOUNT: float = float(os.getenv("DAILY_STREAK_BONUS_AMOUNT", "1"))
    BOX_GAME_REWARD_AMOUNT: float = float(os.getenv("BOX_GAME_REWARD_AMOUNT", "0.1"))
    BOX_GAME_COOLDOWN_MINUTES: int = int(os.getenv("BOX_GAME_COOLDOWN_MINUTES", "30"))

    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "/app/data/referral_bot.db")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Anti-spam settings
    MIN_WITHDRAW_INTERVAL_HOURS: int = 24  # Минимальный интервал между выводами
    MAX_PENDING_WITHDRAWALS: int = 3  # Максимум активных заявок на вывод

    # Block Arabic accounts (bot is for Russian-speaking users only)
    BLOCK_ARABIC: bool = os.getenv("BLOCK_ARABIC", "true").lower() == "true"
    # Phone-number verification has been retired. Keep this forced off so old
    # hosting environment variables cannot re-enable the contact request.
    PHONE_GATE_ENABLED: bool = False

    # Anti-twink (fake/alt account) protection
    ANTITWINK_ENABLED: bool = os.getenv("ANTITWINK_ENABLED", "true").lower() == "true"
    # If True, a suspected twink blocks the referral reward. If False (default),
    # the referrer is still paid and the admin just gets an alert in DM.
    TWINK_BLOCK_REFERRAL: bool = os.getenv("TWINK_BLOCK_REFERRAL", "false").lower() == "true"
    # Telegram user IDs grow over time. Accounts with an ID at/above this value are
    # treated as "very fresh" — one of several twink signals. Tune in .env if needed.
    NEW_ACCOUNT_ID_THRESHOLD: int = int(os.getenv("NEW_ACCOUNT_ID_THRESHOLD", "8000000000"))

    # PiarFlow API
    PIARFLOW_API_KEY: str = os.getenv("PIARFLOW_API_KEY", "")
    PIARFLOW_ENABLED: bool = os.getenv("PIARFLOW_ENABLED", "false").lower() == "true"
    PIARFLOW_MAX_SPONSORS: int = int(os.getenv("PIARFLOW_MAX_SPONSORS", "10"))

    # Project-owned channels shown before paid/partner sponsor tasks.
    OWNER_CHANNEL_IDS: List[str] = [
        x.strip()
        for x in os.getenv(
            "OWNER_CHANNEL_IDS",
            "5372378314,-1003535579194,-1002749675652"
        ).replace("\n", ",").split(",")
        if x.strip()
    ]
    OWNER_CHANNEL_URLS: List[str] = [
        x.strip()
        for x in os.getenv("OWNER_CHANNEL_URLS", "").replace("\n", ",").split(",")
        if x.strip()
    ]
    OWNER_CHANNEL_NAMES: List[str] = [
        x.strip()
        for x in os.getenv("OWNER_CHANNEL_NAMES", "").replace("\n", "|").split("|")
        if x.strip()
    ]

    @classmethod
    def validate(cls) -> bool:
        """Validate configuration."""
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is not set in .env file")
        if not cls.ADMIN_ID:
            raise ValueError("ADMIN_ID is not set in .env file")
        return True


# Validate config on import
Config.validate()
