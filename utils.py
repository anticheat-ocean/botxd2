"""Utility functions for the bot."""
import html
from datetime import datetime
from typing import Optional


def esc(text) -> str:
    """Escape text for safe use inside HTML parse mode."""
    return html.escape(str(text), quote=False)


def fmt_amount(value) -> str:
    """Format a star amount: whole numbers without decimals, fractional with up to 2."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    # strip trailing zeros (3.50 -> 3.5)
    return f"{f:.2f}".rstrip("0").rstrip(".")


def format_datetime(dt_string: Optional[str]) -> str:
    """Format datetime string to readable format."""
    if not dt_string:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_string)
        return dt.strftime("%d.%m.%Y %H:%M")
    except:
        return dt_string


def format_status(status: str) -> str:
    """Format withdrawal status with emoji."""
    status_map = {
        'pending': '⏳ Ожидает',
        'approved': '👌 Одобрено, ждёт выплаты',
        'completed': '✅ Выплачено',
        'rejected': '❌ Отклонено'
    }
    return status_map.get(status, status)


def escape_markdown(text: str) -> str:
    """Escape markdown special characters."""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text


def get_user_display_name(username: Optional[str], first_name: Optional[str], user_id: int) -> str:
    """Get user display name."""
    if username:
        return f"@{username}"
    elif first_name:
        return first_name
    else:
        return f"User {user_id}"
