"""Admin handlers — full button-driven withdrawal management panel (admin only)."""
import asyncio
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import Database
from keyboards import (
    admin_keyboard, admin_back_keyboard,
    withdrawals_list_keyboard, withdrawal_card_keyboard,
    notification_keyboard, broadcast_cancel_keyboard, broadcast_confirm_keyboard,
    promos_keyboard, admin_user_profile_keyboard, admin_user_refs_keyboard,
)
from utils import format_datetime, format_status, get_user_display_name, esc, fmt_amount
from config import Config

router = Router()
logger = logging.getLogger(__name__)


class BroadcastStates(StatesGroup):
    """FSM states for the admin broadcast flow."""
    waiting_for_message = State()
    confirm = State()


class PromoStates(StatesGroup):
    """FSM state for creating a promo code."""
    waiting_for_promo = State()


class AdminUserStates(StatesGroup):
    """FSM state for looking up a user in the admin panel."""
    waiting_for_query = State()


PAGE_SIZE = 8

STATUS_TITLES = {
    "pending": "⏳ Новые заявки",
    "approved": "👌 Одобренные (ждут выплаты)",
    "completed": "✅ Выплаченные",
    "rejected": "❌ Отклонённые",
}


def is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    return user_id == Config.ADMIN_ID


async def _safe_edit(message, text, reply_markup=None):
    """Edit message text, ignoring 'message is not modified' errors."""
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            # Fall back to sending a new message if edit is impossible
            try:
                await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
            except Exception as ex:
                logger.error(f"Failed to edit/send admin message: {ex}")


def _admin_home_text(stats: dict) -> str:
    """Build the admin home panel text."""
    return (
        f"🛠 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: <b>{stats['total_users']}</b>\n"
        f"📈 Активных за неделю: <b>{stats['active_users_week']}</b>\n"
        f"⭐ Начислено рефералам: <b>{fmt_amount(stats['total_earned'])} ⭐</b>\n"
        f"📤 Выплачено: <b>{fmt_amount(stats['total_withdrawn'])} ⭐</b>\n\n"
        f"⏳ Новых заявок: <b>{stats['pending_withdrawals_count']}</b> "
        f"({fmt_amount(stats['pending_withdrawals_amount'])} ⭐)\n\n"
        f"Выберите раздел ниже 👇"
    )


def _withdrawal_card_text(w: dict) -> str:
    """Build a single withdrawal card."""
    name = get_user_display_name(w['username'], w['first_name'], w['user_id'])

    # Referrer (who invited this user)
    if w.get('referrer_id'):
        ref_name = get_user_display_name(
            w.get('referrer_username'), w.get('referrer_first_name'), w['referrer_id']
        )
        referrer_line = f"🤝 Пригласил: {esc(ref_name)} (<code>{w['referrer_id']}</code>)\n"
    else:
        referrer_line = "🤝 Пригласил: — (пришёл сам)\n"

    ban_line = "🚫 <b>ЗАБАНЕН</b>\n" if w.get('is_banned') else ""

    return (
        f"🧾 <b>Заявка #{w['id']}</b>\n\n"
        f"👤 Пользователь: {esc(name)}\n"
        f"🆔 ID: <code>{w['user_id']}</code>\n"
        f"{ban_line}"
        f"{referrer_line}"
        + (f"👥 Его рефералов: {w['total_referrals']}\n" if 'total_referrals' in w else "")
        + f"💰 Сумма: <b>{fmt_amount(w['amount'])} ⭐</b>\n"
        f"📊 Статус: {esc(format_status(w['status']))}\n"
        f"📅 Создана: {esc(format_datetime(w['created_at']))}\n"
        + (f"⚙️ Обработана: {esc(format_datetime(w['processed_at']))}\n" if w.get('processed_at') else "")
        + (f"💬 Баланс пользователя сейчас: {fmt_amount(w['balance'])} ⭐\n" if 'balance' in w else "")
    )


