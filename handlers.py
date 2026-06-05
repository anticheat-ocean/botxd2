"""User handlers for the bot."""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import Database
from keyboards import (
    main_keyboard, withdraw_keyboard, back_button,
    balance_keyboard, referral_keyboard, notification_keyboard,
    farm_keyboard, box_game_keyboard
)
from utils import format_datetime, format_status, get_user_display_name, esc, fmt_amount
from config import Config
from verification import send_captcha, complete_onboarding, request_phone

router = Router()
logger = logging.getLogger(__name__)


class PromoActivateStates(StatesGroup):
    """FSM state while a user is entering a promo code."""
    waiting = State()


def _format_wait(seconds: int) -> str:
    """Format cooldown seconds for user-facing messages."""
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин"
    return "меньше минуты"


async def _farm_menu_text(db: Database, user_id: int) -> str:
    user = await db.get_user(user_id)
    if not user:
        return (
            "🎮 <b>Фарм Stars</b>\n\n"
            "Сначала нажми /start, чтобы бот создал твой профиль."
        )

    daily = await db.get_daily_bonus_status(user_id)
    boxes = await db.get_box_game_status(user_id)

    daily_line = (
        "можно забрать сейчас"
        if daily["can_claim"]
        else f"через {_format_wait(daily['seconds_left'])}"
    )
    boxes_line = (
        "можно играть сейчас"
        if boxes["can_play"]
        else f"через {_format_wait(boxes['seconds_left'])}"
    )

    return (
        "🎮 <b>Фарм Stars</b>\n\n"
        "Тут можно чуть-чуть добирать звёзды между рефералами.\n"
        "Главный фарм всё равно через друзей, но эти штуки держат темп.\n\n"
        f"🎁 Ежедневный бонус: <b>{daily_line}</b>\n"
        f"🔥 Серия входов: <b>{daily['streak']} дн.</b>\n"
        f"🎲 Звёздные коробки: <b>{boxes_line}</b>\n\n"
        f"💰 Баланс: <b>{fmt_amount(user['balance'] if user else 0)} ⭐</b>"
    )


