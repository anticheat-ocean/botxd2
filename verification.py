"""Onboarding: welcome message, anti-bot captcha and anti-twink heuristics."""
import random
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from aiogram.enums import ParseMode

from config import Config
from database import Database
from keyboards import main_keyboard
from utils import esc, fmt_amount, get_user_display_name

router = Router()
logger = logging.getLogger(__name__)


# ============================================================================
#  ПРИВЕТСТВЕННЫЙ ТЕКСТ — показывается сразу любому, кто заходит в бота.
#  Можешь менять текст ниже и пушить изменения в GitHub для редеплоя.
# ============================================================================


def _ce(emoji_id: str, fallback: str) -> str:
    """Telegram custom emoji HTML tag with a visible fallback."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


WELCOME_TEXT = (
    f"{_ce('5469741319330996757', '💫')} Привет! {_ce('5940385029627582223', '💫')}\n"
    "Ты попал в бота, где за друзей дают звёзды!  \n"
    "\n"
    " Очень рад, что ты тут! ✨\n"
    "\n"
    f"Совет: зови побольше друзей! 👥 {_ce('5472019095106886003', '💌')}\n"
    "\n"
    "\n"
    f"С уважением, создатель. {_ce('5190859184312167965', '💌')}\n"
    "\n"
    " 📝 ПРАВИЛА БОТА: \n"
    " \n"
    f" • Выплата — до 48 часов. {_ce('5373236586760651455', '⏱️')}\n"
    "• Указывай юз (свой/друга). \n"
    f"   Ошибся? Не выдам. {_ce('5848136875935535209', '⛔')}\n"
    f" • Накрутка твинками = бан. {_ce('5462882007451185227', '🚫')}\n"
    "• Только русскоговорящие! 🇷🇺\n"
    "\n"
    "\n"
    f"{_ce('5922446600499632269', '🤑')} Выплаты: @dfdfffgfgdg \n"
    f"{_ce('5465300082628763143', '💬')} Вопросы: @JQwMiIIRQHHlii \n"
    "\n"
    "\n"
    f"{_ce('5066563260262647533', '❗️')} Не пиши ерунду — иначе бан!\n"
    " 📢 Подпишись на канал выплат!\n"
    "    чат тоже  @fdfdfkfkfkldl\n"
    "            с уважениям \n"
    f"                создатель.{_ce('5208748315805499400', '✅')}"
)


# In-memory captcha state: user_id -> correct answer. Lost on restart (then re-issued).
_captcha_answers: dict = {}


# Unicode ranges that cover Arabic script (incl. Supplement, Extended-A, Presentation forms)
_ARABIC_RANGES = (
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)


def _has_arabic(text) -> bool:
    """True if the string contains any Arabic-script character."""
    if not text:
        return False
    for ch in text:
        code = ord(ch)
        for lo, hi in _ARABIC_RANGES:
            if lo <= code <= hi:
                return True
    return False


def is_arabic_account(tg_user) -> bool:
    """
    Detect an Arabic account by app language code or Arabic characters in the name.
    Controlled by Config.BLOCK_ARABIC.
    """
    if not Config.BLOCK_ARABIC:
        return False
    lang = (getattr(tg_user, "language_code", None) or "").lower()
    if lang.startswith("ar") or lang == "fa":
        return True
    if _has_arabic(getattr(tg_user, "first_name", "")):
        return True
    if _has_arabic(getattr(tg_user, "last_name", "")):
        return True
    return False


async def reject_arabic(bot, chat_id: int):
    """Tell an Arabic-account user they can't use the bot."""
    try:
        await bot.send_message(
            chat_id,
            "🚫 <b>Доступ запрещён.</b>\n\n"
            "Бот только для русскоязычных пользователей.\n"
            "Этот бот предназначен для русскоязычных пользователей.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Failed to send arabic-reject to {chat_id}: {e}")


# ============ PHONE-NUMBER COUNTRY GATE ============

