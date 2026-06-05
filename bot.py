"""
Improved Telegram Referral Bot
Supports referral rewards and Telegram Stars withdrawals
"""
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import Config
from database import Database
from sponsors import SponsorManager
from sponsor_middleware import SponsorCheckMiddleware
from piarflow import PiarFlowAPI, PiarFlowSponsorManager
import handlers
import admin_handlers
import sponsor_handlers
import verification

# Configure logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


async def main():
    """Main bot function."""
    # Initialize bot and dispatcher
    bot = Bot(
        token=Config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # Initialize database
    db = Database()
    await db.connect()

    # Initialize sponsor manager
    sponsor_manager = SponsorManager(db)

    # Initialize PiarFlow (if enabled)
    piarflow_manager = None
    if Config.PIARFLOW_ENABLED and Config.PIARFLOW_API_KEY:
        piarflow_api = PiarFlowAPI(Config.PIARFLOW_API_KEY)
        piarflow_manager = PiarFlowSponsorManager(piarflow_api, db)
        logger.info("PiarFlow integration enabled")
    else:
        logger.info("PiarFlow integration disabled")

    # Create the sponsor gate middleware
    sponsor_middleware = SponsorCheckMiddleware(sponsor_manager, piarflow_manager)

    # Inject dependencies FIRST so they're available to every later middleware/handler
    @dp.message.middleware()
    @dp.callback_query.middleware()
    async def inject_dependencies(handler, event, data):
        data['db'] = db
        data['bot'] = bot
        data['sponsor_manager'] = sponsor_manager
        data['piarflow_manager'] = piarflow_manager
        data['sponsor_middleware'] = sponsor_middleware
        return await handler(event, data)

    # Then register the sponsor gate (runs after dependencies are injected)
    dp.message.middleware(sponsor_middleware)
    dp.callback_query.middleware(sponsor_middleware)

    # Register routers
    dp.include_router(handlers.router)
    dp.include_router(verification.router)
    dp.include_router(admin_handlers.router)
    dp.include_router(sponsor_handlers.router)

    # Log startup
    bot_info = await bot.get_me()
    logger.info("=" * 50)
    logger.info(f"Bot started successfully!")
    logger.info(f"Bot username: @{bot_info.username}")
    logger.info(f"Reward per referral: {Config.REWARD_PER_REFERRAL} Stars")
    logger.info(f"Withdrawal amounts: {Config.WITHDRAW_AMOUNTS}")
    logger.info(f"Admin ID: {Config.ADMIN_ID}")
    logger.info("=" * 50)

    try:
        # Start polling
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        # Cleanup
        await db.close()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