@router.message(Command("start"))
async def cmd_start(message: Message, db: Database, bot, piarflow_manager):
    """Handle /start command.

    The welcome text is sent earlier by the middleware (before the sponsor gate),
    so here we only handle registration, captcha and the main menu.
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name or "User"

    # Extract referral code
    args = message.text.split()
    referrer_code = args[1] if len(args) > 1 else None

    existing = await db.get_user(user_id)

    if existing:
        await db.update_last_active(user_id)
        # Phone gate: require number before anything else
        if Config.PHONE_GATE_ENABLED and not existing.get('phone_verified'):
            await request_phone(bot, user_id)
            return
        # Existing but never passed the captcha -> show it again
        if Config.ANTITWINK_ENABLED and not existing.get('verified'):
            await send_captcha(bot, user_id, user_id)
            return
        await message.answer(
            f"👋 С возвращением, {first_name}!\n\n"
            f"💰 Ваш баланс: {fmt_amount(existing['balance'])} ⭐\n"
            f"👥 Приглашено друзей: {existing['total_referrals']}",
            reply_markup=main_keyboard()
        )
        logger.info(f"User {user_id} returned to bot")
        return

    # Register new user (referrer reward is deferred until captcha + anti-twink pass)
    success, referrer_id = await db.register_user(user_id, username, first_name, referrer_code)
    if not success:
        return
    logger.info(f"New user {user_id} registered with referrer {referrer_id}")

    # Phone gate first for brand-new users
    if Config.PHONE_GATE_ENABLED:
        await request_phone(bot, user_id)
        return

    if Config.ANTITWINK_ENABLED:
        # Captcha first; referrer crediting + menu happen in complete_onboarding()
        await send_captcha(bot, user_id, user_id)
    else:
        # No anti-twink: finish onboarding right away
        await complete_onboarding(bot, db, message.from_user)


@router.message(Command("farm"))
async def cmd_farm(message: Message, db: Database):
    """Open the engagement/farm menu."""
    await message.answer(
        await _farm_menu_text(db, message.from_user.id),
        parse_mode=ParseMode.HTML,
        reply_markup=farm_keyboard()
    )
    await db.update_last_active(message.from_user.id)


@router.callback_query(F.data == "farm_menu")
async def show_farm_menu(callback: CallbackQuery, db: Database):
    """Show the engagement/farm menu."""
    await callback.message.edit_text(
        await _farm_menu_text(db, callback.from_user.id),
        parse_mode=ParseMode.HTML,
        reply_markup=farm_keyboard()
    )
    await callback.answer()
    await db.update_last_active(callback.from_user.id)


@router.callback_query(F.data == "daily_bonus")
async def claim_daily_bonus(callback: CallbackQuery, db: Database):
    """Claim the daily farm bonus."""
    result = await db.claim_daily_bonus(callback.from_user.id)
    if result.get("not_registered"):
        await callback.answer("Сначала нажми /start", show_alert=True)
        return
    if not result["ok"]:
        await callback.answer(
            f"🎁 Бонус ещё не готов. Возвращайся через {_format_wait(result['seconds_left'])}.",
            show_alert=True
        )
        return

    bonus_line = ""
    if result["streak_bonus"]:
        bonus_line = (
            f"\n🔥 Бонус за серию: <b>+{fmt_amount(result['streak_bonus'])} ⭐</b>"
        )

    await callback.message.edit_text(
        "🎁 <b>Ежедневный бонус забран!</b>\n\n"
        f"✨ Начислено: <b>+{fmt_amount(result['reward'])} ⭐</b>"
        f"{bonus_line}\n"
        f"🔥 Серия входов: <b>{result['streak']} дн.</b>\n"
        f"💰 Баланс: <b>{fmt_amount(result['balance'])} ⭐</b>\n\n"
        "Завтра можно забрать снова. Не теряй серию.",
        parse_mode=ParseMode.HTML,
        reply_markup=farm_keyboard()
    )
    await callback.answer(f"+{fmt_amount(result['reward'])} ⭐")
    await db.update_last_active(callback.from_user.id)


@router.callback_query(F.data == "box_game")
async def start_box_game(callback: CallbackQuery, db: Database):
    """Start the three-box mini-game."""
    result = await db.start_box_game(callback.from_user.id)
    if result.get("not_registered"):
        await callback.answer("Сначала нажми /start", show_alert=True)
        return
    if not result["ok"]:
        await callback.answer(
            f"🎲 Коробки отдыхают. Следующая попытка через {_format_wait(result['seconds_left'])}.",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        "🎲 <b>Звёздные коробки</b>\n\n"
        "В одной из трёх коробок лежит бонус.\n"
        f"Угадаешь — получишь <b>+{fmt_amount(result['reward'])} ⭐</b>.\n\n"
        "Выбирай коробку:",
        parse_mode=ParseMode.HTML,
        reply_markup=box_game_keyboard(result["session_id"])
    )
    await callback.answer()
    await db.update_last_active(callback.from_user.id)


@router.callback_query(F.data.startswith("box_pick:"))
async def finish_box_game(callback: CallbackQuery, db: Database):
    """Finish the box mini-game."""
    try:
        _, session_id, selected_box = callback.data.split(":")
        session_id = int(session_id)
        selected_box = int(selected_box)
    except (ValueError, IndexError):
        await callback.answer("Ошибка игры", show_alert=True)
        return

    result = await db.complete_box_game(callback.from_user.id, session_id, selected_box)
    if not result["ok"]:
        await callback.answer(result["message"], show_alert=True)
        return

    boxes = []
    for i in range(1, 4):
        if i == result["winning_box"]:
            boxes.append("⭐")
        elif i == result["selected_box"]:
            boxes.append("❌")
        else:
            boxes.append("⬛")

    if result["won"]:
        title = "✅ <b>Попал!</b>"
        body = f"Ты выбрал коробку {selected_box} и забрал <b>+{fmt_amount(result['reward'])} ⭐</b>."
        toast = f"+{fmt_amount(result['reward'])} ⭐"
    else:
        title = "😅 <b>Мимо</b>"
        body = f"Ты выбрал коробку {selected_box}, а звезда была в коробке {result['winning_box']}."
        toast = "В следующий раз повезёт"

    await callback.message.edit_text(
        f"🎲 <b>Звёздные коробки</b>\n\n"
        f"{' '.join(boxes)}\n\n"
        f"{title}\n"
        f"{body}\n\n"
        f"💰 Баланс: <b>{fmt_amount(result['balance'])} ⭐</b>\n"
        f"⏳ Следующая попытка через {_format_wait(Config.BOX_GAME_COOLDOWN_MINUTES * 60)}.",
        parse_mode=ParseMode.HTML,
        reply_markup=farm_keyboard()
    )
    await callback.answer(toast)
    await db.update_last_active(callback.from_user.id)


@router.callback_query(F.data == "my_link")
async def show_ref_link(callback: CallbackQuery, db: Database, bot):
    """Show referral link."""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    bot_username = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={user['ref_code']}"

    text = (
        f"🔗 <b>Ваша реферальная ссылка:</b>\n"
        f"<code>{esc(ref_link)}</code>\n\n"
        f"📋 <b>Как это работает:</b>\n"
        f"• Отправьте ссылку другу\n"
        f"• Когда друг нажмёт /start — вы получите +{fmt_amount(Config.REWARD_PER_REFERRAL)} ⭐\n"
        f"• Чем больше друзей, тем больше Stars!\n\n"
        f"📊 <b>Ваша статистика:</b>\n"
        f"👥 Приглашено: {user['total_referrals']} чел.\n"
        f"💰 Заработано всего: {fmt_amount(user['total_earned'])} ⭐\n"
        f"📤 Выведено всего: {fmt_amount(user['total_withdrawn'])} ⭐"
    )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=referral_keyboard()
    )
    await callback.answer()
    await db.update_last_active(callback.from_user.id)


@router.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery, db: Database):
    """Show user balance."""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    text = (
        f"⭐ <b>Ваш баланс Telegram Stars</b>\n\n"
        f"💰 Доступно для вывода: <b>{fmt_amount(user['balance'])} ⭐</b>\n"
        f"📈 Всего заработано: <b>{fmt_amount(user['total_earned'])} ⭐</b>\n"
        f"📤 Всего выведено: <b>{fmt_amount(user['total_withdrawn'])} ⭐</b>\n"
        f"👥 Приглашено друзей: <b>{user['total_referrals']}</b>\n\n"
        f"💎 Минимальная сумма вывода: {Config.WITHDRAW_AMOUNTS[0]} ⭐"
    )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=balance_keyboard()
    )
    await callback.answer()
    await db.update_last_active(callback.from_user.id)


@router.callback_query(F.data == "withdraw")
async def withdraw_menu(callback: CallbackQuery, db: Database):
    """Show withdrawal menu."""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    text = (
        f"💸 <b>Вывод Telegram Stars</b>\n\n"
        f"💰 Ваш баланс: {fmt_amount(user['balance'])} ⭐\n\n"
        f"Выберите сумму для вывода:"
    )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=withdraw_keyboard(user['balance'])
    )
    await callback.answer()


@router.callback_query(F.data.startswith("withdraw_"))
async def process_withdraw(callback: CallbackQuery, db: Database, bot):
    """Process withdrawal request."""
    try:
        amount = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: неверная сумма")
        return

    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    # Create withdrawal
    success, message, withdrawal_id = await db.create_withdrawal(user['user_id'], amount)

    if not success:
        await callback.answer(message, show_alert=True)
        return

    # Build referrer (who invited this user) info for the moderation post
    if user.get('referrer_id'):
        referrer = await db.get_user(user['referrer_id'])
        if referrer:
            ref_name = get_user_display_name(referrer['username'], referrer['first_name'], referrer['user_id'])
            referrer_line = f"🤝 Пригласил: {esc(ref_name)} (<code>{referrer['user_id']}</code>)\n"
        else:
            referrer_line = f"🤝 Пригласил: <code>{user['referrer_id']}</code>\n"
    else:
        referrer_line = "🤝 Пригласил: — (пришёл сам)\n"

    # Post the request to the admin channel (or admin DM as a fallback) with live buttons
    try:
        await bot.send_message(
            Config.admin_destination(),
            f"🆕 <b>Новая заявка на вывод #{withdrawal_id}</b>\n\n"
            f"👤 Пользователь: {esc(get_user_display_name(user['username'], user['first_name'], user['user_id']))}\n"
            f"🆔 ID: <code>{user['user_id']}</code>\n"
            f"{referrer_line}"
            f"👥 Его рефералов: {user['total_referrals']}\n"
            f"💰 Сумма: <b>{amount} ⭐</b>\n\n"
            f"Обработайте кнопками ниже 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=notification_keyboard(withdrawal_id, "pending", user.get('is_banned'))
        )
    except Exception as e:
        logger.error(f"Failed to notify admin channel about withdrawal: {e}")

    await callback.message.edit_text(
        f"✅ <b>Заявка на вывод {amount} ⭐ создана!</b>\n\n"
        f"Администратор рассмотрит её в ближайшее время.\n"
        f"Обычно это занимает до 24 часов.\n\n"
        f"Вы получите уведомление, когда заявка будет обработана.\n"
        f"Спасибо за использование бота! 🤝",
        parse_mode=ParseMode.HTML,
        reply_markup=back_button()
    )
    await callback.answer("✅ Заявка создана!")
    logger.info(f"User {user['user_id']} created withdrawal request #{withdrawal_id} for {amount} stars")


@router.callback_query(F.data.startswith("insufficient_"))
async def insufficient_balance(callback: CallbackQuery):
    """Handle insufficient balance."""
    try:
        amount = int(callback.data.split("_")[1])
        await callback.answer(
            f"❌ Недостаточно средств для вывода {amount} ⭐\n"
            f"Пригласите больше друзей!",
            show_alert=True
        )
    except:
        await callback.answer("Недостаточно средств")


@router.callback_query(F.data == "my_refs")
async def show_referrals(callback: CallbackQuery, db: Database):
    """Show user's referrals."""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    referrals = await db.get_user_referrals(user['user_id'], limit=20)

    if referrals:
        ref_list = []
        for i, ref in enumerate(referrals, 1):
            name = get_user_display_name(ref['username'], ref['first_name'], ref['user_id'])
            date = format_datetime(ref['created_at'])
            ref_list.append(f"{i}. {esc(name)} — {esc(date)}")

        text = (
            f"👥 <b>Ваши рефералы ({user['total_referrals']}):</b>\n\n"
            + "\n".join(ref_list)
        )

        if len(referrals) >= 20:
            text += "\n\n<i>Показаны последние 20</i>"
    else:
        text = (
            "👥 <b>У вас пока нет рефералов</b>\n\n"
            "Пригласите друзей по своей реферальной ссылке!"
        )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Получить ссылку", callback_data="my_link")],
            [InlineKeyboardButton(text="🏆 Топ рефералов", callback_data="top_referrers")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
        ])
    )
    await callback.answer()
    await db.update_last_active(callback.from_user.id)


