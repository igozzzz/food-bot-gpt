import os
import io
import base64
import json
import logging
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from PIL import Image
from openai import AsyncOpenAI
from telegram import Bot, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import uvicorn
import httpx

# === Настройка логирования ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# === Загрузка конфигурации ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 МБ

# Проверка переменных окружения
if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL]):
    logger.error("Отсутствуют необходимые переменные окружения")
    raise EnvironmentError("TELEGRAM_TOKEN, OPENAI_API_KEY или WEBHOOK_URL не заданы")

# Проверка HTTPS для WEBHOOK_URL
if not WEBHOOK_URL.startswith("https://"):
    logger.error("WEBHOOK_URL должен использовать HTTPS")
    raise ValueError("WEBHOOK_URL должен начинаться с https://")

# === Инициализация клиентов ===
bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    http_client=None  # Отключаем кастомизацию http_client
)
application = Application.builder().token(TELEGRAM_TOKEN).build()

# === Обработчик ошибок ===
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок для Telegram-бота"""
    logger.error(f"Ошибка в обработке обновления: {context.error}")
    if update and update.message:
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуй снова позже.")

# === Обработчики Telegram ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    if not update.message:
        logger.warning(f"Обновление от {update.effective_user.id} не содержит сообщения")
        return
    await update.message.reply_text(
        "👋 Привет! Я — бот-диетолог.\n\n"
        "📸 Пришли мне фото еды — и я подскажу:\n"
        "• Название блюда\n"
        "• Калории и БЖУ на 100 г\n\n"
        "Готов? Жду фото!"
    )
    logger.info(f"Пользователь {update.effective_user.id} запустил бота")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик загруженных фотографий"""
    try:
        if not update.message or not update.message.photo:
            logger.warning(f"Обновление от {update.effective_user.id} не содержит фото")
            return

        photo = update.message.photo[-1]
        # Проверка размера файла
        if photo.file_size > MAX_FILE_SIZE:
            await update.message.reply_text("⚠️ Фото слишком большое! Максимум 10 МБ.")
            logger.warning(f"Слишком большой файл от {update.effective_user.id}: {photo.file_size} байт")
            return

        file = await photo.get_file()
        # Загрузка файла в память с использованием httpx
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(file.file_path)
            response.raise_for_status()
            bio = io.BytesIO(response.content)
        bio.seek(0)

        # Конвертация изображения
        with Image.open(bio).convert("RGB") as image:
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
            img_b64 = base64.b64encode(buffer.getvalue()).decode()

        await update.message.reply_text("🤖 Анализирую фото...")
        logger.info(f"Обработка фото от {update.effective_user.id}")

        # Запрос к OpenAI
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты нутрициолог. Проанализируй фото еды и верни ответ в формате JSON со следующими полями:\n"
                        "- dish: название блюда (строка)\n"
                        "- calories: калории на 100 г (число или строка '—' если неизвестно)\n"
                        "- protein: белки на 100 г (число или строка '—' если неизвестно)\n"
                        "- fat: жиры на 100 г (число или строка '—' если неизвестно)\n"
                        "- carbs: углеводы на 100 г (число или строка '—' если неизвестно)\n"
                        "Отвечай только в формате JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": "Проанализируй фото еды."},
                    ],
                },
            ],
            temperature=0.3,
            max_tokens=300,
        )

        # Парсинг ответа
        try:
            response_text = response.choices[0].message.content
            logger.debug(f"Ответ от OpenAI: {response_text}")
            data = json.loads(response_text)
            # Проверка наличия всех ключей
            required_keys = ["dish", "calories", "protein", "fat", "carbs"]
            if not all(key in data for key in required_keys):
                raise ValueError("Некоторые данные отсутствуют в ответе OpenAI")
            dish = data["dish"]
            cal = str(data["calories"])
            prot = str(data["protein"])
            fat = str(data["fat"])
            carb = str(data["carbs"])
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON от OpenAI: {e}")
            await update.message.reply_text("⚠️ Ошибка обработки ответа от OpenAI. Попробуй другое фото.")
            return
        except (KeyError, ValueError) as e:
            logger.error(f"Ошибка в данных от OpenAI: {e}")
            await update.message.reply_text("⚠️ Недостаточно данных от OpenAI. Попробуй другое фото.")
            return

        # Отправка ответа
        await update.message.reply_text(
            f"🍽 Блюдо: {dish}\n"
            f"🔥 Калории: {cal} ккал / 100 г\n"
            f"🥩 Белки: {prot} г\n"
            f"🥑 Жиры: {fat} г\n"
            f"🍞 Углеводы: {carb} г"
        )
        logger.info(f"Успешно обработано фото для {update.effective_user.id}: {dish}")

    except Exception as e:
        logger.error(f"Ошибка обработки фото для {update.effective_user.id}: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Не удалось обработать фото. Попробуй другое изображение.")
        return
    finally:
        bio.close()

# === Подключение обработчиков ===
application.add_error_handler(error_handler)
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# === Webhook endpoint ===
@app.post("/", status_code=200)
async def telegram_webhook(req: Request) -> dict:
    """Обработчик webhook-запросов от Telegram"""
    try:
        data = await req.json()
        update = Update.de_json(data, application.bot)
        if update:
            await application.process_update(update)
            logger.info(f"Обработан webhook-запрос от {update.effective_user.id}")
        else:
            logger.warning("Получен некорректный update")
        return {"ok": True}
    except Exception as e:
        logger.error(f"Ошибка в webhook handler: {e}")
        return {"ok": False}

# === Web UI Ping ===
@app.get("/")
async def root() -> dict:
    """Проверка статуса бота"""
    return {"status": "bot running"}

# === Обработчики событий FastAPI ===
@app.on_event("startup")
async def on_startup() -> None:
    """Инициализация приложения и установка вебхука"""
    try:
        await application.initialize()
        await application.bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook установлен: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Ошибка установки вебхука: {e}")
        raise HTTPException(status_code=500, detail="Не удалось установить webhook")

@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Остановка приложения и удаление вебхука"""
    try:
        await application.bot.delete_webhook()
        await application.shutdown()
        logger.info("Webhook удален, приложение остановлено")
    except Exception as e:
        logger.error(f"Ошибка при остановке: {e}")

# === Запуск приложения ===
if __name__ == "__main__":
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)