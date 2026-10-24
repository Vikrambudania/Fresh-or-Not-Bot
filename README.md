# Vikram Checker (starter)

## Files
- `bot.py`: Telegram bot and SQLite credit/redeem-code logic
- `config.py`: local secrets and numeric admin Telegram ID
- `.gitignore`: excludes secrets and local database from Git

## Setup (Ubuntu)
```bash
cd ~/vikram-checker
python3 -m venv .venv
source .venv/bin/activate
pip install aiogram httpx
```

Edit `config.py` and set:
- `BOT_TOKEN`: token from BotFather
- `API_KEY`: your authorized API key
- `ADMIN_ID`: your numeric Telegram user ID

Then run:
```bash
python bot.py
```

The SQLite database `vikram_checker.db` is created automatically next to `bot.py`.

## Admin commands
- `/generate 100` — create a single-use code worth 100 credits
- `/addcredits USER_ID 100` — add credits to a user
- `/balance USER_ID` — view a user's balance
- `/codes` — list the 20 most recent codes and their status
- `/users` — count registered users

`Buy Credits` opens https://t.me/Amazing2cu. Payment is handled manually by the admin; payment is not verified by the bot.

## Important
- Use only for authorized, consent-based checks and ensure your API provider permits the use case.
- Keep `config.py` private; do not commit it to Git.
- Back up the SQLite database securely.
- This is a starter bot, not a fully audited payment/credit system.
- MyJio and HabitYog remain placeholders because no supported API slugs were supplied for them.
