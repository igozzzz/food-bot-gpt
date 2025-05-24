import os
import io
import base64
import re
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from PIL import Image
from telegram import Bot, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from openai import OpenAI

# === Загрузка переменных ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))

bot = Bot(token=TELEGRAM_TOKEN)
openai = OpenAI(api_key=OPENAI_API_KEY)

# === FastAPI app ===
app = FastAPI()

# === Инициализация Telegram Application ===
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")

# === Обработчики ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я — бот-диетолог.\n\n📸 Пришли мне фото еды, и я покажу примерную калорийность, БЖУ и название блюда."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    img = Image.open(buf).convert("RGB")
    img_io = io.BytesIO()
    img.save(img_io, format="JPEG")
    img_base64 = base64.b64encode(img_io.getvalue()).decode()

    await update.message.reply_text("🤖 Анализирую фото...")

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты — эксперт по питанию. Определи:\n"
                    "1. Название блюда\n"
                    "2. Калории на 100 г\n"
                    "3. Белки, Жиры, Углеводы на 100 г\n"
                    "Формат: кратко, по пунктам, без лишнего."
                )
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}
                    },
                    {"type": "text", "text": "Что на фото?"}
                ]
            }
        ],
        temperature=0.2,
        max_tokens=300,
    )

    reply = response.choices[0].message.content
    name = re.search(r"1\..*?(.+)", reply)
    cal = re.search(r"калории.*?(\d+)", reply.lower())
    prot = re.search(r"белк\w*.*?(\d+)", reply.lower())
    fat = re.search(r"жир\w*.*?(\d+)", reply.lower())
    carb = re.search(r"углевод\w*.*?(\d+)", reply.lower())

    dish = name.group(1).strip() if name else "Не распознано"
    cal = cal.group(1) if cal else "—"
    prot = prot.group(1) if prot else "—"
    fat = fat.group(1) if fat else "—"
    carb = carb.group(1) if carb else "—"

    await update.message.reply_text(
        f"🍽 Блюдо: {dish}\n"
        f"🔥 Калории: {cal} ккал / 100 г\n"
        f"🥩 Белки: {prot} г\n"
        f"🥑 Жиры: {fat} г\n"
        f"🍞 Углеводы: {carb} г"
    )

# === Роуты ===
@app.get("/")
async def root():
    return {"status": "Bot is running"}

@app.post("/", status_code=200)
async def telegram_webhook(req: Request):
    data = await req.json()
    try:
        update = Update.de_json(data, bot)
        await application.process_update(update)
    except Exception as e:
        print("❌ Ошибка обработки:", e)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")

# === Регистрируем обработчики ===
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# === Локальный запуск ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