async def _admin_user_profile_text(db: Database, user: dict) -> str:
    """Build an admin-facing user profile card."""
    name = get_user_display_name(user['username'], user['first_name'], user['user_id'])
    referrer_line = "🤝 Пригласил: — (пришёл сам)\n"
    if user.get('referrer_id'):
        referrer = await db.get_user(user['referrer_id'])
        if referrer:
            ref_name = get_user_display_name(
                referrer['username'], referrer['first_name'], referrer['user_id']
            )
            referrer_line = f"🤝 Пригласил: {esc(ref_name)} (<code>{referrer['user_id']}</code>)\n"
        else:
            referrer_line = f"🤝 Пригласил: <code>{user['referrer_id']}</code>\n"

    status_bits = []
    status_bits.append("🚫 бан" if user.get('is_banned') else "✅ активен")
    status_bits.append("🧩 капча ок" if user.get('verified') else "🧩 капча не пройдена")
    if user.get('is_twink'):
        status_bits.append("⚠️ твинк")
    if user.get('phone_verified'):
        status_bits.append("📱 телефон ок")

    referrals = await db.get_user_referrals(user['user_id'], limit=5)
    if referrals:
        referral_lines = []
        for i, ref in enumerate(referrals, 1):
            ref_name = get_user_display_name(ref['username'], ref['first_name'], ref['user_id'])
            referral_lines.append(
                f"{i}. {esc(ref_name)} — <code>{ref['user_id']}</code>, "
                f"{fmt_amount(ref.get('balance', 0))} ⭐"
            )
        referrals_text = "\n".join(referral_lines)
        if user['total_referrals'] > len(referrals):
            referrals_text += f"\n<i>...и ещё {user['total_referrals'] - len(referrals)}</i>"
    else:
        referrals_text = "Пока никого не пригласил."

    withdrawals = await db.get_user_withdrawals(user['user_id'], limit=3)
    if withdrawals:
        withdrawal_lines = []
        for w in withdrawals:
            withdrawal_lines.append(
                f"• #{w['id']} — {fmt_amount(w['amount'])} ⭐, "
                f"{esc(format_status(w['status']))}, {esc(format_datetime(w['created_at']))}"
            )
        withdrawals_text = "\n".join(withdrawal_lines)
    else:
        withdrawals_text = "Заявок на вывод нет."

    username_line = f"@{esc(user['username'])}" if user.get('username') else "—"
    phone_line = esc(user.get('phone') or "—")

    return (
        f"👤 <b>Профиль пользователя</b>\n\n"
        f"Имя: <b>{esc(name)}</b>\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"Username: {username_line}\n"
        f"Реф-код: <code>{esc(user['ref_code'])}</code>\n"
        f"{referrer_line}"
        f"Статус: {' • '.join(status_bits)}\n\n"
        f"💰 <b>Баланс</b>\n"
        f"• Сейчас: <b>{fmt_amount(user['balance'])} ⭐</b>\n"
        f"• Заработано всего: <b>{fmt_amount(user['total_earned'])} ⭐</b>\n"
        f"• Выведено всего: <b>{fmt_amount(user['total_withdrawn'])} ⭐</b>\n\n"
        f"👥 <b>Рефералы</b>\n"
        f"• Всего пригласил: <b>{user['total_referrals']}</b>\n"
        f"{referrals_text}\n\n"
        f"📤 <b>Последние выводы</b>\n"
        f"{withdrawals_text}\n\n"
        f"📱 Телефон: <code>{phone_line}</code>\n"
        f"📅 Создан: {esc(format_datetime(user['created_at']))}\n"
        f"🕒 Активность: {esc(format_datetime(user['last_active']))}"
    )


async def _admin_user_refs_text(db: Database, user: dict) -> str:
    """Build a full admin-facing referrals list for a user."""
    name = get_user_display_name(user['username'], user['first_name'], user['user_id'])
    referrals = await db.get_user_referrals(user['user_id'], limit=25)

    if not referrals:
        return (
            f"👥 <b>Кого пригласил {esc(name)}</b>\n\n"
            f"ID: <code>{user['user_id']}</code>\n\n"
            f"Пока никого не пригласил."
        )

    lines = []
    for i, ref in enumerate(referrals, 1):
        ref_name = get_user_display_name(ref['username'], ref['first_name'], ref['user_id'])
        lines.append(
            f"{i}. {esc(ref_name)}\n"
            f"   ID: <code>{ref['user_id']}</code> • баланс {fmt_amount(ref.get('balance', 0))} ⭐ "
            f"• своих реф. {ref.get('total_referrals', 0)}\n"
            f"   пришёл: {esc(format_datetime(ref['created_at']))}"
        )

    text = (
        f"👥 <b>Кого пригласил {esc(name)}</b>\n\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"Всего: <b>{user['total_referrals']}</b>\n\n"
        + "\n\n".join(lines)
    )
    if user['total_referrals'] > len(referrals):
        text += f"\n\n<i>Показаны последние {len(referrals)} из {user['total_referrals']}.</i>"
    return text


# ============ ENTRY POINTS ============

