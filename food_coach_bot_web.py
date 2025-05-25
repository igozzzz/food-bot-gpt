# food_coach_bot_web.py
"""
Telegram-бот-нутрициолог: присылаете фото блюда —
получаете название + КБЖУ на 100 г.

• PTB v21, FastAPI web-hook
• OpenAI (gpt-4o / gpt-4o-mini) — ответ строго в JSON
• Требуемые env-переменные: TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL, PORT
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
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ──────── логирование ───────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("food_bot")

# ──────── ENV ───────────────────────────────────────────────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")          # https://…/
PORT           = int(os.getenv("PORT", 8000))
MAX_FILE_SIZE  = 10 * 1024 * 1024                  # 10 МБ

if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL]):
    raise RuntimeError("Нужно задать TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL")

# ──────── внешние клиенты ───────────────────────────────────
bot = Bot(token=TELEGRAM_TOKEN)
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app = FastAPI()

import openai
openai_client = openai.AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    http_client=httpx.AsyncClient(timeout=30.0),
)

# ──────── OpenAI helper ─────────────────────────────────────
async def analyse_image(img_b64: str) -> dict[str, Any]:
    """Отправляем картинку, получаем dict c dish/calories/protein/fat/carbs."""
    resp = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=200,
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты нутрициолог. Верни JSON-объект с ключами: "
                    "dish, calories, protein, fat, carbs. "
                    "Значения — на 100 г. Если уверенности нет — ставь \"—\"."
                )
            },
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": "Проанализируй блюдо на фото."},
                ]
            },
        ],
    )
    return json.loads(resp.choices[0].message.content)

# ──────── Telegram-handlers ─────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Пришли фото блюда — скажу название и КБЖУ на 100 г."
    )
    log.info("User %s started bot", update.effective_user.id)

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    # Добавили отладочные логи
    photos = update.message.photo or []
    log.info("🔍 HANDLE_PHOTO: got %d photo sizes", len(photos))
    if not photos:
        await update.message.reply_text("⚠️ Я не нашёл фото в этом сообщении.")
        return

    photo = photos[-1]  # самое большое превью
    log.info("🔍 HANDLE_PHOTO: selected file_id=%s size=%d", photo.file_id, photo.file_size or 0)
    if photo.file_size and photo.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("⚠️ Фото > 10 МБ. Пришлите поменьше.")
        return

    try:
        tg_file: File = await photo.get_file()
        buf = io.BytesIO()
        # PTB v21: download_to_memory требует аргумент out
        await tg_file.download_to_memory(out=buf)
        raw = buf.getvalue()
        log.info("🔍 HANDLE_PHOTO: downloaded %d bytes", len(raw))

        # Конвертация в JPEG и base64
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        await update.message.reply_text("🤖 Анализирую фото…")
        log.info("🔍 HANDLE_PHOTO: calling analyse_image()")
        data = await analyse_image(img_b64)
        log.info("🔍 HANDLE_PHOTO: analyse_image returned %r", data)

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
    except Exception as e:
        log.error("Handle photo error: %s", e, exc_info=True)
        await update.message.reply_text("⚠️ Не удалось обработать фото. Попробуйте другое.")

# ──────── PTB wiring ────────────────────────────────────────
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ──────── FastAPI webhook ───────────────────────────────────
@app.post("/", status_code=200)
async def telegram_webhook(req: Request) -> dict:
    data = await req.json()
    if not getattr(application, "_initialized", False):
        await application.initialize()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app.get("/")
async def root() -> dict:
    return {"status": "alive"}

# ──────── FastAPI events ───────────────────────────────────
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

# ──────── локальный запуск ─────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
