import asyncio
import re
import secrets
import sqlite3
import string
from pathlib import Path

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from config import BOT_TOKEN, API_KEY, ADMIN_ID

API_URL = "https://tubesave.in/api/v1/check"
ADMIN_USERNAME = "Amazing2cu"
DB_PATH = Path(__file__).with_name("vikram_checker.db")

SERVICES = [
    ("Gosats", "gosats"),
    ("Bigbasket", "bigbasket"),
    ("Meesho", "meesho"),
    ("Plutos", "plutos"),
    ("Starexch", "starexch"),
    ("Swiggy", "swiggy"),
    ("Flipkart", "flipkart"),
    ("Shein", "shein"),
    ("Oyo", "oyo"),
    ("Mantrimall", "mantrimall"),
    ("Blinkit", "blinkit"),
    ("Brevistay", "brevistay"),
    ("Ajio", "ajio"),
    ("Amazon", "amazon"),
    ("MyJio", None),
    ("CrownIt", "crownit"),
    ("HabitYog", None),
    ("Lenskart", "lenskart"),
    ("Operator", "operator"),
    ("Myntra", "myntra"),
]
SERVICE_NAMES = {slug: name for name, slug in SERVICES if slug}

# Conversation state is temporary; balances and codes are stored in SQLite.
user_state = {}
user_locks = {}


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                credits INTEGER NOT NULL DEFAULT 0 CHECK (credits >= 0),
                joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Safe migration for existing databases created by earlier bot versions.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "username" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER NOT NULL,
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # The configured owner always remains an administrator.
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
            (ADMIN_ID, ADMIN_ID),
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS redeem_codes (
                code TEXT PRIMARY KEY,
                credits INTEGER NOT NULL CHECK (credits > 0),
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                redeemed_by INTEGER,
                redeemed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS check_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def ensure_user(user_id, username=None):
    """Create/update a user record; username is saved without the @ prefix."""
    clean_username = username.lstrip("@") if username else None
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, clean_username),
        )
        if clean_username:
            conn.execute(
                "UPDATE users SET username = ? WHERE user_id = ?",
                (clean_username, user_id),
            )


def find_user_id(user_reference):
    """Resolve a numeric Telegram ID or a username already known to this bot."""
    value = user_reference.strip()
    if value.startswith("@"):
        username = value[1:].casefold()
        if not username:
            return None
        with db() as conn:
            row = conn.execute(
                "SELECT user_id FROM users WHERE username = ? COLLATE NOCASE LIMIT 1",
                (username,),
            ).fetchone()
        return int(row["user_id"]) if row else None
    if value.isdigit():
        return int(value)
    return None


def get_credits(user_id):
    ensure_user(user_id)
    with db() as conn:
        row = conn.execute(
            "SELECT credits FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return int(row["credits"])


def is_owner(user_id):
    return ADMIN_ID > 0 and user_id == ADMIN_ID


def is_admin(user_id):
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    with db() as conn:
        return conn.execute(
            "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
        ).fetchone() is not None


def main_menu():
    buttons = []
    for name, slug in SERVICES:
        callback = f"service:{slug}" if slug else f"maintenance:{name}"
        buttons.append(InlineKeyboardButton(text=name, callback_data=callback))

    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    rows.append([
        InlineKeyboardButton(text="💰 Buy Credits", callback_data="buy"),
        InlineKeyboardButton(text="🎟️ Redeem Code", callback_data="redeem"),
    ])
    rows.append([
        InlineKeyboardButton(text="👤 My Profile", callback_data="profile"),
        InlineKeyboardButton(text="💳 My Balance", callback_data="my_balance"),
    ])
    rows.append([
        InlineKeyboardButton(text="🔗 Developer API", callback_data="developer"),
        InlineKeyboardButton(text="⭐ Upgrade Plans", callback_data="upgrade"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")
    ]])


def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Main Menu", callback_data="main_menu")
    ]])


def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Main Menu", callback_data="main_menu")
    ]])


def generate_code():
    alphabet = string.ascii_uppercase + string.digits
    parts = [
        "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(3)
    ]
    return "VK-" + "-".join(parts)


def create_code(credits, admin_id):
    if not 1 <= credits <= 100000:
        raise ValueError("Credits must be between 1 and 100000.")
    for _ in range(10):
        code = generate_code()
        try:
            with db() as conn:
                conn.execute(
                    "INSERT INTO redeem_codes (code, credits, created_by) VALUES (?, ?, ?)",
                    (code, credits, admin_id),
                )
            return code
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("Could not create a unique code. Please try again.")