@router.message(Command("admin"))
async def admin_panel(message: Message, db: Database):
    """Show admin panel (command)."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return
    stats = await db.get_stats()
    await message.answer(_admin_home_text(stats), parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())
    logger.info(f"Admin {message.from_user.id} opened admin panel")


@router.callback_query(F.data == "admin_home")
async def admin_home_callback(callback: CallbackQuery, db: Database):
    """Return to admin home."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    stats = await db.get_stats()
    await _safe_edit(callback.message, _admin_home_text(stats), admin_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery, db: Database):
    """Show detailed statistics."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    stats = await db.get_stats()
    activity = (stats['active_users_week'] / stats['total_users'] * 100) if stats['total_users'] else 0
    avg = (stats['total_earned'] / stats['total_users']) if stats['total_users'] else 0

    text = (
        f"📊 <b>Подробная статистика</b>\n\n"
        f"👥 <b>Пользователи</b>\n"
        f"• Всего: {stats['total_users']}\n"
        f"• Активных за неделю: {stats['active_users_week']} ({activity:.1f}%)\n\n"
        f"⭐ <b>Финансы</b>\n"
        f"• Начислено рефералам: {fmt_amount(stats['total_earned'])} ⭐\n"
        f"• Выплачено: {fmt_amount(stats['total_withdrawn'])} ⭐\n"
        f"• На балансах пользователей: {fmt_amount(stats['total_earned'] - stats['total_withdrawn'])} ⭐\n"
        f"• Средн. на пользователя: {avg:.1f} ⭐\n\n"
        f"💸 <b>Заявки</b>\n"
        f"• Новых: {stats['pending_withdrawals_count']} ({fmt_amount(stats['pending_withdrawals_amount'])} ⭐)"
    )
    await _safe_edit(callback.message, text, admin_back_keyboard())
    await callback.answer()


# ============ USER LOOKUP ============

@router.message(Command("user"))
async def admin_user_command(message: Message, db: Database):
    """Open a user profile by command: /user ID or /user @username."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "🔎 <b>Профиль пользователя</b>\n\n"
            "Напишите так:\n"
            "<code>/user 123456789</code>\n"
            "<code>/user @username</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_back_keyboard(),
        )
        return

    user = await db.find_user(parts[1])
    if not user:
        await message.answer("❌ Пользователь не найден", reply_markup=admin_back_keyboard())
        return

    await message.answer(
        await _admin_user_profile_text(db, user),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_user_profile_keyboard(user['user_id'], user.get('is_banned')),
    )


@router.callback_query(F.data == "admin_user_lookup")
async def admin_user_lookup_start(callback: CallbackQuery, state: FSMContext):
    """Ask admin for a user ID, username or referral code."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await state.set_state(AdminUserStates.waiting_for_query)
    await _safe_edit(
        callback.message,
        "🔎 <b>Профиль пользователя</b>\n\n"
        "Отправьте ID, @username или реф-код пользователя.\n\n"
        "Примеры:\n"
        "<code>123456789</code>\n"
        "<code>@username</code>\n"
        "<code>REF123abc</code>",
        broadcast_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminUserStates.waiting_for_query)
async def admin_user_lookup_receive(message: Message, state: FSMContext, db: Database):
    """Find a user and show their admin profile card."""
    if not is_admin(message.from_user.id):
        return

    query = (message.text or "").strip()
    user = await db.find_user(query)
    if not user:
        await message.answer(
            "❌ Пользователь не найден.\n\n"
            "Попробуйте ID, @username или реф-код ещё раз.",
            reply_markup=broadcast_cancel_keyboard(),
        )
        return

    await state.clear()
    await message.answer(
        await _admin_user_profile_text(db, user),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_user_profile_keyboard(user['user_id'], user.get('is_banned')),
    )


@router.callback_query(F.data.startswith("admin_user_view:"))
async def admin_user_view(callback: CallbackQuery, db: Database):
    """Refresh/open an admin user profile card."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    user = await db.get_user(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    await _safe_edit(
        callback.message,
        await _admin_user_profile_text(db, user),
        admin_user_profile_keyboard(user['user_id'], user.get('is_banned')),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_refs:"))
