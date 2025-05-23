import os
import io
import base64
from PIL import Image
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
import openai

# Загрузка ключей
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
openai.api_key = os.getenv("OPENAI_API_KEY")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Отправь мне фото блюда, и я определю КБЖУ на 100 г."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Получение фото
    photo = update.message.photo[-1]
    file = await photo.get_file()
    bio = io.BytesIO()
    await file.download_to_memory(out=bio)
    bio.seek(0)

    # Кодирование в base64 для передачи в GPT Vision
    image = Image.open(bio).convert("RGB")
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    img_b64 = base64.b64encode(buffered.getvalue()).decode()

    await update.message.reply_text("🤖 Анализирую фото... Пожалуйста, подожди.")

    # Запрос к OpenAI GPT Vision
    resp = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role":"system","content":"Ты — эксперт-нутрициолог. Определи название блюда и рассчитай калории, белки, жиры и углеводы на 100 грамм."},
            {"role":"user","content":[
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{img_b64}"}},
                {"type":"text","text":"Пожалуйста, ответь в формате:\nБлюдо: <название>\nКалории: <число> ккал\nБелки: <число> г\nЖиры: <число> г\nУглеводы: <число> г"}
            ]}
        ],
        temperature=0.1,
        max_tokens=200
    )

    text = resp.choices[0].message.content.strip()
    await update.message.reply_text(f"✅ Результат:\n{text}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    # Приветственное сообщение на текстовую команду Start
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    # Обработчик фотографий
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print("✅ Photo Macro Bot запущен...")
    app.run_polling()
