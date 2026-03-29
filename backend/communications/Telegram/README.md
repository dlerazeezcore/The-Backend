# Telegram Support Bridge

Self-contained FastAPI module for in-app customer support with Telegram as the support-agent surface.

This folder is intentionally standalone so it can be prepared without editing any other backend folders.

## What It Does

- Accepts customer support messages from the mobile app
- Stores conversations and messages in Supabase
- Forwards customer messages to a private Telegram support chat
- Accepts Telegram replies through a webhook
- Writes staff replies back to the same in-app conversation

## Files

- `app.py`: standalone FastAPI app entrypoint
- `router.py`: API routes
- `service.py`: Telegram send + webhook handling
- `supabase_repo.py`: direct Supabase REST access for support tables
- `schemas.py`: request/response models
- `supabase_support_chat.sql`: SQL to create the needed tables
- `figma_ui_prompts.md`: prompt pack for the Figma support UI
- `config.json`: file-based runtime configuration

## Standalone Run

From the repo root:

```bash
uvicorn backend.communications.Telegram.app:app --host 0.0.0.0 --port 5090
```

## Required Configuration

Edit `config.json` and fill:

- `telegram_bot_token`
- `telegram_support_chat_id`
- `telegram_webhook_secret`
- `supabase_service_role_key`

Optional:

- `telegram_support_message_thread_id`
- `support_attachments_bucket`
- `support_conversations_table`
- `support_messages_table`
- `support_telegram_map_table`

## API Endpoints

- `GET /health`
- `GET /__build`
- `GET /api/telegram-support/conversation`
- `POST /api/telegram-support/messages`
- `POST /api/telegram-support/webhook`

## Frontend Contract

### Send customer message

`POST /api/telegram-support/messages`

```json
{
  "body": "Hello, I need help with my order"
}
```

Or send `multipart/form-data` with:

- `body`: optional text
- `file`: optional image attachment

Requires the same bearer auth pattern already used by the backend.

### Load conversation

`GET /api/telegram-support/conversation`

Returns:

```json
{
  "conversation": {},
  "messages": []
}
```

## Telegram Setup

1. Create a Telegram bot with BotFather.
2. Add the bot to your private support group.
3. Make the bot an admin if needed.
4. Set webhook:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-backend-domain/api/telegram-support/webhook",
    "secret_token": "your-secret-token"
  }'
```

## Notes

- Customer messages support text-only, image-only, or text plus one image attachment.
- Staff replies must be sent as Telegram replies to the forwarded customer message.
- The app remains fully in-app; customers are never redirected to Telegram.
- This module is also wired into the main backend gateway in this repo.
