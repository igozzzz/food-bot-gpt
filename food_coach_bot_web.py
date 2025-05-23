import os
import io
import re
import base64

from fastapi import FastAPI, Request
from pydantic import BaseModel
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

# --- Загрузка конфигурации ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")   # например: https://your-app.onrender.com/
PORT           = int(os.getenv("PORT", "8000"))

openai.api_key = OPENAI_API_KEY
bot = Bot(token=TELEGRAM_TOKEN)

# === FastAPI приложение ===
app = FastAPI()

# Перехват обновлений от Telegram
@app.post("/", status_code=200)
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, bot)
    # передаём всем хендлерам
    await application.process_update(update)
    return {}

# При старте сервера устанавливаем webhook
@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(WEBHOOK_URL)
    print("✅ Webhook установлен:", WEBHOOK_URL)

# === Ваши хендлеры ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Пришли фото блюда — я определю, что это, "
        "и выдам КБЖУ на 100 г."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # скачиваем картинку
    photo = update.message.photo[-1]
    fobj  = await photo.get_file()
    bio   = io.BytesIO()
    await fobj.download_to_memory(out=bio)

    # конвертим и кодируем в base64
    image  = Image.open(bio).convert("RGB")
    buff   = io.BytesIO()
    image.save(buff, format="JPEG")
    img_b64 = base64.b64encode(buff.getvalue()).decode()

    await update.message.reply_text("🤖 Анализирую фото...")

    # вызываем GPT Vision
    resp = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role":"system","content":
             "Ты — эксперт по питанию. Кратко по шаблону:\n"
             "1. Название блюда\n"
             "2. Калории на 100 г\n"
             "3. Белки, Жиры, Углеводы на 100 г\n"
             "На русском."},
            {"role":"user","content":[
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{img_b64}"}},
                {"type":"text","text":"Что на фото?"}
            ]}
        ],
        temperature=0.2,
        max_tokens=200
    )

    text = resp.choices[0].message.content

    # парсим регулярками
    name = re.search(r"1\.\s*(.+)", text)
    cal  = re.search(r"(\d+)[^\d]*ккал", text.lower())
    prot = re.search(r"белк.*?(\d+)", text.lower())
    fat  = re.search(r"жир.*?(\d+)", text.lower())
    carb = re.search(r"углевод.*?(\d+)", text.lower())

    dish = name.group(1).strip() if name else "Не распознано"
    cal   = cal.group(1)  if cal  else "—"
    prot  = prot.group(1) if prot else "—"
    fat   = fat.group(1)  if fat  else "—"
    carb  = carb.group(1) if carb else "—"

    # отвечаем
    await update.message.reply_text(
        f"🍽 Блюдо: {dish}\n"
        f"🔥 Калории: {cal} ккал / 100 г\n"
        f"🥩 Белки: {prot} г\n"
        f"🥑 Жиры: {fat} г\n"
        f"🍞 Углеводы: {carb} г"
    )

# === Сборка приложения Telegram ===
application = ApplicationBuilder()\
    .token(TELEGRAM_TOKEN)\
    .build()

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# === Запуск Uvicorn, если стартуем локально ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("food_bot_webhook:app", host="0.0.0.0", port=PORT)