@router.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery, db: Database):
    """Show user statistics."""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    # Calculate earnings per referral
    avg_earning = user['total_earned'] / user['total_referrals'] if user['total_referrals'] > 0 else 0

    # Get registration date
    reg_date = format_datetime(user['created_at'])

    text = (
        f"📊 <b>Ваша статистика</b>\n\n"
        f"📅 Дата регистрации: {esc(reg_date)}\n"
        f"👥 Всего рефералов: <b>{user['total_referrals']}</b>\n"
        f"💰 Текущий баланс: <b>{fmt_amount(user['balance'])} ⭐</b>\n"
        f"📈 Всего заработано: <b>{fmt_amount(user['total_earned'])} ⭐</b>\n"
        f"📤 Всего выведено: <b>{fmt_amount(user['total_withdrawn'])} ⭐</b>\n"
        f"📊 Средний доход: <b>{avg_earning:.1f} ⭐/реферал</b>\n\n"
        f"🎯 Продолжайте приглашать друзей!"
    )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_button()
    )
    await callback.answer()


async def _top_referrers_text(db: Database, user_id: int):
    top_users = await db.get_top_referrers(limit=10)
    if not top_users:
        return None

    user = await db.get_user(user_id)

    # Find user's position
    user_position = None
    for i, top_user in enumerate(top_users, 1):
        if top_user['user_id'] == user_id:
            user_position = i
            break

    # Build leaderboard
    medals = ["🥇", "🥈", "🥉"]
    leaderboard = []

    for i, top_user in enumerate(top_users, 1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        name = get_user_display_name(top_user['username'], top_user['first_name'], top_user['user_id'])
        refs = top_user['total_referrals']
        earned = top_user['total_earned']

        # Highlight current user
        if top_user['user_id'] == user_id:
            leaderboard.append(f"<b>{medal} {esc(name)} — {refs} реф. ({fmt_amount(earned)} ⭐)</b>")
        else:
            leaderboard.append(f"{medal} {esc(name)} — {refs} реф. ({fmt_amount(earned)} ⭐)")

    text = "🏆 <b>Топ рефералов</b>\n\n" + "\n".join(leaderboard)

    if user and not user_position and user['total_referrals'] > 0:
        text += f"\n\n<i>Ваша позиция: вне топ-10</i>"

    return text


@router.message(Command("top"))
async def cmd_top_referrers(message: Message, db: Database):
    """Show top referrers leaderboard by command."""
    text = await _top_referrers_text(db, message.from_user.id)
    if not text:
        await message.answer("Пока нет данных для рейтинга")
        return

    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_button()
    )
    await db.update_last_active(message.from_user.id)


