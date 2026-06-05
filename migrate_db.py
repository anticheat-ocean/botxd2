"""Database migration script to add new columns to existing database."""
import sqlite3
import sys

def migrate_database(db_path='referral_bot.db'):
    """Migrate old database schema to new schema."""
    print(f"Starting migration for {db_path}...")

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Check if users table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cur.fetchone():
            print("No existing database found. New database will be created on bot start.")
            conn.close()
            return True

        # Get current columns
        cur.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cur.fetchall()]
        print(f"Current columns: {columns}")

        # Add missing columns
        migrations = []

        if 'first_name' not in columns:
            migrations.append("ALTER TABLE users ADD COLUMN first_name TEXT")
            print("  - Adding first_name column")

        if 'total_withdrawn' not in columns:
            migrations.append("ALTER TABLE users ADD COLUMN total_withdrawn INTEGER DEFAULT 0")
            print("  - Adding total_withdrawn column")

        if 'created_at' not in columns:
            migrations.append("ALTER TABLE users ADD COLUMN created_at TIMESTAMP")
            print("  - Adding created_at column")

        if 'last_active' not in columns:
            migrations.append("ALTER TABLE users ADD COLUMN last_active TIMESTAMP")
            print("  - Adding last_active column")

        # Execute migrations
        for migration in migrations:
            try:
                cur.execute(migration)
                print(f"  [OK] Executed: {migration}")
            except Exception as e:
                print(f"  [FAIL] Failed: {migration} - {e}")

        # Check withdrawals table
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='withdrawals'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info(withdrawals)")
            withdrawal_columns = [row[1] for row in cur.fetchall()]

            if 'processed_at' not in withdrawal_columns:
                cur.execute("ALTER TABLE withdrawals ADD COLUMN processed_at TIMESTAMP")
                print("  [OK] Added processed_at to withdrawals")

            if 'admin_note' not in withdrawal_columns:
                cur.execute("ALTER TABLE withdrawals ADD COLUMN admin_note TEXT")
                print("  [OK] Added admin_note to withdrawals")

        conn.commit()
        print("\n[SUCCESS] Migration completed successfully!")

        # Show stats
        cur.execute("SELECT COUNT(*) FROM users")
        user_count = cur.fetchone()[0]
        print(f"\nDatabase stats:")
        print(f"  - Total users: {user_count}")

        conn.close()
        return True

    except Exception as e:
        print(f"\n[ERROR] Migration failed: {e}")
        return False

if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else 'referral_bot.db'
    success = migrate_database(db_path)
    sys.exit(0 if success else 1)