async def admin_user_refs(callback: CallbackQuery, db: Database):
    """Show who a user invited."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    user = await db.get_user(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    await _safe_edit(
        callback.message,
        await _admin_user_refs_text(db, user),
        admin_user_refs_keyboard(user_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_ban:"))
async def admin_user_ban(callback: CallbackQuery, db: Database, bot):
    """Ban a user from the admin profile card."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    ok = await db.set_ban(user_id, True)
    if not ok:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    await _notify_user(bot, user_id, "🚫 <b>Вы заблокированы администратором.</b>")
    user = await db.get_user(user_id)
    await _safe_edit(
        callback.message,
        await _admin_user_profile_text(db, user),
        admin_user_profile_keyboard(user_id, True),
    )
    await callback.answer("🚫 Пользователь забанен")
    logger.info(f"Admin banned user {user_id} from profile card")


@router.callback_query(F.data.startswith("admin_user_unban:"))
async def admin_user_unban(callback: CallbackQuery, db: Database, bot):
    """Unban a user from the admin profile card."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    ok = await db.set_ban(user_id, False)
    if not ok:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    await _notify_user(bot, user_id, "♻️ <b>Вы разблокированы. Доступ к боту восстановлен.</b>")
    user = await db.get_user(user_id)
    await _safe_edit(
        callback.message,
        await _admin_user_profile_text(db, user),
        admin_user_profile_keyboard(user_id, False),
    )
    await callback.answer("♻️ Пользователь разбанен")
    logger.info(f"Admin unbanned user {user_id} from profile card")


@router.callback_query(F.data.startswith("admin_user_zero:"))
async def admin_user_zero(callback: CallbackQuery, db: Database):
    """Reset a user's balance from the admin profile card."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    old = await db.reset_balance(user_id)
    if old is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    user = await db.get_user(user_id)
    await _safe_edit(
        callback.message,
        await _admin_user_profile_text(db, user),
        admin_user_profile_keyboard(user_id, user.get('is_banned')),
    )
    await callback.answer(f"🧹 Баланс обнулён (было {fmt_amount(old)} ⭐)")
    logger.info(f"Admin reset balance of user {user_id} from profile card (was {old})")


# ============ WITHDRAWAL LISTS ============

@router.callback_query(F.data.startswith("adm_list:"))
async def admin_list_callback(callback: CallbackQuery, db: Database):
    """Show a paginated list of withdrawals for a given status."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    _, status, offset_str = callback.data.split(":")
    offset = int(offset_str)

    all_w = await db.get_withdrawals_by_status(status)
    total = len(all_w)
    page = all_w[offset:offset + PAGE_SIZE]

    title = STATUS_TITLES.get(status, status)

    if not all_w:
        text = f"{title}\n\nЗдесь пока пусто 📭"
    else:
        total_amount = sum(w['amount'] for w in all_w)
        text = (
            f"<b>{esc(title)}</b>\n\n"
            f"Всего: <b>{total}</b> на <b>{fmt_amount(total_amount)} ⭐</b>\n"
            f"Показаны {offset + 1}–{min(offset + PAGE_SIZE, total)}.\n\n"
            f"Нажмите на заявку, чтобы открыть 👇"
        )

    keyboard = withdrawals_list_keyboard(page, status, offset, PAGE_SIZE, total)
    await _safe_edit(callback.message, text, keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_view:"))
async def admin_view_callback(callback: CallbackQuery, db: Database):
    """Open a single withdrawal card."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    _, wid_str, list_status, offset_str = callback.data.split(":")
    wid = int(wid_str)
    offset = int(offset_str)

    w = await db.get_withdrawal(wid)
    if not w:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    keyboard = withdrawal_card_keyboard(wid, w['status'], list_status, offset, w.get('is_banned'))
    await _safe_edit(callback.message, _withdrawal_card_text(w), keyboard)
    await callback.answer()


# ============ ACTIONS ============

