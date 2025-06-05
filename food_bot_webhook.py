# food_bot_webhook.py
import os
import io
import json
import base64
import logging
import asyncio
from typing import Any, Dict, Optional

import httpx
import uvicorn
from dotenv import load_dotenv
from PIL import Image
from fastapi import FastAPI, Request, HTTPException
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
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://food-bot-gpt.onrender.com/
PORT = int(os.getenv("PORT", 8000))
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 МБ

if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL]):
    raise RuntimeError("Нужно задать TELEGRAM_TOKEN, OPENAI_API_KEY и WEBHOOK_URL")

# ───── КЛИЕНТЫ ───────────────────────────────────────
bot = Bot(token=TELEGRAM_TOKEN)
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app = FastAPI()

# Инициализация OpenAI клиента
try:
    import openai
    openai_client = openai.AsyncOpenAI(
        api_key=OPENAI_API_KEY,
        http_client=httpx.AsyncClient(timeout=30.0),
    )
except ImportError:
    log.error("OpenAI library not installed. Install with: pip install openai")
    raise

# ───── KEEP ALIVE TASK ─────────────────────────────────
async def keep_alive(interval: int = 600):
    """Periodically pings the service health endpoint to keep it alive."""
    while True:
        try:
            async with httpx.AsyncClient() as client:
                await client.get(f"{WEBHOOK_URL}/health")
        except Exception as e:
            log.warning("Keep alive request failed: %s", e)
        await asyncio.sleep(interval)

# ───── ФУНКЦИЯ АНАЛИЗА ─────────────────────────────────
async def analyse_image(img_b64: str) -> Dict[str, Any]:
    """Анализирует изображение еды через OpenAI API"""
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},  # строго JSON-объект
            temperature=0.2,
            max_tokens=300,  # Увеличено для более подробного ответа
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты нутрициолог. Проанализируй блюдо на фото и верни JSON-объект с ключами: "
                        "dish (название блюда), calories (калории), protein (белки), fat (жиры), carbs (углеводы). "
                        "Все значения питательности указывай на 100 грамм продукта. "
                        "Если точно не знаешь значение, ставь \"—\". "
                        "Пример: {\"dish\": \"Борщ\", \"calories\": \"45\", \"protein\": \"1.5\", \"fat\": \"2.1\", \"carbs\": \"6.7\"}"
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                        },
                        {
                            "type": "text",
                            "text": "Проанализируй блюдо на фото и определи КБЖУ."
                        }
                    ]
                },
            ],
        )
        
        # Обработка ответа
        content = resp.choices[0].message.content
        if isinstance(content, str):
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                log.error("Failed to parse JSON response: %s", e)
                return {"dish": "—", "calories": "—", "protein": "—", "fat": "—", "carbs": "—"}
        else:
            data = content
        
        # Валидация ключей
        required_keys = ["dish", "calories", "protein", "fat", "carbs"]
        for key in required_keys:
            if key not in data:
                data[key] = "—"
        
        return data
        
    except Exception as e:
        log.error("Error in analyse_image: %s", e, exc_info=True)
        return {"dish": "—", "calories": "—", "protein": "—", "fat": "—", "carbs": "—"}

