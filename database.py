"""Database module for the referral bot."""
import aiosqlite
import random
import secrets
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from pathlib import Path
from config import Config


class Database:
    """Async database handler."""

    def __init__(self, db_path: str = Config.DATABASE_PATH):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Connect to database and initialize tables."""
        db_parent = Path(self.db_path).expanduser().parent
        if str(db_parent) not in ("", "."):
            db_parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self._init_tables()

    async def close(self):
        """Close database connection."""
        if self.conn:
            await self.conn.close()

    async def _init_tables(self):
        """Initialize database tables."""
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                referrer_id INTEGER,
                ref_code TEXT UNIQUE,
                balance REAL DEFAULT 0,
                total_earned REAL DEFAULT 0,
                total_referrals INTEGER DEFAULT 0,
                total_withdrawn REAL DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                is_twink INTEGER DEFAULT 0,
                phone TEXT,
                phone_country TEXT,
                phone_verified INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Migrations: add new columns to pre-existing databases that lack them
        async with self.conn.execute("PRAGMA table_info(users)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        if "is_banned" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
        if "is_twink" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN is_twink INTEGER DEFAULT 0")
        if "verified" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0")
            # Grandfather every existing user as verified so the new captcha
            # only ever applies to brand-new sign-ups.
            await self.conn.execute("UPDATE users SET verified = 1")
        if "phone" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")
        if "phone_country" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN phone_country TEXT")
        if "phone_verified" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN phone_verified INTEGER DEFAULT 0")
            # Grandfather existing users so the phone gate only applies to new sign-ups.
            await self.conn.execute("UPDATE users SET phone_verified = 1")

        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP,
                admin_note TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')

        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS referral_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                reward_amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (referrer_id) REFERENCES users(user_id),
                FOREIGN KEY (referred_id) REFERENCES users(user_id)
            )
        ''')

        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')

        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS sponsors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT UNIQUE NOT NULL,
                channel_name TEXT NOT NULL,
                channel_url TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                reward REAL NOT NULL,
                max_activations INTEGER DEFAULT 0,
                used_count INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS promo_activations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                reward REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(code, user_id)
            )
        ''')

        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS piarflow_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                link TEXT NOT NULL,
                price REAL DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                UNIQUE(user_id, link)
            )
        ''')

        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS user_reward_state (
                user_id INTEGER NOT NULL,
                reward_type TEXT NOT NULL,
                last_claimed TIMESTAMP,
                streak INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, reward_type),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')

        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS box_game_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                winning_box INTEGER NOT NULL,
                reward REAL NOT NULL,
                selected_box INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_box_game_user_status ON box_game_sessions(user_id, status, completed_at)"
        )

        await self.conn.commit()

    @staticmethod
    def generate_ref_code(user_id: int) -> str:
        """Generate unique referral code."""
        return f"REF{user_id}{secrets.token_hex(3)}"

    async def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user by ID."""
        async with self.conn.execute(
            """SELECT user_id, username, first_name, referrer_id, ref_code,
                      balance, total_earned, total_referrals, total_withdrawn,
                      is_banned, verified, is_twink,
                      phone, phone_country, phone_verified,
                      created_at, last_active
               FROM users WHERE user_id = ?""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
        return None

    async def register_user(
        self,
        user_id: int,
        username: Optional[str],
        first_name: str,
        referrer_code: Optional[str] = None
    ) -> Tuple[bool, Optional[int]]:
        """
        Register new user.
        Returns: (success, referrer_id)
        """
        # Check if user exists
        existing = await self.get_user(user_id)
        if existing:
            return False, None

        # Find referrer
        referrer_id = None
        if referrer_code:
            async with self.conn.execute(
                "SELECT user_id FROM users WHERE ref_code = ?",
                (referrer_code,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    referrer_id = row[0]

        # Create user
        ref_code = self.generate_ref_code(user_id)
        await self.conn.execute(
            """INSERT INTO users (user_id, username, first_name, referrer_id, ref_code)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, username, first_name, referrer_id, ref_code)
        )

        # Log activity
        await self.log_activity(user_id, "register", f"New user registered")

        # NOTE: the referrer is NOT rewarded here anymore. The reward is granted
        # later by credit_referral() — only after the new user passes the captcha
        # and is not flagged as a twink. This prevents alt-account farming.

        await self.conn.commit()
        return True, referrer_id

    async def update_last_active(self, user_id: int):
        """Update user's last active timestamp."""
        await self.conn.execute(
            "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
            (user_id,)
        )
        await self.conn.commit()

    async def create_withdrawal(self, user_id: int, amount: int) -> Tuple[bool, str, Optional[int]]:
        """
        Create withdrawal request.
        Returns: (success, message, withdrawal_id)
        """
        user = await self.get_user(user_id)
        if not user:
            return False, "Пользователь не найден", None

        # Check balance
        if user['balance'] < amount:
            return False, f"Недостаточно средств. Ваш баланс: {user['balance']} ⭐", None

        # Check pending withdrawals limit
        async with self.conn.execute(
            "SELECT COUNT(*) FROM withdrawals WHERE user_id = ? AND status = 'pending'",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            pending_count = row[0]
            if pending_count >= Config.MAX_PENDING_WITHDRAWALS:
                return False, f"У вас уже есть {pending_count} активных заявок. Дождитесь их обработки.", None

        # Check last withdrawal time
        async with self.conn.execute(
            """SELECT created_at FROM withdrawals
               WHERE user_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                last_withdrawal = datetime.fromisoformat(row[0])
                time_diff = datetime.now() - last_withdrawal
                if time_diff < timedelta(hours=Config.MIN_WITHDRAW_INTERVAL_HOURS):
                    hours_left = Config.MIN_WITHDRAW_INTERVAL_HOURS - (time_diff.total_seconds() / 3600)
                    return False, f"Следующий вывод доступен через {hours_left:.1f} часов", None

        # Create withdrawal
        cursor = await self.conn.execute(
            "INSERT INTO withdrawals (user_id, amount) VALUES (?, ?)",
            (user_id, amount)
        )
        withdrawal_id = cursor.lastrowid

        # Deduct balance
        await self.conn.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (amount, user_id)
        )

        await self.log_activity(user_id, "withdrawal_request", f"Requested withdrawal of {amount} stars")
        await self.conn.commit()

        return True, "Заявка создана успешно", withdrawal_id

    async def get_pending_withdrawals(self) -> List[Dict]:
        """Get all pending withdrawal requests."""
        async with self.conn.execute(
            """SELECT w.id, w.user_id, w.amount, w.created_at, u.username, u.first_name
               FROM withdrawals w
               JOIN users u ON w.user_id = u.user_id
               WHERE w.status = 'pending'
               ORDER BY w.created_at ASC"""
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_withdrawals_by_status(self, status: str) -> List[Dict]:
        """Get all withdrawals with a given status, joined with user info."""
        async with self.conn.execute(
            """SELECT w.id, w.user_id, w.amount, w.status, w.created_at, w.processed_at,
                      u.username, u.first_name
               FROM withdrawals w
               JOIN users u ON w.user_id = u.user_id
               WHERE w.status = ?
               ORDER BY w.created_at ASC""",
            (status,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_withdrawal(self, withdrawal_id: int) -> Optional[Dict]:
        """Get a single withdrawal by id, joined with user info and referrer info."""
        async with self.conn.execute(
            """SELECT w.id, w.user_id, w.amount, w.status, w.created_at, w.processed_at,
                      w.admin_note,
                      u.username, u.first_name, u.balance, u.is_banned,
                      u.total_referrals, u.referrer_id,
                      r.username AS referrer_username,
                      r.first_name AS referrer_first_name
               FROM withdrawals w
               JOIN users u ON w.user_id = u.user_id
               LEFT JOIN users r ON u.referrer_id = r.user_id
               WHERE w.id = ?""",
            (withdrawal_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def approve_withdrawal(self, withdrawal_id: int, admin_note: Optional[str] = None) -> Optional[Dict]:
        """
        Approve a pending withdrawal (mark as 'approved', awaiting payout).
        Does NOT count toward total_withdrawn yet — that happens on payout.
        Returns: withdrawal data if successful, None if not pending.
        """
        async with self.conn.execute(
            "SELECT user_id, amount FROM withdrawals WHERE id = ? AND status = 'pending'",
            (withdrawal_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            withdrawal_data = dict(row)

        await self.conn.execute(
            """UPDATE withdrawals
               SET status = 'approved',
                   processed_at = CURRENT_TIMESTAMP,
                   admin_note = ?
               WHERE id = ?""",
            (admin_note, withdrawal_id)
        )

        await self.log_activity(
            withdrawal_data['user_id'],
            "withdrawal_approved",
            f"Withdrawal of {withdrawal_data['amount']} stars approved (awaiting payout)"
        )

        await self.conn.commit()
        return withdrawal_data

    async def mark_withdrawal_paid(self, withdrawal_id: int) -> Optional[Dict]:
        """
        Mark an approved (or pending) withdrawal as paid/completed.
        Counts the amount toward the user's total_withdrawn.
        Returns: withdrawal data if successful, None otherwise.
        """
        async with self.conn.execute(
            "SELECT user_id, amount FROM withdrawals WHERE id = ? AND status IN ('approved', 'pending')",
            (withdrawal_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            withdrawal_data = dict(row)

        await self.conn.execute(
            """UPDATE withdrawals
               SET status = 'completed',
                   processed_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (withdrawal_id,)
        )

        # Now it's actually paid — count it
        await self.conn.execute(
            "UPDATE users SET total_withdrawn = total_withdrawn + ? WHERE user_id = ?",
            (withdrawal_data['amount'], withdrawal_data['user_id'])
        )

        await self.log_activity(
            withdrawal_data['user_id'],
            "withdrawal_paid",
            f"Withdrawal of {withdrawal_data['amount']} stars paid out"
        )

        await self.conn.commit()
        return withdrawal_data

    async def reject_withdrawal(self, withdrawal_id: int, admin_note: Optional[str] = None) -> Optional[Dict]:
        """
        Reject a pending or approved withdrawal.
        IMPORTANT: the stars are NOT refunded — the amount stays deducted from the user's balance.
        Returns: withdrawal data if successful, None otherwise.
        """
        async with self.conn.execute(
            "SELECT user_id, amount FROM withdrawals WHERE id = ? AND status IN ('pending', 'approved')",
            (withdrawal_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            withdrawal_data = dict(row)

        # Update withdrawal status (no balance refund — stars are burned on rejection)
        await self.conn.execute(
            """UPDATE withdrawals
               SET status = 'rejected',
                   processed_at = CURRENT_TIMESTAMP,
                   admin_note = ?
               WHERE id = ?""",
            (admin_note, withdrawal_id)
        )

        await self.log_activity(
            withdrawal_data['user_id'],
            "withdrawal_rejected",
            f"Withdrawal of {withdrawal_data['amount']} stars rejected (no refund)"
        )

        await self.conn.commit()
        return withdrawal_data

    async def is_phone_verified(self, user_id: int) -> bool:
        """Check whether a user has passed the phone-number gate."""
        async with self.conn.execute(
            "SELECT phone_verified FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row else False

    async def set_phone(self, user_id: int, phone: str, country: str, verified: bool):
        """Store a user's phone, detected country and whether it passed the gate."""
        await self.conn.execute(
            "UPDATE users SET phone = ?, phone_country = ?, phone_verified = ? WHERE user_id = ?",
            (phone, country, 1 if verified else 0, user_id)
        )
        await self.log_activity(
            user_id,
            "phone_verified" if verified else "phone_blocked",
            f"Phone country: {country}"
        )
        await self.conn.commit()

    async def mark_verified(self, user_id: int):
        """Mark a user as having passed the captcha."""
        await self.conn.execute(
            "UPDATE users SET verified = 1 WHERE user_id = ?",
            (user_id,)
        )
        await self.conn.commit()

    async def set_twink(self, user_id: int, is_twink: bool):
        """Flag/unflag a user as a suspected twink (alt account)."""
        await self.conn.execute(
            "UPDATE users SET is_twink = ? WHERE user_id = ?",
            (1 if is_twink else 0, user_id)
        )
        await self.conn.commit()

    async def credit_referral(self, user_id: int) -> Optional[Dict]:
        """
        Grant the referral reward to the referrer of `user_id`, exactly once.
        Called only after the new user passes the captcha and isn't a twink.
        Returns: {referrer_id, reward, referrer_balance} or None if nothing to credit.
        """
        user = await self.get_user(user_id)
        if not user or not user.get('referrer_id'):
            return None
        referrer_id = user['referrer_id']

        # Guard against double crediting: a referral_history row means already paid
        async with self.conn.execute(
            "SELECT 1 FROM referral_history WHERE referred_id = ?",
            (user_id,)
        ) as cursor:
            if await cursor.fetchone():
                return None

        reward = Config.REWARD_PER_REFERRAL
        await self.conn.execute(
            """UPDATE users
               SET balance = balance + ?,
                   total_earned = total_earned + ?,
                   total_referrals = total_referrals + 1
               WHERE user_id = ?""",
            (reward, reward, referrer_id)
        )
        await self.conn.execute(
            """INSERT INTO referral_history (referrer_id, referred_id, reward_amount)
               VALUES (?, ?, ?)""",
            (referrer_id, user_id, reward)
        )
        await self.log_activity(
            referrer_id,
            "referral_reward",
            f"Earned {reward} stars from user {user_id}"
        )
        await self.conn.commit()

        referrer = await self.get_user(referrer_id)
        return {
            "referrer_id": referrer_id,
            "reward": reward,
            "referrer_balance": referrer['balance'] if referrer else 0,
        }

    async def set_ban(self, user_id: int, banned: bool) -> bool:
        """Ban or unban a user. Returns True if the user exists."""
        user = await self.get_user(user_id)
        if not user:
            return False
        await self.conn.execute(
            "UPDATE users SET is_banned = ? WHERE user_id = ?",
            (1 if banned else 0, user_id)
        )
        await self.log_activity(
            user_id,
            "banned" if banned else "unbanned",
            "User banned by admin" if banned else "User unbanned by admin"
        )
        await self.conn.commit()
        return True

    async def is_user_banned(self, user_id: int) -> bool:
        """Check whether a user is banned."""
        async with self.conn.execute(
            "SELECT is_banned FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row else False

    async def reset_balance(self, user_id: int) -> Optional[float]:
        """
        Reset a user's available balance to 0.
        Returns the balance that was zeroed out, or None if the user doesn't exist.
        """
        user = await self.get_user(user_id)
        if not user:
            return None
        old_balance = user['balance']
        await self.conn.execute(
            "UPDATE users SET balance = 0 WHERE user_id = ?",
            (user_id,)
        )
        await self.log_activity(
            user_id,
            "balance_reset",
            f"Balance reset from {old_balance} to 0 by admin"
        )
        await self.conn.commit()
        return old_balance

    async def get_user_withdrawals(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Get user's withdrawal history."""
        async with self.conn.execute(
            """SELECT id, amount, status, created_at, processed_at
               FROM withdrawals
               WHERE user_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_all_user_ids(self, include_banned: bool = False) -> List[int]:
        """Return all user IDs (for broadcasts). Banned users are excluded by default."""
        query = "SELECT user_id FROM users"
        if not include_banned:
            query += " WHERE is_banned = 0"
        async with self.conn.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def get_user_referrals(self, user_id: int, limit: int = 50) -> List[Dict]:
        """Get user's referrals."""
        async with self.conn.execute(
            """SELECT user_id, username, first_name, created_at
               FROM users
               WHERE referrer_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_top_referrers(self, limit: int = 10) -> List[Dict]:
        """Get top referrers leaderboard."""
        async with self.conn.execute(
            """SELECT user_id, username, first_name, total_referrals, total_earned
               FROM users
               WHERE total_referrals > 0
               ORDER BY total_referrals DESC, total_earned DESC
               LIMIT ?""",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ============ FARM / MINI GAMES ============

    @staticmethod
    def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.replace(microsecond=0).isoformat(sep=" ")

    async def get_daily_bonus_status(self, user_id: int) -> Dict:
        """Return whether the user can claim the daily bonus and their streak."""
        async with self.conn.execute(
            """SELECT last_claimed, streak FROM user_reward_state
               WHERE user_id = ? AND reward_type = 'daily'""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        now = datetime.now()
        last_claimed = self._parse_timestamp(row["last_claimed"]) if row else None
        streak = row["streak"] if row else 0
        next_claim_at = last_claimed + timedelta(hours=24) if last_claimed else None
        can_claim = not next_claim_at or now >= next_claim_at

        return {
            "can_claim": can_claim,
            "streak": streak,
            "next_claim_at": next_claim_at,
            "seconds_left": max(0, int((next_claim_at - now).total_seconds())) if next_claim_at and not can_claim else 0,
        }

    async def claim_daily_bonus(self, user_id: int) -> Dict:
        """Claim the daily bonus once per 24 hours."""
        if not await self.get_user(user_id):
            return {"ok": False, "not_registered": True, "seconds_left": 0}

        status = await self.get_daily_bonus_status(user_id)
        if not status["can_claim"]:
            return {"ok": False, **status}

        now = datetime.now()
        async with self.conn.execute(
            """SELECT last_claimed, streak FROM user_reward_state
               WHERE user_id = ? AND reward_type = 'daily'""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        last_claimed = self._parse_timestamp(row["last_claimed"]) if row else None
        if last_claimed and now - last_claimed <= timedelta(hours=48):
            streak = (row["streak"] or 0) + 1
        else:
            streak = 1

        base_reward = Config.DAILY_BONUS_AMOUNT
        streak_bonus = (
            Config.DAILY_STREAK_BONUS_AMOUNT
            if Config.DAILY_STREAK_BONUS_DAYS > 0 and streak % Config.DAILY_STREAK_BONUS_DAYS == 0
            else 0
        )
        total_reward = base_reward + streak_bonus

        await self.conn.execute(
            "UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id = ?",
            (total_reward, total_reward, user_id)
        )
        await self.conn.execute(
            """INSERT INTO user_reward_state (user_id, reward_type, last_claimed, streak)
               VALUES (?, 'daily', ?, ?)
               ON CONFLICT(user_id, reward_type)
               DO UPDATE SET last_claimed = excluded.last_claimed, streak = excluded.streak""",
            (user_id, self._timestamp(now), streak)
        )
        await self.log_activity(
            user_id,
            "daily_bonus",
            f"Claimed {total_reward} stars (base {base_reward}, streak bonus {streak_bonus}, streak {streak})"
        )
        await self.conn.commit()

        user = await self.get_user(user_id)
        return {
            "ok": True,
            "reward": total_reward,
            "base_reward": base_reward,
            "streak_bonus": streak_bonus,
            "streak": streak,
            "balance": user["balance"] if user else 0,
        }

    async def get_box_game_status(self, user_id: int) -> Dict:
        """Return cooldown status for the box mini-game."""
        async with self.conn.execute(
            """SELECT completed_at FROM box_game_sessions
               WHERE user_id = ? AND status IN ('won', 'lost')
               ORDER BY completed_at DESC LIMIT 1""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        now = datetime.now()
        completed_at = self._parse_timestamp(row["completed_at"]) if row else None
        next_play_at = (
            completed_at + timedelta(minutes=Config.BOX_GAME_COOLDOWN_MINUTES)
            if completed_at else None
        )
        can_play = not next_play_at or now >= next_play_at

        return {
            "can_play": can_play,
            "next_play_at": next_play_at,
            "seconds_left": max(0, int((next_play_at - now).total_seconds())) if next_play_at and not can_play else 0,
        }

    async def start_box_game(self, user_id: int) -> Dict:
        """Create or return a pending star-box game session."""
        if not await self.get_user(user_id):
            return {"ok": False, "not_registered": True, "seconds_left": 0}

        status = await self.get_box_game_status(user_id)
        if not status["can_play"]:
            return {"ok": False, **status}

        await self.conn.execute(
            """UPDATE box_game_sessions
               SET status = 'expired', completed_at = CURRENT_TIMESTAMP
               WHERE user_id = ? AND status = 'pending'
                 AND created_at < datetime('now', '-15 minutes')""",
            (user_id,)
        )

        async with self.conn.execute(
            """SELECT id, reward FROM box_game_sessions
               WHERE user_id = ? AND status = 'pending'
               ORDER BY created_at DESC LIMIT 1""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"ok": True, "session_id": row["id"], "reward": row["reward"], "existing": True}

        winning_box = random.randint(1, 3)
        reward = Config.BOX_GAME_REWARD_AMOUNT
        cursor = await self.conn.execute(
            "INSERT INTO box_game_sessions (user_id, winning_box, reward) VALUES (?, ?, ?)",
            (user_id, winning_box, reward)
        )
        session_id = cursor.lastrowid
        await self.conn.commit()
        return {"ok": True, "session_id": session_id, "reward": reward, "existing": False}

    async def complete_box_game(self, user_id: int, session_id: int, selected_box: int) -> Dict:
        """Finish a pending box-game session and credit the reward on win."""
        async with self.conn.execute(
            """SELECT id, user_id, winning_box, reward, status FROM box_game_sessions
               WHERE id = ? AND user_id = ?""",
            (session_id, user_id)
        ) as cursor:
            session = await cursor.fetchone()

        if not session:
            return {"ok": False, "message": "Игра не найдена"}
        if session["status"] != "pending":
            return {"ok": False, "message": "Эта игра уже завершена"}

        won = selected_box == session["winning_box"]
        status = "won" if won else "lost"
        reward = session["reward"] if won else 0
        completed_at = self._timestamp(datetime.now())

        if won:
            await self.conn.execute(
                "UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id = ?",
                (session["reward"], session["reward"], user_id)
            )

        await self.conn.execute(
            """UPDATE box_game_sessions
               SET selected_box = ?, status = ?, completed_at = ?
               WHERE id = ?""",
            (selected_box, status, completed_at, session_id)
        )
        await self.log_activity(
            user_id,
            "box_game",
            f"Selected {selected_box}, winning {session['winning_box']}, reward {reward}"
        )
        await self.conn.commit()

        user = await self.get_user(user_id)
        return {
            "ok": True,
            "won": won,
            "reward": reward,
            "winning_box": session["winning_box"],
            "selected_box": selected_box,
            "balance": user["balance"] if user else 0,
        }

    # ============ PROMO CODES ============

    async def create_promo(self, code: str, reward: float, max_activations: int = 0) -> bool:
        """Create a promo code. max_activations=0 means unlimited. Returns False if code exists."""
        code = code.strip().upper()
        try:
            await self.conn.execute(
                "INSERT INTO promo_codes (code, reward, max_activations) VALUES (?, ?, ?)",
                (code, reward, max_activations)
            )
            await self.conn.commit()
            return True
        except Exception:
            return False

    async def get_promo(self, code: str) -> Optional[Dict]:
        """Get a promo code by its code (case-insensitive)."""
        async with self.conn.execute(
            "SELECT * FROM promo_codes WHERE code = ?",
            (code.strip().upper(),)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_promos(self) -> List[Dict]:
        """List all promo codes (newest first)."""
        async with self.conn.execute(
            "SELECT * FROM promo_codes ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def delete_promo(self, promo_id: int) -> bool:
        """Delete a promo code by id."""
        await self.conn.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
        await self.conn.commit()
        return True

    async def activate_promo(self, user_id: int, code: str) -> Tuple[bool, str, float]:
        """
        Activate a promo code for a user.
        Returns: (success, message, reward).
        """
        code = code.strip().upper()
        promo = await self.get_promo(code)
        if not promo or not promo['is_active']:
            return False, "❌ Промокод не найден или неактивен.", 0

        # Already used by this user?
        async with self.conn.execute(
            "SELECT 1 FROM promo_activations WHERE code = ? AND user_id = ?",
            (code, user_id)
        ) as cursor:
            if await cursor.fetchone():
                return False, "⚠️ Вы уже активировали этот промокод.", 0

        # Exhausted? (0 = unlimited)
        if promo['max_activations'] and promo['used_count'] >= promo['max_activations']:
            return False, "😔 Промокод исчерпан — лимит активаций закончился.", 0

        reward = promo['reward']
        await self.conn.execute(
            "UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id = ?",
            (reward, reward, user_id)
        )
        await self.conn.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 WHERE id = ?",
            (promo['id'],)
        )
        await self.conn.execute(
            "INSERT INTO promo_activations (code, user_id, reward) VALUES (?, ?, ?)",
            (code, user_id, reward)
        )
        await self.log_activity(user_id, "promo_activated", f"Activated promo {code} for {reward} stars")
        await self.conn.commit()
        return True, "ok", reward

    async def get_stats(self) -> Dict:
        """Get global statistics."""
        stats = {}

        # Total users
        async with self.conn.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            stats['total_users'] = row[0]

        # Total earned
        async with self.conn.execute("SELECT SUM(total_earned) FROM users") as cursor:
            row = await cursor.fetchone()
            stats['total_earned'] = row[0] or 0

        # Total withdrawn
        async with self.conn.execute("SELECT SUM(total_withdrawn) FROM users") as cursor:
            row = await cursor.fetchone()
            stats['total_withdrawn'] = row[0] or 0

        # Pending withdrawals
        async with self.conn.execute(
            "SELECT COUNT(*), SUM(amount) FROM withdrawals WHERE status = 'pending'"
        ) as cursor:
            row = await cursor.fetchone()
            stats['pending_withdrawals_count'] = row[0] or 0
            stats['pending_withdrawals_amount'] = row[1] or 0

        # Active users (last 7 days)
        async with self.conn.execute(
            """SELECT COUNT(*) FROM users
               WHERE last_active >= datetime('now', '-7 days')"""
        ) as cursor:
            row = await cursor.fetchone()
            stats['active_users_week'] = row[0]

        return stats

    async def log_activity(self, user_id: int, action: str, details: str):
        """Log user activity."""
        await self.conn.execute(
            "INSERT INTO activity_log (user_id, action, details) VALUES (?, ?, ?)",
            (user_id, action, details)
        )
        # Note: commit is done by the calling function
