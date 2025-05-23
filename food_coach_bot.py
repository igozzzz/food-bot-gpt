import os
import io
import re
import base64

from PIL import Image
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import openai

# --- Загрузка токенов ---
load_dotenv()
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
openai.api_key  = os.getenv("OPENAI_API_KEY")

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Отправь мне фото блюда, и я определю, что это, "
        "а также укажу калории, белки, жиры и углеводы на 100 г."
    )

# --- Обработка фотографии ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Скачиваем картинку в память
    photo = update.message.photo[-1]
    fobj  = await photo.get_file()
    bio   = io.BytesIO()
    await fobj.download_to_memory(out=bio)

    # Кодируем в base64
    image   = Image.open(bio).convert("RGB")
    buffer  = io.BytesIO()
    image.save(buffer, format="JPEG")
    img_b64 = base64.b64encode(buffer.getvalue()).decode()

    await update.message.reply_text("🤖 Анализирую фото...")

    # Вызываем GPT Vision через chat.completions
    resp = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role":"system","content":
             "Ты — эксперт по питанию. Кратко ответь по шаблону:\n"
             "1. Название блюда\n"
             "2. Калории на 100 г\n"
             "3. Белки, Жиры, Углеводы на 100 г\n"
             "На русском языке."},
            {"role":"user","content":[
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{img_b64}"}},
                {"type":"text","text":"Что на фото?"}
            ]}
        ],
        temperature=0.2,
        max_tokens=250
    )

    text = resp.choices[0].message.content

    # Парсим ответ регулярками
    name = re.search(r"1\.\s*(.+)", text)
    cal  = re.search(r"(\d+)[^\d]*ккал", text.lower())
    prot = re.search(r"белк.*?(\d+)", text.lower())
    fat  = re.search(r"жир.*?(\d+)", text.lower())
    carb = re.search(r"углевод.*?(\d+)", text.lower())

    dish  = name.group(1).strip() if name else "Не распознано"
    cal   = cal and cal.group(1) or "—"
    prot  = prot and prot.group(1) or "—"
    fat   = fat and fat.group(1) or "—"
    carb  = carb and carb.group(1) or "—"

    # Отправляем пользователю
    await update.message.reply_text(
        f"🍽 Блюдо: {dish}\n"
        f"🔥 Калории: {cal} ккал / 100 г\n"
        f"🥩 Белки: {prot} г\n"
        f"🥑 Жиры: {fat} г\n"
        f"🍞 Углеводы: {carb} г"
    )

# --- Точка входа ---
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print("✅ Food-Bot запущен…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
