# Hardnested Bot
Telegram wrapper for [HardnestedRecovery](https://github.com/noproto/HardnestedRecovery) attacks on the go

## Setup

Using docker compose:

```yaml
services:
  hardnestedbot:
    image: ghcr.io/bernikr/hardnestedbot:0.3.0
    restart: always
    volumes:
      - data:/app/data
    environment:
      TELEGRAM_TOKEN: <your telegram token>
      WHITELISTED_CHAT_IDS: <your telegram chat id>
  
volumes:
  data:
```

## Config

All of this projects features are configured through environment variables:
| Variable | Default | Description |
| --- | --- | --- |
| `TELEGRAM_TOKEN` | *required* | Telegram bot token (can be obtained from [@BotFather](https://t.me/BotFather)) |
| `WHITELISTED_CHAT_IDS` | *required* | Comma separated list of chat ids to whitelist (You can use [@userinfobot](https://t.me/userinfobot) to get your chat id) |
| `WEBHOOK_URL` |  | Set URL to use webhooks instead of polling |
| `WEBHOOK_PORT` | 8080 | Port to run the webhook on |

## Usage

Send a `.nested.log` file to the bot to get started.
