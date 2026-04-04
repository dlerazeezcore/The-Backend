# Telegram Support Bridge

Self-contained FastAPI module for in-app customer support with Telegram as the support-agent surface.

This folder is intentionally standalone so it can be prepared without editing other backend folders or frontend API contracts.

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
- `settings.py`: env-first runtime configuration
- `supabase_repo.py`: direct Supabase REST access for support tables
- `schemas.py`: request/response models
- `supabase_support_chat.sql`: SQL to create the needed tables
- `figma_ui_prompts.md`: prompt pack for the Figma support UI

## Standalone Run

From the repo root:

```bash
uvicorn backend.communications.Telegram.app:app --host 0.0.0.0 --port 5090
```

## Required Environment Variables

The module is env-first. You can copy `backend/communications/Telegram/.env.example` and set:

- `TELEGRAM_SUPPORT_BOT_TOKEN`
- `TELEGRAM_SUPPORT_CHAT_ID`
- `TELEGRAM_SUPPORT_WEBHOOK_SECRET`
- `TELEGRAM_SUPPORT_PUBLIC_BASE_URL`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Optional:

- `TELEGRAM_SUPPORT_MESSAGE_THREAD_ID`
- `TELEGRAM_SUPPORT_TIMEOUT_SECONDS`
- `TELEGRAM_SUPPORT_WEBHOOK_SYNC_ON_STARTUP`
- `TELEGRAM_SUPPORT_ALLOWED_UPDATES`
- `SUPABASE_TIMEOUT_SECONDS`
- `TELEGRAM_SUPPORT_ATTACHMENTS_BUCKET`
- `TELEGRAM_SUPPORT_CONVERSATIONS_TABLE`
- `TELEGRAM_SUPPORT_MESSAGES_TABLE`
- `TELEGRAM_SUPPORT_MAP_TABLE`

Backwards-compatible legacy env names are still accepted, but the `TELEGRAM_SUPPORT_*` names are the preferred standard for future bots.

## API Endpoints

- `GET /health`
- `GET /__build`
- `GET /api/telegram-support/conversation`
- `POST /api/telegram-support/messages`
- `POST /api/telegram-support/webhook`
- `GET /api/telegram-support/admin/webhook`
- `POST /api/telegram-support/admin/webhook/register`
- `POST /api/telegram-support/admin/webhook/ensure`

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
4. Set `TELEGRAM_SUPPORT_PUBLIC_BASE_URL` to your public backend URL.
5. Set webhook manually once, or let backend startup sync do it:

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
- Existing frontend routes are unchanged; the cleanup only affects backend internals and configuration.
