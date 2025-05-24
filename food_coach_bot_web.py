import os
import io
import re
import base64

from fastapi import FastAPI, Request
from dotenv import load_dotenv
from PIL import Image
import openai
from telegram import Bot, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# === Загрузка конфигов ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")    # https://<your-render-domain>.onrender.com/
PORT           = int(os.getenv("PORT", 8000))

openai.api_key = OPENAI_API_KEY
bot = Bot(token=TELEGRAM_TOKEN)

# === FastAPI приложение ===
app = FastAPI()

# --- Простой GET для health check ---
@app.get("/")
async def root():
    return {"status": "Bot is running"}

# === Telegram ApplicationBuilder и хендлеры ===
application = ApplicationBuilder() \
    .token(TELEGRAM_TOKEN) \
    .build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Пришли фото блюда — я определю его и выдам КБЖУ на 100 г."
    )

from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# updated photo handling

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    fobj = await photo.get_file()
    bio = io.BytesIO()
    await fobj.download_to_memory(out=bio)

    img = Image.open(bio).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    await update.message.reply_text("🤖 Анализирую фото...")

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "Ты — эксперт по питанию. Отвечай кратко по шаблону:\n"
                               "1. Название блюда\n"
                               "2. Калории на 100 г\n"
                               "3. Белки, Жиры, Углеводы на 100 г\n"
                               "Отвечай на русском языке."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": "Что на фото?"}
                    ]
                }
            ],
            max_tokens=400
        )

        text = response.choices[0].message.content

        name = re.search(r"1\.\s*(.+)", text)
        cal  = re.search(r"(\d+)[^\d]*ккал", text.lower())
        prot = re.search(r"белк.*?(\d+)", text.lower())
        fat  = re.search(r"жир.*?(\d+)", text.lower())
        carb = re.search(r"углевод.*?(\d+)", text.lower())

        dish = name.group(1).strip() if name else "Не распознано"
        cal  = cal.group(1) if cal else "—"
        prot = prot.group(1) if prot else "—"
        fat  = fat.group(1) if fat else "—"
        carb = carb.group(1) if carb else "—"

        await update.message.reply_text(
            f"🍽 Блюдо: {dish}\n"
            f"🔥 Калории: {cal} ккал / 100 г\n"
            f"🥩 Белки: {prot} г\n"
            f"🥑 Жиры: {fat} г\n"
            f"🍞 Углеводы: {carb} г"
        )

    except Exception as e:
        await update.message.reply_text("❌ Ошибка при анализе фото.")
        print("Ошибка OpenAI:", e)

# Регистрируем хендлеры
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# === Webhook endpoint ===
@app.post("/", status_code=200)
async def telegram_webhook(req: Request):
    data = await req.json()
    try:
        upd = Update.de_json(data, bot)
        await application.process_update(upd)
    except Exception as e:
        print("❌ Ошибка в webhook handler:", e)
        import traceback; traceback.print_exc()
    return {"ok": True}

# --- Инициализация и установка webhook при старте ---
@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await application.initialize()
    await bot.set_webhook(WEBHOOK_URL)
    print("✅ Webhook установлен:", WEBHOOK_URL)

# === Локальный запуск Uvicorn ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
