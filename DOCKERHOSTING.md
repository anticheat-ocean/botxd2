# Deploy to DockerHosting.ru

This project is ready to run as one Docker Compose service: `botxd2`.

## What to upload

Use the prepared archive:

```text
D:\botxd2-dockerhosting-ready.zip
```

It intentionally does not include `.env`, `referral_bot.db`, `bot.log`, or `__pycache__`.

## Environment variables

In the DockerHosting.ru panel, add the variables from your local `D:\botxd2\.env`.
Required:

```text
BOT_TOKEN
ADMIN_ID
```

Recommended to copy too:

```text
ADMIN_CHANNEL_ID
REWARD_PER_REFERRAL
WITHDRAW_AMOUNTS
LOG_LEVEL
ANTITWINK_ENABLED
NEW_ACCOUNT_ID_THRESHOLD
TWINK_BLOCK_REFERRAL
BLOCK_ARABIC
PHONE_GATE_ENABLED
PIARFLOW_API_KEY
PIARFLOW_ENABLED
PIARFLOW_MAX_SPONSORS
```

`DATABASE_PATH` is already set in `docker-compose.yml` to `/data/referral_bot.db`.

## Panel steps

1. Open `https://dockerhosting.ru/my/index.php` and log in.
2. Create a Docker app/container/stack.
3. Choose Docker Compose if the panel asks for a deployment type.
4. Upload `D:\botxd2-dockerhosting-ready.zip`, or paste the contents of `docker-compose.yml` if the panel opens Portainer Stack editor.
5. Add the environment variables above.
6. Deploy.
7. Open logs and check that the bot prints `Bot started successfully`.

Do not run the same Telegram bot token on your PC and on the server at the same time. Stop the local `python bot.py` before the hosted container starts, otherwise Telegram polling can conflict.