async def _notify_user(bot, user_id: int, text: str):
    try:
        await bot.send_message(user_id, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed to notify user {user_id}: {e}")


@router.callback_query(F.data.startswith("adm_appr:"))
async def admin_approve_callback(callback: CallbackQuery, db: Database, bot):
    """Approve a pending withdrawal (awaiting payout)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    _, wid_str, list_status, offset_str = callback.data.split(":")
    wid, offset = int(wid_str), int(offset_str)

    data = await db.approve_withdrawal(wid)
    if not data:
        await callback.answer("Заявка уже обработана", show_alert=True)
    else:
        await _notify_user(
            bot, data['user_id'],
            f"👌 <b>Ваша заявка на вывод {data['amount']} ⭐ одобрена!</b>\n\n"
            f"Выплата будет произведена в ближайшее время. Ожидайте 🌟"
        )
        await callback.answer("✅ Одобрено")
        logger.info(f"Admin approved withdrawal {wid}")

    w = await db.get_withdrawal(wid)
    await _safe_edit(callback.message, _withdrawal_card_text(w),
                     withdrawal_card_keyboard(wid, w['status'], list_status, offset, w.get('is_banned')))


@router.callback_query(F.data.startswith("adm_paid:"))
async def admin_paid_callback(callback: CallbackQuery, db: Database, bot):
    """Mark a withdrawal as paid out."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    _, wid_str, list_status, offset_str = callback.data.split(":")
    wid, offset = int(wid_str), int(offset_str)

    data = await db.mark_withdrawal_paid(wid)
    if not data:
        await callback.answer("Заявка уже обработана", show_alert=True)
    else:
        await _notify_user(
            bot, data['user_id'],
            f"💸 <b>Выплата {data['amount']} ⭐ отправлена!</b>\n\n"
            f"Звёзды зачислены на ваш аккаунт. Спасибо, что с нами! 🌟"
        )
        await callback.answer("💸 Отмечено как выплачено")
        logger.info(f"Admin marked withdrawal {wid} as paid")

    w = await db.get_withdrawal(wid)
    await _safe_edit(callback.message, _withdrawal_card_text(w),
                     withdrawal_card_keyboard(wid, w['status'], list_status, offset, w.get('is_banned')))


@router.callback_query(F.data.startswith("adm_rej:"))
async def admin_reject_callback(callback: CallbackQuery, db: Database, bot):
    """Reject a withdrawal and refund balance."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    _, wid_str, list_status, offset_str = callback.data.split(":")
    wid, offset = int(wid_str), int(offset_str)

    data = await db.reject_withdrawal(wid, admin_note="Отклонено администратором")
    if not data:
        await callback.answer("Заявка уже обработана", show_alert=True)
    else:
        await _notify_user(
            bot, data['user_id'],
            f"❌ <b>Ваша заявка на вывод {data['amount']} ⭐ отклонена</b>\n\n"
            f"⚠️ Звёзды по этой заявке не возвращаются на баланс."
        )
        await callback.answer("❌ Отклонено (без возврата)")
        logger.info(f"Admin rejected withdrawal {wid}")

    w = await db.get_withdrawal(wid)
    await _safe_edit(callback.message, _withdrawal_card_text(w),
                     withdrawal_card_keyboard(wid, w['status'], list_status, offset, w.get('is_banned')))


async def _refresh_card(callback, db, wid, list_status, offset):
    """Re-render a withdrawal card with up-to-date data after a moderation action."""
    w = await db.get_withdrawal(wid)
    if not w:
        await _safe_edit(callback.message, "Заявка не найдена", None)
        return
    await _safe_edit(callback.message, _withdrawal_card_text(w),
                     withdrawal_card_keyboard(wid, w['status'], list_status, offset, w.get('is_banned')))


@router.callback_query(F.data.startswith("adm_ban:"))
async def admin_ban_callback(callback: CallbackQuery, db: Database, bot):
    """Ban the user behind a withdrawal (from the card)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    _, wid_str, list_status, offset_str = callback.data.split(":")
    wid, offset = int(wid_str), int(offset_str)
    w = await db.get_withdrawal(wid)
    if not w:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await db.set_ban(w['user_id'], True)
    await _notify_user(bot, w['user_id'], "🚫 <b>Вы заблокированы администратором.</b>")
    await callback.answer("🚫 Пользователь забанен")
    logger.info(f"Admin banned user {w['user_id']} (withdrawal {wid})")
    await _refresh_card(callback, db, wid, list_status, offset)


@router.callback_query(F.data.startswith("adm_unban:"))
async def admin_unban_callback(callback: CallbackQuery, db: Database, bot):
    """Unban the user behind a withdrawal (from the card)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    _, wid_str, list_status, offset_str = callback.data.split(":")
    wid, offset = int(wid_str), int(offset_str)
    w = await db.get_withdrawal(wid)
    if not w:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await db.set_ban(w['user_id'], False)
    await _notify_user(bot, w['user_id'], "♻️ <b>Вы разблокированы. Доступ к боту восстановлен.</b>")
    await callback.answer("♻️ Пользователь разбанен")
    logger.info(f"Admin unbanned user {w['user_id']} (withdrawal {wid})")
    await _refresh_card(callback, db, wid, list_status, offset)


@router.callback_query(F.data.startswith("adm_zero:"))
async def admin_zero_callback(callback: CallbackQuery, db: Database, bot):
    """Reset the user's balance to 0 (from the card)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    _, wid_str, list_status, offset_str = callback.data.split(":")
    wid, offset = int(wid_str), int(offset_str)
    w = await db.get_withdrawal(wid)
    if not w:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    old = await db.reset_balance(w['user_id'])
    await callback.answer(f"🧹 Баланс обнулён (было {fmt_amount(old)} ⭐)" if old is not None else "Пользователь не найден")
    logger.info(f"Admin reset balance of user {w['user_id']} (was {old})")
    await _refresh_card(callback, db, wid, list_status, offset)


# ============ DIRECT NOTIFICATION BUTTONS (from the channel post when a request is created) ============

@router.callback_query(F.data.startswith("nappr:"))
async def notif_approve(callback: CallbackQuery, db: Database, bot):
    """Approve directly from the notification message."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    wid = int(callback.data.split(":")[1])
    data = await db.approve_withdrawal(wid)
    if not data:
        await callback.answer("Заявка уже обработана", show_alert=True)
    else:
        await _notify_user(
            bot, data['user_id'],
            f"👌 <b>Ваша заявка на вывод {data['amount']} ⭐ одобрена!</b>\n\n"
            f"Выплата будет произведена в ближайшее время 🌟"
        )
        await callback.answer("✅ Одобрено")
    await _refresh_notif(callback, db, wid)


@router.callback_query(F.data.startswith("npaid:"))
async def notif_paid(callback: CallbackQuery, db: Database, bot):
    """Mark paid directly from the notification message."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    wid = int(callback.data.split(":")[1])
    data = await db.mark_withdrawal_paid(wid)
    if not data:
        await callback.answer("Заявка уже обработана", show_alert=True)
    else:
        await _notify_user(
            bot, data['user_id'],
            f"💸 <b>Выплата {data['amount']} ⭐ отправлена!</b>\n\n"
            f"Звёзды зачислены на ваш аккаунт 🌟"
        )
        await callback.answer("💸 Выплачено")
    await _refresh_notif(callback, db, wid)


@router.callback_query(F.data.startswith("nrej:"))
async def notif_reject(callback: CallbackQuery, db: Database, bot):
    """Reject directly from the notification message."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    wid = int(callback.data.split(":")[1])
    data = await db.reject_withdrawal(wid, admin_note="Отклонено администратором")
    if not data:
        await callback.answer("Заявка уже обработана", show_alert=True)
    else:
        await _notify_user(
            bot, data['user_id'],
            f"❌ <b>Ваша заявка на вывод {data['amount']} ⭐ отклонена</b>\n\n"
            f"⚠️ Звёзды по этой заявке не возвращаются на баланс."
        )
        await callback.answer("❌ Отклонено (без возврата)")
    await _refresh_notif(callback, db, wid)


@router.callback_query(F.data.startswith("nban:"))
async def notif_ban(callback: CallbackQuery, db: Database, bot):
    """Ban the user directly from the channel post."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    wid = int(callback.data.split(":")[1])
    w = await db.get_withdrawal(wid)
    if not w:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await db.set_ban(w['user_id'], True)
    await _notify_user(bot, w['user_id'], "🚫 <b>Вы заблокированы администратором.</b>")
    await callback.answer("🚫 Забанен")
    logger.info(f"Admin banned user {w['user_id']} (withdrawal {wid})")
    await _refresh_notif(callback, db, wid)


@router.callback_query(F.data.startswith("nunban:"))
async def notif_unban(callback: CallbackQuery, db: Database, bot):
    """Unban the user directly from the channel post."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    wid = int(callback.data.split(":")[1])
    w = await db.get_withdrawal(wid)
    if not w:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await db.set_ban(w['user_id'], False)
    await _notify_user(bot, w['user_id'], "♻️ <b>Вы разблокированы. Доступ к боту восстановлен.</b>")
    await callback.answer("♻️ Разбанен")
    logger.info(f"Admin unbanned user {w['user_id']} (withdrawal {wid})")
    await _refresh_notif(callback, db, wid)


@router.callback_query(F.data.startswith("nzero:"))
async def notif_zero(callback: CallbackQuery, db: Database, bot):
    """Reset the user's balance to 0 directly from the channel post."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    wid = int(callback.data.split(":")[1])
    w = await db.get_withdrawal(wid)
    if not w:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    old = await db.reset_balance(w['user_id'])
    await callback.answer(f"🧹 Баланс обнулён (было {fmt_amount(old)} ⭐)" if old is not None else "Пользователь не найден")
    logger.info(f"Admin reset balance of user {w['user_id']} (was {old})")
    await _refresh_notif(callback, db, wid)


async def _refresh_notif(callback: CallbackQuery, db: Database, wid: int):
    """Refresh the channel/DM notification message after an action, keeping live buttons."""
    w = await db.get_withdrawal(wid)
    if not w:
        return
    keyboard = notification_keyboard(wid, w['status'], w.get('is_banned'))
    await _safe_edit(callback.message, _withdrawal_card_text(w), keyboard)


# ============ BROADCAST ============

@router.callback_query(F.data == "admin_broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    """Ask the admin for the message to broadcast."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.set_state(BroadcastStates.waiting_for_message)
    await _safe_edit(
        callback.message,
        "📢 <b>Рассылка</b>\n\n"
        "Отправьте сообщение, которое нужно разослать всем пользователям.\n"
        "Это может быть текст, фото, видео — что угодно.\n\n"
        "Для отмены нажмите кнопку ниже.",
        broadcast_cancel_keyboard(),
    )
    await callback.answer()


@router.message(BroadcastStates.waiting_for_message)
async def broadcast_receive(message: Message, state: FSMContext, db: Database):
    """Store the message and ask for confirmation."""
    if not is_admin(message.from_user.id):
        return
    await state.update_data(from_chat_id=message.chat.id, message_id=message.message_id)
    await state.set_state(BroadcastStates.confirm)

    total = len(await db.get_all_user_ids())
    await message.answer(
        f"👆 Это сообщение будет отправлено <b>{total}</b> пользователям.\n\n"
        f"Отправляем?",
        parse_mode=ParseMode.HTML,
        reply_markup=broadcast_confirm_keyboard(),
    )


@router.callback_query(F.data == "bc_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext, db: Database):
    """Cancel the broadcast and return to the admin panel."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.clear()
    stats = await db.get_stats()
    await _safe_edit(callback.message, _admin_home_text(stats), admin_keyboard())
    await callback.answer("Рассылка отменена")


@router.callback_query(F.data == "bc_send", BroadcastStates.confirm)
async def broadcast_send(callback: CallbackQuery, state: FSMContext, db: Database, bot):
    """Send the stored message to every user."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    from_chat_id = data.get("from_chat_id")
    message_id = data.get("message_id")
    await state.clear()

    if not from_chat_id or not message_id:
        await callback.answer("Сообщение потеряно, начните заново", show_alert=True)
        return

    await callback.answer("📤 Рассылка запущена")
    await _safe_edit(callback.message, "📤 <b>Рассылка началась…</b>", None)

    user_ids = await db.get_all_user_ids()
    sent = failed = 0
    for i, uid in enumerate(user_ids, 1):
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=from_chat_id, message_id=message_id)
            sent += 1
        except Exception as e:
            failed += 1
            logger.debug(f"Broadcast to {uid} failed: {e}")
        # Stay under Telegram's ~30 msg/sec limit
        if i % 25 == 0:
            await asyncio.sleep(1)

    logger.info(f"Broadcast finished: sent={sent} failed={failed} total={len(user_ids)}")
    await _safe_edit(
        callback.message,
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"📨 Доставлено: <b>{sent}</b>\n"
        f"🚫 Не доставлено: <b>{failed}</b>\n"
        f"👥 Всего: <b>{len(user_ids)}</b>",
        admin_back_keyboard(),
    )


# ============ PROMO CODES ============

def _promos_text(promos: list) -> str:
    if not promos:
        return (
            "🎟 <b>Промокоды</b>\n\n"
            "Пока нет ни одного промокода.\n\n"
            "Нажмите «➕ Создать промокод», чтобы добавить."
        )
    lines = ["🎟 <b>Промокоды</b>\n"]
    for p in promos:
        limit = "∞" if not p['max_activations'] else p['max_activations']
        lines.append(
            f"• <code>{esc(p['code'])}</code> — <b>{fmt_amount(p['reward'])} ⭐</b> "
            f"(активаций: {p['used_count']}/{limit})"
        )
    lines.append("\nНажмите на промокод, чтобы удалить его 🗑")
    return "\n".join(lines)


@router.callback_query(F.data == "admin_promos")
async def admin_promos(callback: CallbackQuery, db: Database):
    """Show the list of promo codes."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    promos = await db.list_promos()
    await _safe_edit(callback.message, _promos_text(promos), promos_keyboard(promos))
    await callback.answer()


@router.callback_query(F.data == "promo_new")
async def promo_new(callback: CallbackQuery, state: FSMContext):
    """Prompt the admin to enter a new promo code."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.set_state(PromoStates.waiting_for_promo)
    await _safe_edit(
        callback.message,
        "➕ <b>Новый промокод</b>\n\n"
        "Отправьте в одном сообщении:\n"
        "<code>КОД СУММА КОЛИЧЕСТВО</code>\n\n"
        "• <b>КОД</b> — например <code>BONUS50</code>\n"
        "• <b>СУММА</b> — сколько ⭐ начислять (например 50)\n"
        "• <b>КОЛИЧЕСТВО</b> — лимит активаций (0 = безлимит, можно не указывать)\n\n"
        "Примеры:\n"
        "<code>BONUS50 50 100</code> — 50⭐, 100 активаций\n"
        "<code>VIP 25</code> — 25⭐, без лимита",
        broadcast_cancel_keyboard(),
    )
    await callback.answer()


@router.message(PromoStates.waiting_for_promo)
async def promo_create(message: Message, state: FSMContext, db: Database):
    """Parse and create the promo code."""
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "❌ Неверный формат. Нужно: <code>КОД СУММА [КОЛИЧЕСТВО]</code>",
            parse_mode=ParseMode.HTML, reply_markup=broadcast_cancel_keyboard()
        )
        return

    code = parts[0]
    try:
        reward = float(parts[1].replace(",", "."))
    except ValueError:
        await message.answer("❌ Сумма должна быть числом, например 50.",
                             reply_markup=broadcast_cancel_keyboard())
        return
    if reward <= 0:
        await message.answer("❌ Сумма должна быть больше нуля.",
                             reply_markup=broadcast_cancel_keyboard())
        return

    max_act = 0
    if len(parts) >= 3:
        try:
            max_act = int(parts[2])
        except ValueError:
            await message.answer("❌ Количество должно быть целым числом (0 = безлимит).",
                                 reply_markup=broadcast_cancel_keyboard())
            return

    ok = await db.create_promo(code, reward, max_act)
    await state.clear()
    if not ok:
        await message.answer(
            f"❌ Промокод <code>{esc(code.upper())}</code> уже существует.",
            parse_mode=ParseMode.HTML, reply_markup=admin_back_keyboard()
        )
        return

    limit = "∞" if not max_act else max_act
    await message.answer(
        f"✅ <b>Промокод создан!</b>\n\n"
        f"🎟 Код: <code>{esc(code.upper())}</code>\n"
        f"⭐ Награда: <b>{fmt_amount(reward)}</b>\n"
        f"🔢 Лимит активаций: <b>{limit}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_keyboard(),
    )
    logger.info(f"Admin created promo {code.upper()} reward={reward} max={max_act}")


@router.callback_query(F.data.startswith("promo_del:"))
async def promo_delete(callback: CallbackQuery, db: Database):
    """Delete a promo code and refresh the list."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    promo_id = int(callback.data.split(":")[1])
    await db.delete_promo(promo_id)
    await callback.answer("🗑 Промокод удалён")
    promos = await db.list_promos()
    await _safe_edit(callback.message, _promos_text(promos), promos_keyboard(promos))


# ============ LEGACY COMMANDS (still work) ============

@router.message(Command("pending"))
async def pending_withdrawals(message: Message, db: Database):
    """List pending withdrawal requests (command)."""
    if not is_admin(message.from_user.id):
        return
    withdrawals = await db.get_pending_withdrawals()
    if not withdrawals:
        await message.answer("📭 Нет новых заявок на вывод", reply_markup=admin_keyboard())
        return
    await message.answer(
        "Открываю список заявок 👇",
        reply_markup=withdrawals_list_keyboard(
            withdrawals[:PAGE_SIZE], "pending", 0, PAGE_SIZE, len(withdrawals)
        )
    )


@router.message(Command("stats"))
async def detailed_stats(message: Message, db: Database):
    """Show detailed statistics (command)."""
    if not is_admin(message.from_user.id):
        return
    stats = await db.get_stats()
    activity = (stats['active_users_week'] / stats['total_users'] * 100) if stats['total_users'] else 0
    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"📈 Активных за неделю: {stats['active_users_week']} ({activity:.1f}%)\n"
        f"⭐ Начислено рефералам: {fmt_amount(stats['total_earned'])} ⭐\n"
        f"📤 Выплачено: {fmt_amount(stats['total_withdrawn'])} ⭐\n"
        f"⏳ Новых заявок: {stats['pending_withdrawals_count']} ({fmt_amount(stats['pending_withdrawals_amount'])} ⭐)"
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())
