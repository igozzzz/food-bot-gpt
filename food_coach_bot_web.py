# food_coach_bot_web.py
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
from fastapi import FastAPI, Request, HTTPException
from telegram import Update, Bot, File
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ────── базовое логирование ────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("food_bot")

# ────── .env ───────────────────────────────────────────────────────────────────
load_dotenv()
TELEGRAM_TOKEN: str | None = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL: str | None = os.getenv("WEBHOOK_URL")          # https://…/ on Render
PORT: int = int(os.getenv("PORT", 8000))
MAX_FILE_SIZE = 10 * 1024 * 1024                            # 10 МБ

if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL]):
    raise RuntimeError("TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL обязательны")

# ────── внешние клиенты ───────────────────────────────────────────────────────
bot: Bot = Bot(token=TELEGRAM_TOKEN)
application: Application = Application.builder().token(TELEGRAM_TOKEN).build()
app: FastAPI = FastAPI()

import openai
openai_client = openai.AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    http_client=httpx.AsyncClient(timeout=30.0),
)

# ────── helpers ───────────────────────────────────────────────────────────────
async def openai_json(img_b64: str) -> dict[str, Any]:
    """Отправить изображение в GPT-4o и вернуть dict (dish/calories/…)."""
    resp = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=200,
        messages=[
            {"role": "system",
             "content": (
                 "Ты нутрициолог. Верни только JSON с полями "
                 "dish, calories, protein, fat, carbs."
             )},
            {"role": "user",
             "content": [
                 {"type": "image_url",
                  "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                 {"type": "text", "text": "Проанализируй фото еды."},
             ]},
        ],
    )
    return json.loads(resp.choices[0].message.content)

# ────── Telegram-handlers ──────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Пришли фото блюда, а я дам название и КБЖУ на 100 г."
    )

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        photo = update.message.photo[-1]                # наибольшее превью
        if photo.file_size > MAX_FILE_SIZE:
            await update.message.reply_text("⚠️ Фото > 10 МБ, пришли поменьше.")
            return

        # ── скачиваем ───────────────────────────────────
        tg_file: File = await photo.get_file()
        raw: bytes = await tg_file.download()           # PTB-21 bytes

        # ── превращаем в base64 для OpenAI ─────────────
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO();  img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        await update.message.reply_text("🤖 Анализирую фото…")

        # ── GPT-4o ─────────────────────────────────────
        try:
            data = await openai_json(img_b64)
        except Exception as e:
            log.error("OpenAI error: %s", e, exc_info=True)
            await update.message.reply_text("⚠️ Ошибка анализа. Попробуй ещё.")
            return

        dish = data.get("dish", "не распознано")
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
        await update.message.reply_text("⚠️ Не смог обработать фото, сорри.")

# ────── PTB wiring ────────────────────────────────────────────────────────────
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ───── FastAPI webhook ─────────────────────────────────────────
@app.post("/", status_code=200)
async def telegram_webhook(req: Request) -> dict:
    data = await req.json()

    # ① если бот ещё не инициализирован — делаем это «лениво»
    if not getattr(application, "_initialized", False):
        await application.initialize()

    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app.get("/")
async def root() -> dict:
    return {"status": "alive"}

# ────── events ───────────────────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup() -> None:
    await application.initialize()
    await application.bot.set_webhook(WEBHOOK_URL)
    log.info("Webhook set → %s", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown() -> None:
    await application.bot.delete_webhook()
    await application.shutdown()
    log.info("Webhook removed, bot stopped")

# ────── local run ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