# Calling codes of Arab-League countries that we block (code -> country name).
ARAB_PHONE_CODES = {
    "966": "Саудовская Аравия",
    "971": "ОАЭ",
    "974": "Катар",
    "973": "Бахрейн",
    "965": "Кувейт",
    "968": "Оман",
    "967": "Йемен",
    "962": "Иордания",
    "961": "Ливан",
    "963": "Сирия",
    "964": "Ирак",
    "970": "Палестина",
    "20": "Египет",
    "212": "Марокко",
    "213": "Алжир",
    "216": "Тунис",
    "218": "Ливия",
    "249": "Судан",
    "222": "Мавритания",
    "252": "Сомали",
    "253": "Джибути",
    "269": "Коморы",
}


def detect_phone_country(phone: str):
    """Return (code, country_name) for a blocked Arab code, else (None, None)."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    # Longest codes first so e.g. "212" (Morocco) wins over "21"
    for code in sorted(ARAB_PHONE_CODES, key=len, reverse=True):
        if digits.startswith(code):
            return code, ARAB_PHONE_CODES[code]
    return None, None


def _phone_request_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def request_phone(bot, chat_id: int):
    """Ask the user to share their phone number via the contact button."""
    try:
        await bot.send_message(
            chat_id,
            "📱 <b>Подтверждение номера</b>\n\n"
            "Чтобы пользоваться ботом, подтвердите свой номер телефона.\n"
            "Нажмите кнопку ниже 👇\n\n"
            "<i>Это нужно для защиты от ботов и накрутки.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=_phone_request_keyboard(),
        )
    except Exception as e:
        logger.error(f"Failed to request phone from {chat_id}: {e}")


@router.message(F.contact)
async def on_contact(message: Message, db: Database, bot):
    """Handle a shared contact: verify ownership + country, then continue onboarding."""
    if not Config.PHONE_GATE_ENABLED:
        return
    user = message.from_user
    contact = message.contact

    # Must be the user's OWN number (prevents sharing someone else's contact)
    if contact.user_id != user.id:
        await message.answer(
            "❌ Это не ваш номер. Нажмите кнопку «📱 Поделиться номером», "
            "а не отправляйте чужой контакт.",
            reply_markup=_phone_request_keyboard(),
        )
        return

    phone = contact.phone_number
    code, country = detect_phone_country(phone)

    # Make sure the user row exists (they may not be registered yet)
    existing = await db.get_user(user.id)
    if not existing:
        await db.register_user(user.id, user.username, user.first_name or "User", None)

    if code:
        # Blocked Arab country
        await db.set_phone(user.id, phone, country, verified=False)
        logger.info(f"User {user.id} blocked by phone country: {country} (+{code})")
        await message.answer(
            f"🚫 <b>Доступ запрещён.</b>\n\n"
            f"Регистрация с номеров страны «{esc(country)}» недоступна.\n"
            f"Бот только для русскоязычных пользователей.",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Allowed
    await db.set_phone(user.id, phone, country or "", verified=True)
    await message.answer(
        "✅ <b>Номер подтверждён!</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )

    # Continue the onboarding: captcha (if needed) then menu
    refreshed = await db.get_user(user.id)
    if Config.ANTITWINK_ENABLED and not (refreshed and refreshed.get("verified")):
        await send_captcha(bot, user.id, user.id)
    else:
        await complete_onboarding(bot, db, user)


# ============ WELCOME ============

async def send_welcome(bot, chat_id: int, name: str = ""):
    """Send the welcome text with the user's name. Safe to call on every /start."""
    try:
        text = WELCOME_TEXT.format(name=esc(name) if name else "друг")
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed to send welcome to {chat_id}: {e}")


# ============ CAPTCHA ============

def _build_captcha(user_id: int):
    """Generate a fresh, per-user math captcha. Returns (question_str, keyboard)."""
    a = random.randint(2, 9)
    b = random.randint(2, 9)
    answer = a + b
    _captcha_answers[user_id] = answer

    # Build wrong options near the correct answer
    options = {answer}
    while len(options) < 4:
        delta = random.choice([-3, -2, -1, 1, 2, 3])
        cand = answer + delta
        if cand > 0:
            options.add(cand)
    opts = list(options)
    random.shuffle(opts)

    buttons = [InlineKeyboardButton(text=str(v), callback_data=f"cap:{v}") for v in opts]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
    return f"{a} + {b}", keyboard


