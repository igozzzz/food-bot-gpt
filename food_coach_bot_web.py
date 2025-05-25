"""
food_coach_bot_web.py
-----------------------------------------------
Telegram-бот-нутрициолог: присылаете фото блюда —
получаете название + КБЖУ на 100 г.

• PTB v21, FastAPI webhook
• OpenAI (gpt-4o) — ответ строго в JSON
• Требуемые env-переменные: TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL, PORT
"""

import os
import io
import json
import base64
import logging
from typing import Any, Dict

import httpx
import uvicorn
from dotenv import load_dotenv
from PIL import Image
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─────────── ЛОГИРОВАНИЕ ───────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("food_bot")

# ─────────── ЗАГРУЗКА ENV ─────────────────────────────────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
PORT           = int(os.getenv("PORT", 8000))
MAX_FILE_SIZE  = 10 * 1024 * 1024  # 10 МБ

if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL]):
    raise RuntimeError("Нужно задать TELEGRAM_TOKEN, OPENAI_API_KEY и WEBHOOK_URL в .env")

# ─────────── ИНИЦИАЛИЗАЦИЯ ────────────────────────────────
bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

import openai
openai_client = openai.AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    http_client=httpx.AsyncClient(timeout=30.0),
)

# ─────────── HELPER ДЛЯ OpenAI ───────────────────────────
async def analyse_image(img_b64: str) -> Dict[str, Any]:
    """
    Отправляем картинку, получаем dict с полями:
    dish, calories, protein, fat, carbs.
    """
    resp = await openai_client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},  # обязательный объект
        temperature=0.2,
        max_tokens=200,
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты нутрициолог. Верни JSON-объект с ключами: "
                    "dish, calories, protein, fat, carbs. "
                    "Значения — на 100 г. Если не уверен — ставь “—”."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    },
                    {"type": "text", "text": "Проанализируй блюдо на фото."},
                ],
            },
        ],
    )
    # Парсим строку JSON в dict
    return json.loads(resp.choices[0].message.content)

# ─────────── HANDLERS ────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Пришли фото блюда — скажу название и КБЖУ на 100 г."
    )

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        # Проверяем наличие фото
        photos = update.message.photo
        if not photos:
            return
        photo = photos[-1]  # берём самую большую превью
        if photo.file_size and photo.file_size > MAX_FILE_SIZE:
            return await update.message.reply_text("⚠️ Фото > 10 МБ, пришлите поменьше.")

        # Скачиваем файл в буфер
        tg_file = await photo.get_file()
        buf = io.BytesIO()
        await tg_file.download_to_memory(out=buf)
        raw = buf.getvalue()

        # Подготовка base64
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        buf2 = io.BytesIO()
        image.save(buf2, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf2.getvalue()).decode()

        await update.message.reply_text("🤖 Анализирую фото…")

        # Запрос к OpenAI
        try:
            data = await analyse_image(img_b64)
        except Exception as e:
            log.error("OpenAI error:", exc_info=e)
            return await update.message.reply_text("⚠️ Ошибка анализа. Попробуйте ещё.")

        # Формируем ответ
        dish = data.get("dish", "—")
        cal  = data.get("calories", "—")
        prot = data.get("protein", "—")
        fat  = data.get("fat", "—")
        carb = data.get("carbs", "—")

        await update.message.reply_text(
            f"🍽 {dish}\n"
            f"🔥 {cal} ккал / 100 г\n"
            f"🥩 {prot} г   🥑 {fat} г   🍞 {carb} г"
        )

    except Exception as e:
        log.error("Handle photo error:", exc_info=e)
        await update.message.reply_text("⚠️ Не удалось обработать фото. Попробуйте другое.")

# ─────────── ПОДВЯЗКА HANDLERS ─────────────────────────
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ─────────── FASTAPI WEBHOOK ────────────────────────────
@app.post("/", status_code=200)
async def telegram_webhook(req: Request) -> dict:
    payload = await req.json()
    if not getattr(application, "_initialized", False):
        await application.initialize()
    update = Update.de_json(payload, bot)
    await application.process_update(update)
    return {"ok": True}

@app.get("/")
async def root() -> dict:
    return {"status": "alive"}

# ─────────── START / SHUTDOWN EVENTS ────────────────────
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

# ─────────── ЛОКАЛЬНЫЙ ЗАПУСК ───────────────────────────
if __name__ == "__main__":
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