def redeem_code(user_id, raw_code):
    code = raw_code.strip().upper()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT credits, redeemed_by FROM redeem_codes WHERE code = ?",
            (code,),
        ).fetchone()

        if row is None:
            return None, "Invalid redeem code."
        if row["redeemed_by"] is not None:
            return None, "This redeem code has already been used."

        conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,),
        )
        conn.execute(
            "UPDATE users SET credits = credits + ? WHERE user_id = ?",
            (row["credits"], user_id),
        )
        updated = conn.execute(
            "UPDATE redeem_codes SET redeemed_by = ?, redeemed_at = CURRENT_TIMESTAMP "
            "WHERE code = ? AND redeemed_by IS NULL",
            (user_id, code),
        )
        if updated.rowcount != 1:
            return None, "This redeem code has already been used."
        return int(row["credits"]), None


async def call_api(number, slug):
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(
            API_URL,
            headers={"X-API-Key": API_KEY},
            json={"number": number, "service": slug},
        )

    if response.status_code == 429:
        return None, "Too many requests. Try again later."
    if response.status_code in (401, 403):
        return None, "API authorization failed. Check your API key."
    if response.status_code == 503:
        return None, "The checking service is temporarily unavailable."
    if response.status_code != 200:
        return None, f"Checking failed (HTTP {response.status_code})."

    try:
        data = response.json()
    except ValueError:
        return None, "Invalid response from the checking service."

    if data.get("success") is not True:
        return None, "The check was unsuccessful."
    if not isinstance(data.get("in_database"), bool):
        return None, "No valid database result was returned."
    if data.get("is_down") is True:
        return None, "This service is under maintenance. No credit was used."
    if data.get("is_registered") not in (True, False, None):
        return None, "The service returned an invalid status."

    return data, None


