# food_bot_webhook.py
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("food_bot")

# ───── ENV ─────────────────────────────────────────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")  # https://<ваш-домен>
PORT           = int(os.getenv("PORT", 8000))
MAX_FILE_SIZE  = 10 * 1024 * 1024  # 10 МБ

if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL]):
    raise RuntimeError("Нужно задать TELEGRAM_TOKEN, OPENAI_API_KEY и WEBHOOK_URL")

# ───── КЛИЕНТЫ ───────────────────────────────────────
bot = Bot(token=TELEGRAM_TOKEN)
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app = FastAPI()

import openai
openai_client = openai.AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    http_client=httpx.AsyncClient(timeout=30.0),
)

# ───── ФУНКЦИЯ АНАЛИЗА ─────────────────────────────────
async def analyse_image(img_b64: str) -> dict[str, Any]:
    resp = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type":"json_object"},  # строго JSON-объект
        temperature=0.2,
        max_tokens=200,
        messages=[
            {"role":"system","content":
             "Ты нутрициолог. Верни JSON-объект с ключами: "
             "dish, calories, protein, fat, carbs. "
             "Значения — на 100 г. Если не знаешь — ставь \"—\"."},
            {"role":"user","content":[
                {"type":"image_url","image_url":
                 {"url":f"data:image/jpeg;base64,{img_b64}"}},
                {"type":"text","text":"Проанализируй блюдо на фото."}
            ]},
        ],
    )
    # после response_format={"type":"json_object"} content — уже dict
    data = resp.choices[0].message.content
    if isinstance(data, str):
        data = json.loads(data)
    return data

# ───── HANDLERS ───────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Пришли фото блюда — скажу название и КБЖУ на 100 г."
    )
    log.info("User %s started bot", update.effective_user.id)

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        photo = update.message.photo[-1]
        log.info("🔍 HANDLE_PHOTO start for user %s, size=%s", user_id, photo.file_size)
        if photo.file_size and photo.file_size > MAX_FILE_SIZE:
            await update.message.reply_text("⚠️ Фото >10 МБ. Пришли поменьше.")
            return

        tg_file: File = await photo.get_file()
        buf = io.BytesIO()
        await tg_file.download_to_memory(out=buf)
        raw = buf.getvalue()
        log.info("🔍 downloaded %d bytes", len(raw))

        img = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO(); img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()
        log.info("🔍 image → base64, length=%d", len(img_b64))

        await update.message.reply_text("🤖 Анализирую фото…")
        log.info("🔍 Calling analyse_image() for user %s", user_id)
        data = await analyse_image(img_b64)
        log.info("🔍 analyse_image returned %s", data)

        dish     = data.get("dish", "—")
        calories = data.get("calories", "—")
        protein  = data.get("protein", "—")
        fat      = data.get("fat", "—")
        carbs    = data.get("carbs", "—")

        text = (
            f"🍽 {dish}\n"
            f"🔥 {calories} ккал / 100 г\n"
            f"🥩 {protein} г   🥑 {fat} г   🍞 {carbs} г"
        )
        await update.message.reply_text(text)
        log.info("✅ HANDLE_PHOTO done for user %s", user_id)

    except Exception as e:
        log.error("Handle photo error: %s", e, exc_info=True)
        await update.message.reply_text("⚠️ Не удалось обработать фото. Попробуй другое.")

# ───── MAPPING ─────────────────────────────────────────
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ───── WEBHOOK ENDPOINT ─────────────────────────────────
@app.post("/", status_code=200)
async def telegram_webhook(req: Request) -> dict:
    data = await req.json()
    if not getattr(application, "_initialized", False):
        await application.initialize()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status":"alive"}

# ───── LIFECYCLE ────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    log.info("🚀 Webhook установлен: %s", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await application.shutdown()
    log.info("🛑 Webhook удалён, бот остановлен")

# ───── LOCAL RUN ───────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("food_bot_webhook:app", host="0.0.0.0", port=PORT)
