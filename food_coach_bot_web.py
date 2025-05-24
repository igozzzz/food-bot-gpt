import os
import io
import base64
import re
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

# === Конфигурация ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://your-bot.onrender.com/
PORT = int(os.getenv("PORT", 8000))

openai.api_key = OPENAI_API_KEY
bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()

# === Telegram AppBuilder ===
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

# === Обработчики ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я — бот-диетолог.\n\n"
        "📸 Пришли мне фото еды — и я подскажу:\n"
        "• Название блюда\n"
        "• Калории и БЖУ на 100 г\n\n"
        "Готов? Жду фото!"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    bio = io.BytesIO()
    await file.download_to_memory(out=bio)

    image = Image.open(bio).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    img_b64 = base64.b64encode(buffer.getvalue()).decode()

    await update.message.reply_text("🤖 Анализирую фото...")

    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": (
                    "Ты нутрициолог. Ответь кратко по пунктам:\n"
                    "1. Название блюда\n"
                    "2. Калории на 100 г\n"
                    "3. Белки, жиры, углеводы на 100 г\n"
                    "Отвечай на русском языке."
                )},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": "Что на фото? Укажи БЖУ и калории."}
                ]}
            ],
            temperature=0.3,
            max_tokens=300
        )

        reply = response.choices[0].message.content

        name = re.search(r"1\.\s*(.+)", reply)
        cal = re.search(r"калл?ор[ииея].*?(\d+)", reply.lower())
        prot = re.search(r"белк.*?(\d+)", reply.lower())
        fat = re.search(r"жир.*?(\d+)", reply.lower())
        carb = re.search(r"углевод.*?(\d+)", reply.lower())

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
    except Exception as e:
        print("❌ Ошибка обработки:", e)
        await update.message.reply_text("⚠️ Не удалось обработать фото. Попробуй другое изображение.")

# === Подключение обработчиков ===
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# === Webhook endpoint ===
@app.post("/", status_code=200)
async def telegram_webhook(req: Request):
    data = await req.json()
    try:
        await application.initialize()
        update = Update.de_json(data, bot)
        await application.process_update(update)
    except Exception as e:
        print("❌ Ошибка в webhook handler:", e)
    return {"ok": True}

# === Web UI Ping ===
@app.get("/")
async def root():
    return {"status": "bot running"}

@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await bot.set_webhook(WEBHOOK_URL)
    print("✅ Webhook установлен:", WEBHOOK_URL)

# === Запуск локально через uvicorn ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