def _captcha_text(question: str) -> str:
    return (
        "🤖 <b>Проверка, что ты не бот</b>\n\n"
        "Реши простой пример и нажми правильный ответ:\n\n"
        f"<b>{question} = ?</b>"
    )


async def send_captcha(bot, chat_id: int, user_id: int):
    """Send (or resend) the captcha to the user."""
    question, keyboard = _build_captcha(user_id)
    try:
        await bot.send_message(chat_id, _captcha_text(question),
                               parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Failed to send captcha to {user_id}: {e}")


@router.callback_query(F.data.startswith("cap:"))
async def captcha_answer(callback: CallbackQuery, db: Database, bot):
    """Handle a captcha answer."""
    user_id = callback.from_user.id
    try:
        chosen = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer()
        return

    expected = _captcha_answers.get(user_id)

    # No state (bot restarted / stale button) -> issue a fresh captcha
    if expected is None:
        await callback.answer("Обновляю проверку…", show_alert=False)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await send_captcha(bot, user_id, user_id)
        return

    if chosen != expected:
        await callback.answer("❌ Неверно, попробуй ещё раз", show_alert=True)
        # Issue a brand-new example
        question, keyboard = _build_captcha(user_id)
        try:
            await callback.message.edit_text(
                _captcha_text(question), parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
        except Exception:
            pass
        return

    # Correct!
    _captcha_answers.pop(user_id, None)
    await callback.answer("✅ Верно!")
    try:
        await callback.message.edit_text("✅ <b>Проверка пройдена!</b>", parse_mode=ParseMode.HTML)
    except Exception:
        pass

    await complete_onboarding(bot, db, callback.from_user)


# ============ ANTI-TWINK HEURISTICS ============

async def assess_account(bot, user_id: int, username) -> tuple:
    """
    Heuristic twink check. Returns (suspicious: bool, reasons: list[str]).
    A real twink can never be proven, so we require at least TWO weak signals.
    """
    reasons = []

    if not username:
        reasons.append("нет @username")

    # Profile photo (twinks usually have none). Fail-open on API errors.
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        has_photo = photos.total_count > 0
    except Exception as e:
        logger.warning(f"profile photo check failed for {user_id}: {e}")
        has_photo = True
    if not has_photo:
        reasons.append("нет аватарки")

    # Very fresh account (high numeric id)
    if user_id >= Config.NEW_ACCOUNT_ID_THRESHOLD:
        reasons.append("свежий аккаунт")

    suspicious = len(reasons) >= 2
    return suspicious, reasons


# ============ ONBOARDING (after captcha) ============

async def complete_onboarding(bot, db: Database, tg_user):
    """
    Finalize a new user's onboarding after the captcha:
    run the twink check, credit the referrer (or flag), then show the menu.
    """
    user_id = tg_user.id
    username = tg_user.username
    first_name = tg_user.first_name or "User"

    await db.mark_verified(user_id)

    user = await db.get_user(user_id)
    referrer_id = user.get('referrer_id') if user else None

    suspicious, reasons = await assess_account(bot, user_id, username)

    if suspicious:
        await db.set_twink(user_id, True)
        logger.info(f"User {user_id} flagged as twink: {reasons}")
        await _flag_twink_to_admin(bot, db, user, reasons)

    # Credit the referrer. By default a twink suspicion is alert-only and the
    # referrer is still paid; strict mode (TWINK_BLOCK_REFERRAL) blocks the payout.
    block = suspicious and Config.TWINK_BLOCK_REFERRAL
    if referrer_id and not block:
        data = await db.credit_referral(user_id)
        if data:
            logger.info(
                f"Referral credited: referrer {referrer_id} +{data['reward']} from {user_id}"
            )
            await _notify_referrer(bot, data, username, first_name, user_id)
        else:
            logger.info(f"Referral NOT credited for {user_id} (already credited / no referrer)")
    elif referrer_id and block:
        logger.info(f"Referral blocked for suspected twink {user_id} (referrer {referrer_id})")

    # The user gets into the bot regardless.
    try:
        await bot.send_message(
            user_id,
            "🎉 <b>Готово!</b> Добро пожаловать в реферальную программу.\n\n"
            f"💰 Приглашай друзей и получай {fmt_amount(Config.REWARD_PER_REFERRAL)} ⭐ за каждого.\n\n"
            "Выбирай действие в меню 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )
    except Exception as e:
        logger.error(f"Failed to send menu to {user_id}: {e}")


async def _notify_referrer(bot, data: dict, username, first_name, user_id: int):
    try:
        await bot.send_message(
            data['referrer_id'],
            f"🎉 По вашей ссылке зарегистрировался новый пользователь!\n\n"
            f"👤 {get_user_display_name(username, first_name, user_id)}\n"
            f"✨ Вы получили +{fmt_amount(data['reward'])} ⭐\n"
            f"💰 Ваш баланс: {fmt_amount(data['referrer_balance'])} ⭐"
        )
    except Exception as e:
        logger.error(f"Failed to notify referrer {data['referrer_id']}: {e}")


async def _flag_twink_to_admin(bot, db: Database, user: dict, reasons: list):
    """Send a twink warning to the admin's private chat (NOT the channel)."""
    if not user:
        return
    referrer_line = "🤝 Пригласил: — (пришёл сам)\n"
    if user.get('referrer_id'):
        referrer = await db.get_user(user['referrer_id'])
        if referrer:
            ref_name = get_user_display_name(referrer['username'], referrer['first_name'], referrer['user_id'])
            referrer_line = (
                f"🤝 Пригласил: {esc(ref_name)} (<code>{referrer['user_id']}</code>) "
                f"— ⚠️ звёзды НЕ начислены\n"
            )
        else:
            referrer_line = f"🤝 Пригласил: <code>{user['referrer_id']}</code> — ⚠️ звёзды НЕ начислены\n"

    name = get_user_display_name(user['username'], user['first_name'], user['user_id'])
    text = (
        f"⚠️ <b>Подозрение на ТВИНК</b>\n\n"
        f"👤 Пользователь: {esc(name)}\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"{referrer_line}"
        f"🔎 Признаки: {esc(', '.join(reasons))}\n\n"
        f"Реферальная награда по этому юзеру не выдана."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚫 Забанить", callback_data=f"tban:{user['user_id']}"),
        InlineKeyboardButton(text="🧹 Обнулить баланс", callback_data=f"tzero:{user['user_id']}"),
    ]])
    try:
        await bot.send_message(
            Config.ADMIN_ID, text,
            parse_mode=ParseMode.HTML, reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Failed to flag twink to admin: {e}")


# ============ ADMIN ACTIONS ON A TWINK FLAG ============

def _is_admin(user_id: int) -> bool:
    return user_id == Config.ADMIN_ID


async def _append_note(callback: CallbackQuery, note: str):
    """Append a status line to the flag message and drop its buttons."""
    try:
        await callback.message.edit_text(
            (callback.message.html_text or "") + f"\n\n{note}",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("tban:"))
async def twink_ban(callback: CallbackQuery, db: Database, bot):
    """Ban a suspected twink straight from the flag message."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    uid = int(callback.data.split(":")[1])
    await db.set_ban(uid, True)
    try:
        await bot.send_message(uid, "🚫 <b>Вы заблокированы администратором.</b>", parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.answer("🚫 Забанен")
    await _append_note(callback, "🚫 <b>Забанен администратором</b>")


@router.callback_query(F.data.startswith("tzero:"))
async def twink_zero(callback: CallbackQuery, db: Database, bot):
    """Reset a suspected twink's balance straight from the flag message."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    uid = int(callback.data.split(":")[1])
    old = await db.reset_balance(uid)
    await callback.answer(f"🧹 Баланс обнулён (было {fmt_amount(old)} ⭐)" if old is not None else "Не найден")
    await _append_note(callback, "🧹 <b>Баланс обнулён администратором</b>")
