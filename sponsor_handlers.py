"""Admin handlers for sponsor management."""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import Config
from sponsors import SponsorManager

router = Router()
logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    return user_id == Config.ADMIN_ID


@router.message(Command("sponsors"))
async def list_sponsors(message: Message, sponsor_manager: SponsorManager):
    """List all sponsor channels."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    sponsors = await sponsor_manager.get_all_sponsors()

    if not sponsors:
        await message.answer(
            "📋 *Список спонсоров пуст*\n\n"
            "Добавьте спонсорский канал:\n"
            "`/addsponsor @channel_username Название канала`\n\n"
            "Пример:\n"
            "`/addsponsor @mychannel Мой канал`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    text = "📋 *Список спонсорских каналов:*\n\n"
    for i, sponsor in enumerate(sponsors, 1):
        status = "✅" if sponsor['is_active'] else "❌"
        text += (
            f"{i}. {status} *{sponsor['channel_name']}*\n"
            f"   ID: `{sponsor['channel_id']}`\n"
            f"   URL: {sponsor['channel_url']}\n\n"
        )

    text += (
        "\n*Команды:*\n"
        "`/addsponsor @username Название` - добавить\n"
        "`/removesponsor @username` - удалить\n"
        "`/togglesponsor @username` - вкл/выкл"
    )

    await message.answer(text, parse_mode=ParseMode.MARKDOWN)


@router.message(Command("addsponsor"))
async def add_sponsor(message: Message, sponsor_manager: SponsorManager, bot):
    """Add sponsor channel."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "❌ Неверный формат\n\n"
            "Использование:\n"
            "`/addsponsor @channel_username Название канала`\n\n"
            "Пример:\n"
            "`/addsponsor @mychannel Мой крутой канал`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    channel_username = args[1]
    channel_name = args[2]

    # Remove @ if present
    if channel_username.startswith('@'):
        channel_username = channel_username[1:]

    # Try to get chat info to verify bot is admin
    try:
        chat = await bot.get_chat(f"@{channel_username}")
        channel_id = str(chat.id)

        # Check if bot is admin
        bot_member = await bot.get_chat_member(chat.id, bot.id)
        if bot_member.status not in ['administrator', 'creator']:
            await message.answer(
                f"⚠️ *Внимание!*\n\n"
                f"Бот не является администратором канала @{channel_username}\n\n"
                f"Добавьте бота в администраторы канала, иначе проверка подписки не будет работать!",
                parse_mode=ParseMode.MARKDOWN
            )

        # Add sponsor
        channel_url = f"https://t.me/{channel_username}"
        success = await sponsor_manager.add_sponsor(channel_id, channel_name, channel_url)

        if success:
            await message.answer(
                f"✅ *Спонсор добавлен!*\n\n"
                f"📢 Канал: {channel_name}\n"
                f"🆔 ID: `{channel_id}`\n"
                f"🔗 Ссылка: {channel_url}\n\n"
                f"Теперь пользователи должны подписаться на этот канал для использования бота.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await message.answer("❌ Ошибка при добавлении спонсора (возможно, уже существует)")

    except Exception as e:
        logger.error(f"Failed to add sponsor: {e}")
        await message.answer(
            f"❌ *Ошибка при добавлении спонсора*\n\n"
            f"Возможные причины:\n"
            f"• Канал @{channel_username} не существует\n"
            f"• Канал приватный и бот не добавлен\n"
            f"• Неверное имя канала\n\n"
            f"Убедитесь, что:\n"
            f"1. Канал существует\n"
            f"2. Бот добавлен в канал как администратор\n"
            f"3. Имя канала указано правильно",
            parse_mode=ParseMode.MARKDOWN
        )


@router.message(Command("removesponsor"))
async def remove_sponsor(message: Message, sponsor_manager: SponsorManager, bot):
    """Remove sponsor channel."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ Неверный формат\n\n"
            "Использование:\n"
            "`/removesponsor @channel_username`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    channel_username = args[1]
    if channel_username.startswith('@'):
        channel_username = channel_username[1:]

    try:
        chat = await bot.get_chat(f"@{channel_username}")
        channel_id = str(chat.id)

        success = await sponsor_manager.remove_sponsor(channel_id)

        if success:
            await message.answer(
                f"✅ Спонсор @{channel_username} удален",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await message.answer("❌ Ошибка при удалении спонсора")

    except Exception as e:
        logger.error(f"Failed to remove sponsor: {e}")
        await message.answer(f"❌ Канал @{channel_username} не найден")


@router.message(Command("togglesponsor"))
async def toggle_sponsor(message: Message, sponsor_manager: SponsorManager, bot):
    """Toggle sponsor active status."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ Неверный формат\n\n"
            "Использование:\n"
            "`/togglesponsor @channel_username`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    channel_username = args[1]
    if channel_username.startswith('@'):
        channel_username = channel_username[1:]

    try:
        chat = await bot.get_chat(f"@{channel_username}")
        channel_id = str(chat.id)

        success = await sponsor_manager.toggle_sponsor(channel_id)

        if success:
            await message.answer(
                f"✅ Статус спонсора @{channel_username} изменен",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await message.answer("❌ Ошибка при изменении статуса")

    except Exception as e:
        logger.error(f"Failed to toggle sponsor: {e}")
        await message.answer(f"❌ Канал @{channel_username} не найден")


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery, sponsor_manager: SponsorManager, bot, sponsor_middleware=None):
    """Handle subscription check button."""
    user_id = callback.from_user.id

    # Check subscriptions
    all_subscribed, unsubscribed = await sponsor_manager.check_all_subscriptions(bot, user_id)

    if all_subscribed:
        # User is now subscribed to all channels
        if sponsor_middleware:
            sponsor_middleware.invalidate(user_id)
        await callback.message.edit_text(
            "✅ Отлично!\n\n"
            "Вы подписались на все каналы.\n"
            "Теперь вы можете пользоваться ботом.\n\n"
            "Нажмите /start для продолжения"
        )
        await callback.answer("✅ Проверка пройдена!", show_alert=False)
    else:
        # Still not subscribed to some channels
        await callback.answer(
            f"❌ Вы подписались не на все каналы!\n"
            f"Осталось: {len(unsubscribed)}",
            show_alert=True
        )


@router.callback_query(F.data == "check_owner_channels")
async def check_owner_channels_callback(callback: CallbackQuery, sponsor_manager: SponsorManager, bot, sponsor_middleware=None):
    """Handle the project-owned channels check button."""
    user_id = callback.from_user.id
    all_subscribed, unsubscribed = await sponsor_manager.check_owner_subscriptions(bot, user_id)

    if all_subscribed:
        if sponsor_middleware:
            sponsor_middleware.invalidate(user_id)
        await callback.message.edit_text(
            "✅ <b>Отлично, наши каналы готовы!</b>\n\n"
            "Теперь остался второй шаг — спонсорские каналы.\n\n"
            "Нажмите /start, и бот покажет следующий список.",
            parse_mode=ParseMode.HTML,
        )
        await callback.answer("✅ Проверка пройдена!", show_alert=False)
    else:
        await callback.answer(
            f"❌ Вы подписались не на все наши каналы!\n"
            f"Осталось: {len(unsubscribed)}",
            show_alert=True
        )


@router.callback_query(F.data == "owner_channel_no_link")
async def owner_channel_no_link(callback: CallbackQuery):
    """Explain that an owner channel has no join link available."""
    await callback.answer(
        "У этого канала нет ссылки для входа. Добавьте бота админом в канал или укажите OWNER_CHANNEL_URLS в переменных.",
        show_alert=True,
    )


@router.callback_query(F.data == "check_piarflow")
async def check_piarflow_callback(callback: CallbackQuery, piarflow_manager, bot, sponsor_middleware=None):
    """Handle PiarFlow tasks check button."""
    user_id = callback.from_user.id

    if not piarflow_manager:
        await callback.answer("❌ PiarFlow не настроен", show_alert=True)
        return

    # Check PiarFlow tasks
    try:
        all_completed, uncompleted = await piarflow_manager.check_user_subscriptions(user_id)
    except Exception as e:
        logger.error(f"PiarFlow check failed for {user_id}: {e}")
        await callback.answer(
            "⏳ Сервис проверки временно недоступен, попробуйте через минуту.",
            show_alert=True
        )
        return

    if all_completed:
        # User completed all tasks
        if sponsor_middleware:
            sponsor_middleware.invalidate(user_id)
        await callback.message.edit_text(
            "✅ Отлично!\n\n"
            "Вы подписались на всех спонсоров.\n"
            "Теперь вы можете пользоваться ботом.\n\n"
            "Нажмите /start для продолжения"
        )
        await callback.answer("✅ Проверка пройдена!", show_alert=False)
    else:
        # Still has uncompleted tasks
        await callback.answer(
            f"❌ Вы подписались не на все каналы!\n"
            f"Осталось: {len(uncompleted)}",
            show_alert=True
        )


async def _get_user_total_earned(piarflow_manager, user_id: int) -> int:
    """Get total amount earned by user from completed tasks."""
    try:
        async with piarflow_manager.db.conn.execute(
            """SELECT SUM(price) FROM piarflow_tasks
               WHERE user_id = ? AND status = 'completed'""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else 0
    except:
        return 0


@router.message(Command("piarflow"))
async def piarflow_status(message: Message, piarflow_manager):
    """Show PiarFlow integration status."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    if not piarflow_manager:
        await message.answer(
            "❌ *PiarFlow не настроен*\n\n"
            "Для включения:\n"
            "1. Получите API ключ на https://piarflow.ru\n"
            "2. Добавьте в .env файл:\n"
            "```\n"
            "PIARFLOW_API_KEY=ваш_ключ\n"
            "PIARFLOW_ENABLED=true\n"
            "```\n"
            "3. Перезапустите бота",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Get statistics
    try:
        async with piarflow_manager.db.conn.execute(
            "SELECT COUNT(*), SUM(price) FROM piarflow_tasks WHERE status = 'completed'"
        ) as cursor:
            row = await cursor.fetchone()
            completed_tasks = row[0] if row else 0
            total_earned = row[1] if row and row[1] else 0

        async with piarflow_manager.db.conn.execute(
            "SELECT COUNT(*) FROM piarflow_tasks WHERE status = 'pending'"
        ) as cursor:
            row = await cursor.fetchone()
            pending_tasks = row[0] if row else 0

        await message.answer(
            "📊 *Статистика PiarFlow*\n\n"
            "✅ *Статус:* Активен\n\n"
            f"📋 Выполнено заданий: {completed_tasks}\n"
            f"⏳ В процессе: {pending_tasks}\n"
            f"💰 Всего заработано: {total_earned} ₽\n\n"
            "Пользователи автоматически получают задания при входе в бота.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Failed to get PiarFlow stats: {e}")
        await message.answer("❌ Ошибка при получении статистики")
