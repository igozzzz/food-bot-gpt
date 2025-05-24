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
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# === Загрузка переменных окружения ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))

# === Инициализация OpenAI и Telegram Bot ===
openai.api_key = OPENAI_API_KEY
bot = Bot(token=TELEGRAM_TOKEN)

# === FastAPI App ===
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "Bot is running"}

@app.post("/", status_code=200)
async def telegram_webhook(req: Request):
    data = await req.json()
    try:
        upd = Update.de_json(data, bot)
        await application.initialize()  # Новое важное добавление
        await application.process_update(upd)
    except Exception as e:
        print("\u274c Ошибка в webhook handler:", e)
        import traceback
        traceback.print_exc()
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(WEBHOOK_URL)
    print("\u2705 Webhook установлен:", WEBHOOK_URL)

# === Telegram handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\U0001F44B Привет! Я бот-диетолог.\n\n"
        "\U0001F4F7 Просто пришли мне фото блюда, и я определю его и выдам калорийность."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    fobj = await photo.get_file()
    bio = io.BytesIO()
    await fobj.download_to_memory(out=bio)

    img = Image.open(bio).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    await update.message.reply_text("\U0001F916 Анализирую фото...")

    client = openai.OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content":
             "Ты — диетолог. Определи:
1. Название блюда
2. Калории на 100 г
3. Белки, Жиры, Углеводы на 100 г\nТолько на русском."},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": "Что на фото?"}
            ]}
        ],
        temperature=0.2,
        max_tokens=300
    )

    reply = response.choices[0].message.content

    name = re.search(r"1\\.\\s*(.+)", reply)
    cal = re.search(r"калори\\D*(\\d+)", reply.lower())
    prot = re.search(r"белк\\D*(\\d+)", reply.lower())
    fat = re.search(r"жир\\D*(\\d+)", reply.lower())
    carb = re.search(r"углевод\\D*(\\d+)", reply.lower())

    dish = name.group(1).strip() if name else "Не распознано"
    cal = cal.group(1) if cal else "—"
    prot = prot.group(1) if prot else "—"
    fat = fat.group(1) if fat else "—"
    carb = carb.group(1) if carb else "—"

    await update.message.reply_text(
        f"\U0001F37D Блюдо: {dish}\n"
        f"\U0001F525 Калории: {cal} ккал / 100 г\n"
        f"\U0001F969 Белки: {prot} г\n"
        f"\U0001F951 Жиры: {fat} г\n"
        f"\U0001F35E Углеводы: {carb} г"
    )

# === Application ===
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# === Локальный запуск ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
