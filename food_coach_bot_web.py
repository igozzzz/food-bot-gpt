# food_bot_webhook.py
import os, io, json, base64, logging, httpx, uvicorn
from fastapi import FastAPI, Request
from telegram import Update, Bot, File
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv
from PIL import Image
import openai

# ─── Логирование ───────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("food_bot")

# ─── Загрузка ENV ──────────────────────────────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
PORT           = int(os.getenv("PORT", 10000))
MAX_FILE_SIZE  = 10 * 1024 * 1024  # 10 MB

if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL]):
    log.error("Не заданы обязательные ENV-переменные")
    raise RuntimeError("TELEGRAM_TOKEN, OPENAI_API_KEY и WEBHOOK_URL обязательны")

# ─── Клиенты ───────────────────────────────────
bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
openai_client = openai.AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    http_client=httpx.AsyncClient(timeout=30.0),
)

# ─── Хелпер OpenAI ────────────────────────────
async def analyse_image(img_b64: str) -> dict:
    resp = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=200,
        messages=[
            {"role":"system","content":
             "Ты — нутрициолог. Верни JSON-объект с полями: "
             "dish, calories, protein, fat, carbs — на 100 г. "
             "Если не уверен — ставь \"—\"."},
            {"role":"user","content":[
                {"type":"image_url","image_url":
                    {"url":f"data:image/jpeg;base64,{img_b64}"}},
                {"type":"text","text":"Проанализируй фото блюда."}
            ]},
        ],
    )
    content = resp.choices[0].message.content
    if isinstance(content, dict):
        return content
    try:
        return json.loads(content)
    except Exception as e:
        log.error("Не удалось распарсить JSON: %s", e)
        raise

# ─── Телеграм-хендлеры ────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Пришли мне фото блюда — и я верну название и КБЖУ на 100 г."
    )
    log.info("User %s started", update.effective_user.id)

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        photo = update.message.photo[-1]
        if photo.file_size and photo.file_size > MAX_FILE_SIZE:
            return await update.message.reply_text("⚠️ Фото >10 МБ, пришлите поменьше.")
        tg_file: File = await photo.get_file()
        buf = io.BytesIO()
        await tg_file.download_to_memory(out=buf)
        raw = buf.getvalue()

        img = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        await update.message.reply_text("🤖 Анализирую фото…")
        data = await analyse_image(img_b64)

        dish = data.get("dish","—")
        cal  = data.get("calories","—")
        p    = data.get("protein","—")
        f    = data.get("fat","—")
        c    = data.get("carbs","—")

        await update.message.reply_text(
            f"🍽 {dish}\n"
            f"🔥 {cal} ккал / 100 г\n"
            f"🥩 {p} г   🥑 {f} г   🍞 {c} г"
        )
        log.info("Answered for user %s: %s", update.effective_user.id, dish)
    except Exception as e:
        log.error("Error handling photo: %s", e, exc_info=True)
        await update.message.reply_text("⚠️ Не удалось обработать фото. Попробуйте ещё.")

application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ─── Webhook endpoint ─────────────────────────
@app.post("/", status_code=200)
async def telegram_webhook(req: Request):
    data = await req.json()
    if not getattr(application, "_initialized", False):
        await application.initialize()
    upd = Update.de_json(data, application.bot)
    await application.process_update(upd)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status":"ok"}

# ─── Startup / Shutdown ───────────────────────
@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    log.info("Webhook set to %s", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await application.shutdown()
    log.info("Webhook deleted")

# ─── Локальный запуск ─────────────────────────
if __name__ == "__main__":
    uvicorn.run("food_bot_webhook:app", host="0.0.0.0", port=PORT)
