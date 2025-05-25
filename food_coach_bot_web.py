"""
Простой Telegram-бот: пришлите фото еды — получите название блюда + КБЖУ на 100 г.
Работает через Webhook (FastAPI → Render/Vercel/Fly и т.п.).

⚙️  Зависимости (requirements.txt)
---------------------------------
fastapi
uvicorn
python-telegram-bot==21.4
openai>=1.2.0           # обязателен ≥1.2 для response_format
httpx
pillow
python-dotenv
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
from fastapi import FastAPI, Request, HTTPException
from PIL import Image
from telegram import Update, Bot
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler,
    MessageHandler, ContextTypes, filters,
)
from openai import AsyncOpenAI

# ---------- базовая настройка ------------------------------------------------------------------
load_dotenv()  # берём TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL, PORT из .env
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")      # https://…/  (HTTPS обязателен!)
PORT           = int(os.getenv("PORT", 8000))  # Render передаёт PORT env

if not all((TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL)):
    raise RuntimeError("Не заданы TELEGRAM_TOKEN / OPENAI_API_KEY / WEBHOOK_URL")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("food_bot")

# ---------- Telegram & OpenAI ------------------------------------------------------------------
bot         = Bot(token=TELEGRAM_TOKEN)
application: Application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
openai_cli  = AsyncOpenAI(api_key=OPENAI_API_KEY)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 МБ

# ---------- Telegram-handlers ------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Приветствие"""
    await update.message.reply_text(
        "👋 Привет! Я — бот-нутрициолог.\n\n"
        "📸 Пришлите фото блюда, а я назову его и дам КБЖУ (на 100 г)."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not update.message or not update.message.photo:
            return

        tg_photo = update.message.photo[-1]
        if tg_photo.file_size and tg_photo.file_size > MAX_FILE_SIZE:
            await update.message.reply_text("⚠️ Фото слишком большое (максимум 10 МБ).")
            return

        file = await tg_photo.get_file()
        raw: bytes = await file.download_as_bytes()          # PTB v21

        # ---> base64-кодируем для image_url
        with Image.open(io.BytesIO(raw)).convert("RGB") as img:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            img_b64 = base64.b64encode(buf.getvalue()).decode()

        await update.message.reply_text("🤖 Анализирую фото…")

        # ---> запрос к OpenAI (строго JSON)
        response = await openai_cli.chat.completions.create(
            model="gpt-4o-mini",          # или gpt-4o, gpt-4o-audio-preview
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты нутрициолог. ВЕРНИ ТОЛЬКО JSON без форматирования "
                        "с полями: dish, calories, protein, fat, carbs. "
                        "calories/бжк — на 100 г. Если не уверен — ставь \"—\"."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text",
                         "text": "Что это за блюдо и КБЖУ на 100 г?"},
                    ],
                },
            ],
            temperature=0.2,
            max_tokens=200,
        )

        reply = response.choices[0].message.content.strip()
        data: dict[str, Any] = json.loads(reply)  # гарантированно валидный JSON

        await update.message.reply_text(
            f"🍽 Блюдо: {data.get('dish','—')}\n"
            f"🔥 Калории: {data.get('calories','—')} ккал / 100 г\n"
            f"🥩 Белки: {data.get('protein','—')} г\n"
            f"🥑 Жиры: {data.get('fat','—')} г\n"
            f"🍞 Углеводы: {data.get('carbs','—')} г"
        )

    except Exception as e:
        log.error("Handle photo error: %s", e, exc_info=True)
        await update.message.reply_text("⚠️ Не удалось распознать. Попробуйте другое фото.")

# регистрируем
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ---------- FastAPI (Webhook) ------------------------------------------------------------------
app = FastAPI()

@app.post("/", status_code=200)
async def telegram_webhook(req: Request):
    """Получаем update от Telegram → передаём PTB."""
    data = await req.json()
    update = Update.de_json(data, bot)
    await application.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "bot running"}

# ---------- жизненный цикл FastAPI -------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    await application.initialize()
    # перестраховка: удаляем старый webhook, ставим новый
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    log.info("Webhook установлен: %s", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await application.shutdown()
    log.info("Бот остановлен")

# ---------- локальный/Render-старт -------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