async def main():
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_BOT_TOKEN_HERE":
        raise RuntimeError("Add your Telegram bot token in config.py.")
    if not API_KEY or API_KEY == "PASTE_API_KEY_HERE":
        raise RuntimeError("Add your API key in config.py.")
    if not isinstance(ADMIN_ID, int) or ADMIN_ID <= 0:
        raise RuntimeError("Set ADMIN_ID to your numeric Telegram user ID in config.py.")

    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start", description="Open the main menu"),
        BotCommand(command="menu", description="Show the main menu"),
        BotCommand(command="generate", description="Admin: generate a redeem code"),
        BotCommand(command="addcredits", description="Admin: add credits by ID or known username"),
        BotCommand(command="balance", description="Admin: check a user balance by ID or username"),
        BotCommand(command="codes", description="Admin: list recent redeem codes"),
        BotCommand(command="users", description="Admin: count registered users"),
        BotCommand(command="addadmin", description="Owner: add an admin by ID or saved username"),
        BotCommand(command="removeadmin", description="Owner: remove an admin by ID or saved username"),
        BotCommand(command="admins", description="Admin: list administrators"),
    ])

    @dp.message(CommandStart())
    async def start(message: Message):
        uid = message.from_user.id
        ensure_user(uid, message.from_user.username)
        user_state.pop(uid, None)
        await message.answer(
            "👋 Welcome to Vikram Checker!\n\nSelect a checker below.",
            reply_markup=main_menu(),
        )

    @dp.message(Command("menu"))
    async def menu(message: Message):
        ensure_user(message.from_user.id, message.from_user.username)
        user_state.pop(message.from_user.id, None)
        await message.answer("Select an option:", reply_markup=main_menu())

    @dp.callback_query(F.data.startswith("service:"))
    async def select_service(callback: CallbackQuery):
        slug = callback.data.split(":", 1)[1]
        if slug not in SERVICE_NAMES:
            await callback.answer("Unknown service.", show_alert=True)
            return
        uid = callback.from_user.id
        ensure_user(uid, callback.from_user.username)
        user_state[uid] = {"mode": "check", "service": slug}
        await callback.answer()
        await callback.message.answer(
            f"Selected: {SERVICE_NAMES[slug]}\n"
            f"Available credits: {get_credits(uid)}\n\n"
            "Enter an authorized 10-digit mobile number.",
            reply_markup=cancel_menu(),
        )

    @dp.callback_query(F.data.startswith("maintenance:"))
    async def unavailable_service(callback: CallbackQuery):
        name = callback.data.split(":", 1)[1]
        await callback.answer()
        await callback.message.answer(
            f"🙂 {name} is still under maintenance.\nPlease try again later."
        )

    @dp.callback_query(F.data == "buy")
    async def buy_credits(callback: CallbackQuery):
        await callback.answer()
        plans = [
            (100, 49),
            (250, 99),
            (500, 179),
            (1000, 299),
        ]
        rows = []
        for credits, price in plans:
            message_text = f"Hi! I want to buy the {credits} credits plan for INR {price}. Please share payment instructions."
            contact_url = f"https://t.me/{ADMIN_USERNAME}?text=" + __import__("urllib.parse", fromlist=["quote"]).quote(message_text)
            rows.append([InlineKeyboardButton(
                text=f"💳 {credits} Credits — ₹{price}",
                url=contact_url,
            )])
        rows.extend([
            [InlineKeyboardButton(text="🎟️ Redeem Code", callback_data="redeem")],
            [InlineKeyboardButton(text="⬅️ Main Menu", callback_data="main_menu")],
        ])
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        await callback.message.answer(
            "💰 Buy Credits — Plans\n\n"
            "Choose a plan below to message @Amazing2cu with your selected package.\n\n"
            "💠 100 credits — ₹49\n"
            "💠 250 credits — ₹99\n"
            "💠 500 credits — ₹179\n"
            "💠 1,000 credits — ₹299\n\n"
            "Payment is arranged manually with the admin. After payment is confirmed, "
            "the admin will send you a redeem code. Redeem it in this bot to add credits.\n\n"
            "Never share your Telegram login code or password.",
            reply_markup=keyboard,
        )

    @dp.callback_query(F.data == "redeem")
    async def redeem_start(callback: CallbackQuery):
        user_state[callback.from_user.id] = {"mode": "redeem"}
        await callback.answer()
        await callback.message.answer(
            "🎟️ Send your redeem code:",
            reply_markup=cancel_menu(),
        )

    @dp.callback_query(F.data == "profile")
    async def profile(callback: CallbackQuery):
        uid = callback.from_user.id
        ensure_user(uid, callback.from_user.username)
        credits = get_credits(uid)
        with db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM check_log WHERE user_id = ?",
                (uid,),
            ).fetchone()["n"]
        await callback.answer()
        await callback.message.edit_text(
            "👤 Your Profile\n\n"
            f"Telegram ID: {uid}\n"
            f"Available credits: {credits}\n"
            f"Successful checks: {total}",
            reply_markup=back_menu(),
        )

    @dp.callback_query(F.data == "my_balance")
    async def my_balance(callback: CallbackQuery):
        uid = callback.from_user.id
        ensure_user(uid, callback.from_user.username)
        await callback.answer()
        await callback.message.edit_text(
            f"💳 Your Balance\n\nAvailable credits: {get_credits(uid)}",
            reply_markup=back_menu(),
        )

    @dp.callback_query(F.data.in_({"developer", "upgrade"}))
    async def placeholders(callback: CallbackQuery):
        await callback.answer()
        label = "Developer API" if callback.data == "developer" else "Upgrade Plans"
        await callback.message.answer(
            f"🙂 {label} is still under maintenance."
        )

    @dp.callback_query(F.data == "main_menu")
    async def back_to_menu(callback: CallbackQuery):
        user_state.pop(callback.from_user.id, None)
        await callback.answer()
        await callback.message.answer("Main menu:", reply_markup=main_menu())

    @dp.callback_query(F.data == "cancel")
    async def cancel(callback: CallbackQuery):
        user_state.pop(callback.from_user.id, None)
        await callback.answer("Cancelled")
        await callback.message.answer("Cancelled.", reply_markup=main_menu())

    @dp.callback_query(F.data == "admin_help")
    async def admin_help(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("Not authorized.", show_alert=True)
            return
        await callback.answer()
        await callback.message.answer(
            "🛠️ Admin Commands\n\n"
            "/generate 100 — create a 100-credit code\n"
            "/addcredits USER_ID_OR_@username 100 — add credits\n"
            "/balance USER_ID_OR_@username — view balance\n"
            "/codes — list recent codes and status\n"
            "/users — count registered users\n"
            "/admins — list administrators\n"
            "/addadmin USER_ID_OR_@username — add admin (owner only)\n"
            "/removeadmin USER_ID_OR_@username — remove admin (owner only)\n\n"
            "For username lookup, the user must have pressed /start in this bot."
        )

    @dp.message(Command("generate"))
    async def generate_command(message: Message):
        if not is_admin(message.from_user.id):
            await message.answer("⛔ Admin only.")
            return
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Usage: /generate 100")
            return
        try:
            code = create_code(int(parts[1]), message.from_user.id)
        except (ValueError, RuntimeError) as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            "✅ Redeem code created\n\n"
            f"Code: `{code}`\n\n"
            "Send it privately to the intended buyer.",
            parse_mode="Markdown",
        )

    @dp.message(Command("addcredits"))
    async def add_credits_command(message: Message):
        if not is_admin(message.from_user.id):
            await message.answer("⛔ Admin only.")
            return
        parts = (message.text or "").split()
        if len(parts) != 3 or not parts[2].isdigit():
            await message.answer("Usage: /addcredits USER_ID 100 or /addcredits @username 100\n\nFor usernames, the user must have pressed /start in this bot so their username is saved.")
            return
        uid = find_user_id(parts[1])
        if uid is None:
            await message.answer("❌ Username not found in this bot's saved users. Ask that person to open the bot and press /start, then try again. Numeric Telegram IDs also work.")
            return
        amount = int(parts[2])
        if amount < 1 or amount > 100000:
            await message.answer("Amount must be between 1 and 100000.")
            return
        ensure_user(uid)
        with db() as conn:
            conn.execute(
                "UPDATE users SET credits = credits + ? WHERE user_id = ?",
                (amount, uid),
            )
        await message.answer(f"✅ Added {amount} credits to {parts[1]} (ID: {uid}).")

    @dp.message(Command("balance"))
    async def balance_command(message: Message):
        if not is_admin(message.from_user.id):
            await message.answer("⛔ Admin only.")
            return
        parts = (message.text or "").split()
        if len(parts) != 2:
            await message.answer("Usage: /balance USER_ID or /balance @username")
            return
        uid = find_user_id(parts[1])
        if uid is None:
            await message.answer("❌ Username not found in this bot's saved users. Ask that person to open the bot and press /start, then try again.")
            return
        await message.answer(f"User {parts[1]} (ID: {uid}): {get_credits(uid)} credits.")

    @dp.message(Command("addadmin"))
    async def add_admin_command(message: Message):
        if not is_owner(message.from_user.id):
            await message.answer("⛔ Only the bot owner configured in config.py can add or remove administrators.")
            return
        parts = (message.text or "").split()
        if len(parts) != 2:
            await message.answer("Usage: /addadmin USER_ID or /addadmin @username\nFor usernames, the person must have pressed /start in this bot first.")
            return
        target_id = find_user_id(parts[1])
        if target_id is None:
            await message.answer("❌ User not found. Ask them to press /start first, or use their numeric Telegram ID.")
            return
        with db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
                (target_id, message.from_user.id),
            )
        await message.answer(f"✅ Admin access granted to {parts[1]} (ID: {target_id}). They should send /start to refresh their bot menu if needed.")

    @dp.message(Command("removeadmin"))
    async def remove_admin_command(message: Message):
        if not is_owner(message.from_user.id):
            await message.answer("⛔ Only the bot owner configured in config.py can add or remove administrators.")
            return
        parts = (message.text or "").split()
        if len(parts) != 2:
            await message.answer("Usage: /removeadmin USER_ID or /removeadmin @username")
            return
        target_id = find_user_id(parts[1])
        if target_id is None:
            await message.answer("❌ User not found. Use their numeric Telegram ID, or ask them to press /start so their username is saved.")
            return
        if target_id == ADMIN_ID:
            await message.answer("⛔ The primary owner in config.py cannot be removed.")
            return
        with db() as conn:
            cur = conn.execute("DELETE FROM admins WHERE user_id = ?", (target_id,))
        if cur.rowcount:
            await message.answer(f"✅ Admin access removed from {parts[1]} (ID: {target_id}).")
        else:
            await message.answer(f"ℹ️ {parts[1]} (ID: {target_id}) is not currently an admin.")

    @dp.message(Command("admins"))
    async def list_admins_command(message: Message):
        if not is_admin(message.from_user.id):
            await message.answer("⛔ Admin only.")
            return
        with db() as conn:
            rows = conn.execute(
                "SELECT a.user_id, u.username FROM admins a LEFT JOIN users u ON u.user_id = a.user_id ORDER BY a.user_id"
            ).fetchall()
        lines = ["🛡️ Administrators:"]
        for row in rows:
            label = f"@{row['username']}" if row['username'] else "username not saved"
            owner_tag = " (primary owner)" if row['user_id'] == ADMIN_ID else ""
            lines.append(f"• {label} — ID: {row['user_id']}{owner_tag}")
        await message.answer("\n".join(lines))

    @dp.message(Command("codes"))
    async def codes_command(message: Message):
        if not is_admin(message.from_user.id):
            await message.answer("⛔ Admin only.")
            return
        with db() as conn:
            rows = conn.execute(
                "SELECT code, credits, redeemed_by FROM redeem_codes "
                "ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        if not rows:
            await message.answer("No redeem codes created yet.")
            return
        lines = ["Recent redeem codes:"]
        for row in rows:
            status = (
                f"Used by {row['redeemed_by']}"
                if row["redeemed_by"] is not None
                else "Unused"
            )
            lines.append(f"{row['code']} — {row['credits']} credits — {status}")
        await message.answer("\n".join(lines))

    @dp.message(Command("users"))
    async def users_command(message: Message):
        if not is_admin(message.from_user.id):
            await message.answer("⛔ Admin only.")
            return
        with db() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        await message.answer(f"Registered users: {count}")

    @dp.message()
    async def handle_text(message: Message):
        if not message.text:
            return
        uid = message.from_user.id
        ensure_user(uid, message.from_user.username)
        state = user_state.get(uid)
        if not state:
            await message.answer(
                "Please select an option using /start.",
                reply_markup=main_menu(),
            )
            return

        if state["mode"] == "redeem":
            amount, error = redeem_code(uid, message.text)
            if error:
                await message.answer(f"❌ {error}", reply_markup=cancel_menu())
                return
            user_state.pop(uid, None)
            await message.answer(
                f"✅ Code redeemed!\nCredits added: {amount}\n"
                f"Available credits: {get_credits(uid)}",
                reply_markup=main_menu(),
            )
            return

        if state["mode"] != "check":
            return

        lock = user_locks.setdefault(uid, asyncio.Lock())
        async with lock:
            if get_credits(uid) < 1:
                await message.answer(
                    "⚠️ You have no credits left.\nChoose Buy Credits to contact the admin.",
                    reply_markup=main_menu(),
                )
                return

            number = re.sub(r"[\s-]", "", message.text.strip())
            if number.startswith("+91"):
                number = number[3:]
            elif number.startswith("91") and len(number) == 12:
                number = number[2:]

            if not re.fullmatch(r"[0-9]{10}", number):
                await message.answer(
                    "❌ Enter a valid 10-digit mobile number.",
                    reply_markup=cancel_menu(),
                )
                return

            slug = state["service"]
            name = SERVICE_NAMES[slug]
            waiting = await message.answer("🔎 Checking, please wait...")

            try:
                data, error = await call_api(number, slug)
                if error:
                    await waiting.edit_text(
                        f"⚠️ {error}\nNo credit was deducted.",
                        reply_markup=cancel_menu(),
                    )
                    return

                # Deduct one credit in a DB transaction only after valid result.
                with db() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT credits FROM users WHERE user_id = ?",
                        (uid,),
                    ).fetchone()
                    if row is None or row["credits"] < 1:
                        await waiting.edit_text("You have no credits remaining.")
                        return
                    conn.execute(
                        "UPDATE users SET credits = credits - 1 WHERE user_id = ?",
                        (uid,),
                    )
                    conn.execute(
                        "INSERT INTO check_log (user_id, service) VALUES (?, ?)",
                        (uid, slug),
                    )

                registered = data.get("is_registered")
                if registered is True:
                    status = "✅ Registered"
                elif registered is False:
                    status = "❌ Not Registered ⚡"
                else:
                    status = "Unknown"

                await waiting.edit_text(
                    f"{name} 👇\n\n"
                    f"Status: {status}\n"
                    f"Database: {'Yes' if data['in_database'] else 'No'}\n\n"
                    f"Credits remaining: {get_credits(uid)}",
                    reply_markup=cancel_menu(),
                )

            except httpx.TimeoutException:
                await waiting.edit_text("⏱️ Request timed out. No credit was deducted.")
            except httpx.RequestError:
                await waiting.edit_text("🌐 Connection error. No credit was deducted.")
            except Exception:
                await waiting.edit_text("⚠️ Unexpected error. Please try again.")

    print("Vikram Checker is running!")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
