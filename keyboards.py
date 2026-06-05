"""Keyboard layouts for the bot."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config


def main_keyboard() -> InlineKeyboardMarkup:
    """Main menu keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Моя реферальная ссылка", callback_data="my_link")],
        [InlineKeyboardButton(text="⭐ Мой баланс", callback_data="balance")],
        [InlineKeyboardButton(text="💸 Вывести Stars", callback_data="withdraw")],
        [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="enter_promo")],
        [InlineKeyboardButton(text="👥 Мои рефералы", callback_data="my_refs")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="🏆 Топ рефералов", callback_data="top_referrers")],
        [InlineKeyboardButton(text="📜 История выводов", callback_data="withdrawal_history")]
    ])


def withdraw_keyboard(user_balance: int) -> InlineKeyboardMarkup:
    """Withdrawal amounts keyboard."""
    buttons = []
    for amount in Config.WITHDRAW_AMOUNTS:
        # Disable button if insufficient balance
        if user_balance >= amount:
            text = f"💎 {amount} ⭐"
            callback = f"withdraw_{amount}"
        else:
            text = f"🔒 {amount} ⭐"
            callback = f"insufficient_{amount}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=callback)])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_button() -> InlineKeyboardMarkup:
    """Simple back button."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
    ])


def balance_keyboard() -> InlineKeyboardMarkup:
    """Balance screen keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывести", callback_data="withdraw")],
        [InlineKeyboardButton(text="📜 История", callback_data="withdrawal_history")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
    ])


def referral_keyboard() -> InlineKeyboardMarkup:
    """Referral link screen keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список рефералов", callback_data="my_refs")],
        [InlineKeyboardButton(text="🏆 Топ рефералов", callback_data="top_referrers")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    """Admin panel keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ Новые заявки", callback_data="adm_list:pending:0")],
        [InlineKeyboardButton(text="👌 Одобренные (к выплате)", callback_data="adm_list:approved:0")],
        [InlineKeyboardButton(text="✅ Выплаченные", callback_data="adm_list:completed:0")],
        [InlineKeyboardButton(text="❌ Отклонённые", callback_data="adm_list:rejected:0")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin_promos")],
    ])


def promos_keyboard(promos) -> InlineKeyboardMarkup:
    """Admin promo list: each promo has a delete button, plus create + back."""
    rows = []
    for p in promos:
        limit = "∞" if not p['max_activations'] else p['max_activations']
        label = f"🗑 {p['code']} • {p['reward']}⭐ • {p['used_count']}/{limit}"
        rows.append([InlineKeyboardButton(text=label[:60], callback_data=f"promo_del:{p['id']}")])
    rows.append([InlineKeyboardButton(text="➕ Создать промокод", callback_data="promo_new")])
    rows.append([InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    """Cancel button shown while waiting for the broadcast message."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="bc_cancel")]
    ])


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirm/cancel before sending the broadcast to everyone."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить всем", callback_data="bc_send")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="bc_cancel")],
    ])


def admin_back_keyboard() -> InlineKeyboardMarkup:
    """Back to admin panel."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin_home")]
    ])


def withdrawal_card_keyboard(withdrawal_id: int, status: str, list_status: str, offset: int,
                             is_banned: bool = False) -> InlineKeyboardMarkup:
    """
    Action buttons for a single withdrawal card.
    Buttons depend on current status. `list_status`/`offset` let us return to the same list page.
    """
    rows = []
    suffix = f"{withdrawal_id}:{list_status}:{offset}"
    back_to_list = f"adm_list:{list_status}:{offset}"

    if status == "pending":
        rows.append([
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"adm_appr:{suffix}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_rej:{suffix}"),
        ])
        rows.append([
            InlineKeyboardButton(text="💸 Сразу выплачено", callback_data=f"adm_paid:{suffix}"),
        ])
    elif status == "approved":
        rows.append([
            InlineKeyboardButton(text="💸 Выплачено", callback_data=f"adm_paid:{suffix}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_rej:{suffix}"),
        ])

    # Moderation actions (always available)
    if is_banned:
        rows.append([InlineKeyboardButton(text="♻️ Разбанить", callback_data=f"adm_unban:{suffix}")])
    else:
        rows.append([InlineKeyboardButton(text="🚫 Забанить", callback_data=f"adm_ban:{suffix}")])
    rows.append([InlineKeyboardButton(text="🧹 Обнулить баланс", callback_data=f"adm_zero:{suffix}")])

    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data=back_to_list)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notification_keyboard(withdrawal_id: int, status: str, is_banned: bool = False) -> InlineKeyboardMarkup:
    """
    Live action buttons for the withdrawal post in the admin channel / DM.
    Uses the short 'n*' callbacks that operate without a list context.
    """
    rows = []
    if status == "pending":
        rows.append([
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"nappr:{withdrawal_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"nrej:{withdrawal_id}"),
        ])
        rows.append([InlineKeyboardButton(text="💸 Сразу выплачено", callback_data=f"npaid:{withdrawal_id}")])
    elif status == "approved":
        rows.append([
            InlineKeyboardButton(text="💸 Выплачено", callback_data=f"npaid:{withdrawal_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"nrej:{withdrawal_id}"),
        ])

    if is_banned:
        rows.append([InlineKeyboardButton(text="♻️ Разбанить", callback_data=f"nunban:{withdrawal_id}")])
    else:
        rows.append([InlineKeyboardButton(text="🚫 Забанить", callback_data=f"nban:{withdrawal_id}")])
    rows.append([InlineKeyboardButton(text="🧹 Обнулить баланс", callback_data=f"nzero:{withdrawal_id}")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def withdrawals_list_keyboard(withdrawals, list_status: str, offset: int, page_size: int, total: int) -> InlineKeyboardMarkup:
    """A list of withdrawals as buttons + pagination + back."""
    rows = []
    for w in withdrawals:
        label = f"#{w['id']} • {w['amount']} ⭐ • {w['first_name'] or w['username'] or w['user_id']}"
        rows.append([InlineKeyboardButton(text=label[:60], callback_data=f"adm_view:{w['id']}:{list_status}:{offset}")])

    # Pagination
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm_list:{list_status}:{max(0, offset - page_size)}"))
    if offset + page_size < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm_list:{list_status}:{offset + page_size}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
