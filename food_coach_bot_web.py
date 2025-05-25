import os, io, base64, json, logging
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError
from openai import AsyncOpenAI
from telegram import Bot, Update
from telegram.ext import (
    ApplicationBuilder, Application, CommandHandler,
    MessageHandler, ContextTypes, filters,
)
import httpx, uvicorn

# ─────────── конфигурация ───────────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")        # https://<service>.onrender.com/
PORT           = int(os.getenv("PORT", 8000))
MAX_SIZE       = 20 * 1024 * 1024               # 20 МБ

if not all([TELEGRAM_TOKEN, OPENAI_API_KEY, WEBHOOK_URL]):
    raise RuntimeError("Нужно задать TELEGRAM_TOKEN, OPENAI_API_KEY и WEBHOOK_URL в .env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("foodbot")

# ─────────── инициализация клиентов ───────────
bot: Bot = Bot(TELEGRAM_TOKEN)
app  = FastAPI()
tg_app: Application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
ai   = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=httpx.AsyncClient(timeout=30))

# ─────────── Telegram-handlers ───────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Пришли фото блюда — я дам название\n"
        "и 🤓 КБЖУ на 100 грамм."
    )

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    if photo.file_size > MAX_SIZE:
        await update.message.reply_text("⚠️ Фото > 20 МБ, пришли поменьше.")
        return

    # загрузка файла
    f = await photo.get_file()
    async with httpx.AsyncClient(timeout=30) as hc:
        r = await hc.get(f.file_path)
        r.raise_for_status()
        img_bytes = r.content

    # base64
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=85)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    await update.message.reply_text("🤖 Анализирую…")

    # запрос к GPT-4o
    try:
        resp = await ai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content":
                 "Ты нутрициолог. Верни только JSON с ключами: "
                 "dish, calories, protein, fat, carbs."},
                {"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": "Что на фото? КБЖУ/100 г."},
                ]},
            ],
            temperature=0.2,
            max_tokens=200,
        )
    except Exception as e:
        log.error("OpenAI error: %s", e)
        await update.message.reply_text("⚠️ Ошибка анализа. Попробуй ещё.")
        return

    raw = resp.choices[0].message.content.strip()
    # убираем ```json ... ```
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        raw = raw.lstrip("json").strip()

    try:
        data = json.loads(raw)
        dish = data.get("dish", "не распознано")
        cal  = data.get("calories", "—")
        p, f, c = data.get("protein", "—"), data.get("fat", "—"), data.get("carbs", "—")
    except Exception as e:
        log.error("JSON parse error: %s, raw: %s", e, raw)
        await update.message.reply_text("⚠️ Не смог распознать ответ ИИ.")
        return

    await update.message.reply_text(
        f"🍽 {dish}\n"
        f"🔥 {cal} ккал / 100 г\n"
        f"🥩 Белки: {p} г\n"
        f"🥑 Жиры: {f} г\n"
        f"🍞 Углеводы: {c} г"
    )

# регистрация
tg_app.add_handler(CommandHandler("start", cmd_start))
tg_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ─────────── FastAPI endpoints ───────────
@app.post("/", status_code=200)
async def webhook(req: Request):
    update = Update.de_json(await req.json(), bot)
    await tg_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def ping():  # health-check
    return {"status": "ok"}

# ─────────── стартап/шатаун ───────────
@app.on_event("startup")
async def startup():
    await tg_app.initialize()
    await bot.set_webhook(WEBHOOK_URL)
    log.info("Webhook set to %s", WEBHOOK_URL)

@app.on_event("shutdown")
async def shutdown():
    await bot.delete_webhook()
    await tg_app.shutdown()

# ─────────── локальный запуск ───────────
if __name__ == "__main__":
    uvicorn.run("food_coach_bot_web:app", host="0.0.0.0", port=PORT)
