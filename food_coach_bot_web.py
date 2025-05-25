"""
food_coach_bot_web.py
-----------------------------------------------
Telegram-бот-нутрициолог: фото блюда → название + КБЖУ на 100 г

• PTB v21, FastAPI web-hook
• OpenAI (gpt-4o) — ответ строго в JSON
• env-переменные: TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL, PORT
"""

import os
import io
import json
import base64
import logging
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from PIL import Image
from fastapi import FastAPI, Request
from telegram import Update, Bot, File
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ──────── ЛОГИ ───────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("food_bot")

# ──────── ENV ──────────────────────────────────────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")  # https://…
PORT           = int(os.getenv("PORT", 8000))
MAX_FILE_SIZE  = 10 * 1024 * 1024          # 10 МБ

if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL]):
    raise RuntimeError("Нужно TELEGRAM_TOKEN, OPENAI_API_KEY и WEBHOOK_URL")

# ──────── Клиенты ─────────────────────────────────────
bot = Bot(token=TELEGRAM_TOKEN)
application: Application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app = FastAPI()

import openai
openai_client = openai.AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    http_client=httpx.AsyncClient(timeout=30.0),
)

# ──────── OpenAI helper ─────────────────────────────────
async def analyse_image(img_b64: str) -> dict[str, Any]:
    resp = await openai_client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},  # строго объект
        temperature=0.2,
        max_tokens=200,
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты нутрициолог. Верни JSON-объект с ключами: "
                    "dish, calories, protein, fat, carbs. "
                    "Значения — на 100 г. Если не уверен — “—”."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img_b64}"
                        },
                    },
                    {"type": "text", "text": "Проанализируй блюдо на фото."},
                ],
            },
        ],
    )
    content = resp.choices[0].message.content
    # иногда библиотека возвращает строку, а иногда dict
    if isinstance(content, str):
        return json.loads(content)
    return content

# ──────── Handlers ─────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Пришли фото блюда — скажу название и КБЖУ на 100 г."
    )

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        photo = update.message.photo[-1]
        if photo.file_size and photo.file_size > MAX_FILE_SIZE:
            await update.message.reply_text("⚠️ Фото > 10 МБ, пришли поменьше.")
            return

        tg_file: File = await photo.get_file()
        buf = io.BytesIO()
        await tg_file.download_to_memory(out=buf)
        raw = buf.getvalue()

        img = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        await update.message.reply_text("🤖 Анализирую фото…")
        data = await analyse_image(img_b64)

        dish = data.get("dish", "—")
        cal  = data.get("calories", "—")
        p    = data.get("protein", "—")
        f    = data.get("fat", "—")
        c    = data.get("carbs", "—")

        await update.message.reply_text(
            f"🍽 {dish}\n"
            f"🔥 {cal} ккал / 100 г\n"
            f"🥩 {p} г   🥑 {f} г   🍞 {c} г"
        )

    except Exception:
        log.exception("Ошибка при обработке фото")
        await update.message.reply_text("⚠️ Не удалось обработать фото. Попробуй другое.")

# PTB wiring
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ──────── Webhook endpoint ─────────────────────────────
@app.post("/", status_code=200)
async def telegram_webhook(req: Request) -> dict:
    data = await req.json()
    if not getattr(application, "_initialized", False):
        await application.initialize()
    upd = Update.de_json(data, application.bot)
    await application.process_update(upd)
    return {"ok": True}

@app.get("/")
async def root() -> dict:
    return {"status": "alive"}

# ──────── FastAPI events ──────────────────────────────
@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    log.info("Webhook установлен: %s", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await application.shutdown()
    log.info("Webhook удалён, бот остановлен")

# локальный запуск
if __name__ == "__main__":
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