# ───── HANDLERS ───────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "👋 Привет! Пришли фото блюда — скажу название и КБЖУ на 100 г.\n\n"
        "🔥 Калории\n🥩 Белки\n🥑 Жиры\n🍞 Углеводы"
    )
    log.info("User %s started bot", update.effective_user.id)

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик фотографий"""
    user_id = update.effective_user.id
    
    try:
        photo = update.message.photo[-1]  # Берем фото наибольшего размера
        log.info("🔍 HANDLE_PHOTO start for user %s, size=%s", user_id, photo.file_size)
        
        # Проверка размера файла
        if photo.file_size and photo.file_size > MAX_FILE_SIZE:
            await update.message.reply_text("⚠️ Фото слишком большое (>10 МБ). Пришли фото поменьше.")
            return

        # Отправляем сообщение о начале обработки
        processing_msg = await update.message.reply_text("🤖 Анализирую фото...")
        
        # Скачиваем файл
        tg_file: File = await photo.get_file()
        buf = io.BytesIO()
        await tg_file.download_to_memory(out=buf)
        raw = buf.getvalue()
        log.info("🔍 Downloaded %d bytes", len(raw))

        # Конвертируем в JPEG и сжимаем
        try:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            
            # Ограничиваем размер изображения для экономии токенов
            max_size = (1024, 1024)
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            img_b64 = base64.b64encode(buf.getvalue()).decode()
            log.info("🔍 Image converted to base64, length=%d", len(img_b64))
        except Exception as e:
            log.error("Image processing error: %s", e)
            await processing_msg.edit_text("⚠️ Ошибка обработки изображения. Попробуйте другое фото.")
            return

        # Анализируем изображение
        log.info("🔍 Calling analyse_image() for user %s", user_id)
        data = await analyse_image(img_b64)
        log.info("🔍 analyse_image returned %s", data)

        # Формируем ответ
        dish = data.get("dish", "—")
        calories = data.get("calories", "—")
        protein = data.get("protein", "—")
        fat = data.get("fat", "—")
        carbs = data.get("carbs", "—")

        text = (
            f"🍽 **{dish}**\n\n"
            f"*На 100 грамм:*\n"
            f"🔥 Калории: {calories} ккал\n"
            f"🥩 Белки: {protein} г\n"
            f"🥑 Жиры: {fat} г\n"
            f"🍞 Углеводы: {carbs} г"
        )
        
        await processing_msg.edit_text(text, parse_mode='Markdown')
        log.info("✅ HANDLE_PHOTO done for user %s", user_id)

    except Exception as e:
        log.error("Handle photo error: %s", e, exc_info=True)
        try:
            await update.message.reply_text("⚠️ Не удалось обработать фото. Попробуйте еще раз или пришлите другое изображение.")
        except Exception as reply_error:
            log.error("Failed to send error message: %s", reply_error)

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    await update.message.reply_text(
        "📸 Пришлите фото блюда, чтобы я мог проанализировать его состав!\n\n"
        "Текстовые сообщения я пока не обрабатываю."
    )

# ───── MAPPING ─────────────────────────────────────────
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

# ───── WEBHOOK ENDPOINT ─────────────────────────────────
@app.post("/", status_code=200)
async def telegram_webhook(req: Request) -> Dict[str, bool]:
    """Endpoint для получения webhook'ов от Telegram"""
    try:
        data = await req.json()
        
        # Инициализируем приложение если еще не сделали
        if not getattr(application, "_initialized", False):
            await application.initialize()
            
        update = Update.de_json(data, application.bot)
        if update:
            await application.process_update(update)
        
        return {"ok": True}
    except Exception as e:
        log.error("Webhook processing error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/")
async def root():
    """Проверка работоспособности сервера"""
    return {"status": "alive", "bot": "food_analyzer"}

@app.get("/health")
async def health_check():
    """Расширенная проверка здоровья сервиса"""
    try:
        # Проверяем подключение к Telegram API
        me = await bot.get_me()
        return {
            "status": "healthy",
            "bot_username": me.username,
            "bot_id": me.id,
            "webhook_url": WEBHOOK_URL
        }
    except Exception as e:
        log.error("Health check failed: %s", e)
        return {"status": "unhealthy", "error": str(e)}

# ───── LIFECYCLE ────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    """Инициализация при запуске"""
    try:
        await application.initialize()
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        log.info("🚀 Webhook установлен: %s", WEBHOOK_URL)

        # Запускаем периодический ping, чтобы не засыпал хостинг
        app.state.keep_alive_task = asyncio.create_task(keep_alive())
        
        # Проверяем подключение
        me = await bot.get_me()
        log.info("✅ Bot connected: @%s (%s)", me.username, me.first_name)
        
    except Exception as e:
        log.error("Startup failed: %s", e, exc_info=True)
        raise

@app.on_event("shutdown")
async def on_shutdown():
    """Очистка при остановке"""
    try:
        # Останавливаем задачу поддержания активности
        task = getattr(app.state, "keep_alive_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await bot.delete_webhook()
        await application.shutdown()
        
        # Закрываем OpenAI клиент
        if hasattr(openai_client, 'close'):
            await openai_client.close()
            
        log.info("🛑 Webhook удалён, бот остановлен")
    except Exception as e:
        log.error("Shutdown error: %s", e, exc_info=True)

# ───── LOCAL RUN ───────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("food_bot_webhook:app", host="0.0.0.0", port=PORT, reload=False)