@router.callback_query(F.data == "top_referrers")
async def show_top_referrers(callback: CallbackQuery, db: Database):
    """Show top referrers leaderboard."""
    text = await _top_referrers_text(db, callback.from_user.id)
    if not text:
        await callback.answer("Пока нет данных для рейтинга")
        return

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_button()
    )
    await callback.answer()


@router.callback_query(F.data == "withdrawal_history")
async def show_withdrawal_history(callback: CallbackQuery, db: Database):
    """Show withdrawal history."""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    withdrawals = await db.get_user_withdrawals(user['user_id'], limit=10)

    if not withdrawals:
        text = (
            "📜 <b>История выводов</b>\n\n"
            "У вас пока нет выводов.\n"
            "Накопите Stars и создайте заявку!"
        )
    else:
        history_lines = []
        for w in withdrawals:
            date = format_datetime(w['created_at'])
            status = format_status(w['status'])
            history_lines.append(f"• {fmt_amount(w['amount'])} ⭐ — {esc(status)}\n  <i>{esc(date)}</i>")

        text = "📜 <b>История выводов</b>\n\n" + "\n\n".join(history_lines)

        if len(withdrawals) >= 10:
            text += "\n\n<i>Показаны последние 10 операций</i>"

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_button()
    )
    await callback.answer()


