import os, io, json, base64, logging, httpx
from fastapi import FastAPI, Request
from telegram import Update, Bot, File
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv
from PIL import Image
import openai, uvicorn

# ─────── Логирование ───────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("food_bot")

# ─────── ENV ───────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
PORT           = int(os.getenv("PORT", 8000))
MAX_FILE_SIZE  = 10 * 1024 * 1024  # 10 МБ

if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL]):
    raise RuntimeError("Нужно задать TELEGRAM_TOKEN, OPENAI_API_KEY и WEBHOOK_URL")

# ─────── Клиенты ───────
bot = Bot(token=TELEGRAM_TOKEN)
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app = FastAPI()
openai_client = openai.AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    http_client=httpx.AsyncClient(timeout=30.0),
)

# ─────── OpenAI Helper ───────
async def analyse_image(img_b64: str) -> dict:
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=200,
            messages=[
                {"role":"system","content":
                 "Ты нутрициолог. Верни JSON-объект с ключами dish, calories, protein, fat, carbs. "
                 "Значения — на 100 г. Если не уверен — оцени примерно."},
                {"role":"user","content":[
                    {"type":"image_url", "image_url":{"url":f"data:image/jpeg;base64,{img_b64}"}},
                    {"type":"text","text":"Проанализируй фото блюда."}
                ]}
            ],
        )
        return resp.choices[0].message.content
    except openai.error.RateLimitError:
        raise RuntimeError("Слишком много запросов к OpenAI, попробуйте чуть позже")
    except Exception as e:
        raise RuntimeError(f"OpenAI error: {e}")

# ─────── Telegram Handlers ───────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Пришли фото блюда — скажу название и КБЖУ на 100 г.")

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        photo = update.message.photo[-1]
        if photo.file_size > MAX_FILE_SIZE:
            return await update.message.reply_text("⚠️ Фото >10 МБ, пришлите поменьше.")
        tg_file: File = await photo.get_file()
        buf = io.BytesIO()
        await tg_file.download_to_memory(out=buf)
        raw = buf.getvalue()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO(); img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        await update.message.reply_text("🤖 Анализирую фото…")
        data = await analyse_image(img_b64)

        dish = data.get("dish","—"); cal = data.get("calories","—")
        prot = data.get("protein","—"); fat = data.get("fat","—"); carb = data.get("carbs","—")
        await update.message.reply_text(
            f"🍽 {dish}\n🔥 {cal} ккал/100 г\n🥩 {prot} г  🥑 {fat} г  🍞 {carb} г"
        )

    except Exception as e:
        log.error("Handle photo error: %s", e, exc_info=True)
        await update.message.reply_text("⚠️ Не удалось обработать фото. Попробуйте ещё.")

# ─────── Подключение Handlers ───────
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ─────── Webhook Endpoint ───────
@app.post("/", status_code=200)
async def telegram_webhook(req: Request):
    data = await req.json()
    if not getattr(application, "_initialized", False):
        await application.initialize()
    upd = Update.de_json(data, application.bot)
    try:
        await application.process_update(upd)
    except Exception as e:
        log.error("❌ Error in process_update: %s", e, exc_info=True)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status":"alive"}

# ─────── Startup / Shutdown ───────
@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True, max_connections=1)
    log.info("Webhook установлен: %s", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await application.shutdown()
    log.info("Webhook удалён")

# ─────── Локальный запуск ───────
if __name__ == "__main__":
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