@router.callback_query(F.data == "enter_promo")
async def enter_promo(callback: CallbackQuery, state: FSMContext):
    """Ask the user to type a promo code."""
    await state.set_state(PromoActivateStates.waiting)
    await callback.message.edit_text(
        "🎟 <b>Активация промокода</b>\n\n"
        "Введите промокод одним сообщением:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="promo_cancel")]
        ])
    )
    await callback.answer()


@router.callback_query(F.data == "promo_cancel")
async def promo_cancel(callback: CallbackQuery, state: FSMContext, db: Database):
    """Cancel promo entry and go back to the menu."""
    await state.clear()
    user = await db.get_user(callback.from_user.id)
    text = "👋 Главное меню"
    if user:
        text += f"\n\n💰 Ваш баланс: {fmt_amount(user['balance'])} ⭐"
    await callback.message.edit_text(text, reply_markup=main_keyboard())
    await callback.answer()


@router.message(PromoActivateStates.waiting)
async def activate_promo_code(message: Message, state: FSMContext, db: Database):
    """Try to activate the promo code the user sent."""
    code = (message.text or "").strip()
    await state.clear()

    success, msg, reward = await db.activate_promo(message.from_user.id, code)
    if not success:
        await message.answer(msg, reply_markup=main_keyboard())
        return

    user = await db.get_user(message.from_user.id)
    await message.answer(
        f"🎉 <b>Промокод активирован!</b>\n\n"
        f"✨ Вам начислено: <b>+{fmt_amount(reward)} ⭐</b>\n"
        f"💰 Ваш баланс: <b>{fmt_amount(user['balance'])} ⭐</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )
    logger.info(f"User {message.from_user.id} activated promo {code.upper()} (+{reward})")


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, db: Database):
    """Return to main menu."""
    user = await db.get_user(callback.from_user.id)

    text = f"👋 Главное меню"
    if user:
        text += f"\n\n💰 Ваш баланс: {fmt_amount(user['balance'])} ⭐"

    await callback.message.edit_text(text, reply_markup=main_keyboard())
    await callback.answer()


# Import InlineKeyboardButton and InlineKeyboardMarkup for my_refs handler
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
