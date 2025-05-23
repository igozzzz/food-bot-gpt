import os
import io
import re
import base64

from PIL import Image
from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
import openai

# --- Загрузка переменных окружения ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
openai.api_key = os.getenv("OPENAI_API_KEY")

# --- Состояния для ConversationHandler ---
GENDER, AGE, HEIGHT, WEIGHT, WAIST, HIPS, ACTIVITY, HABITS, GOAL, DIET = range(10)

# --- Хранилища данных пользователей ---
user_profiles = {}
user_habits    = {}
last_meal      = {}

# --- Утилита пересчёта на порцию ---
def scale(value: float, weight: int) -> float:
    return round(value * weight / 100, 1)

# --- Обработчики анкеты ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Мужской", "Женский"]]
    await update.message.reply_text(
        "👋 Привет! Я — AI-диетолог. Выбери пол:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return GENDER

async def gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["gender"] = update.message.text
    await update.message.reply_text("Сколько тебе лет?", reply_markup=ReplyKeyboardRemove())
    return AGE

async def age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("Пожалуйста, введи возраст цифрами.")
        return AGE
    context.user_data["age"] = int(txt)
    await update.message.reply_text("Рост (см):")
    return HEIGHT

async def height(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("Пожалуйста, введи рост цифрами (см).")
        return HEIGHT
    context.user_data["height"] = int(txt)
    await update.message.reply_text("Вес (кг):")
    return WEIGHT

async def weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("Пожалуйста, введи вес цифрами (кг).")
        return WEIGHT
    context.user_data["weight"] = int(txt)
    await update.message.reply_text("Обхват талии (см, можно пропустить):")
    return WAIST

async def waist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waist"] = update.message.text.strip() or "—"
    await update.message.reply_text("Обхват бедер (см, можно пропустить):")
    return HIPS

async def hips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["hips"] = update.message.text.strip() or "—"
    keyboard = [["Сидячий", "Легкая активность"], ["Средняя активность", "Высокая активность"]]
    await update.message.reply_text(
        "Уровень физической активности:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return ACTIVITY

async def activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["activity"] = update.message.text
    user_habits[update.effective_user.id] = []
    keyboard = [["Курю", "Алкоголь"], ["Нет вредных привычек", "Далее"]]
    await update.message.reply_text(
        "Есть ли вредные привычки?",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return HABITS

async def habits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    txt = update.message.text
    if txt == "Далее":
        context.user_data["habits"] = ", ".join(user_habits[uid]) or "Нет"
        keyboard = [["Похудеть", "Поддерживать вес"], ["Набрать массу", "Улучшить здоровье"]]
        await update.message.reply_text(
            "Какая у тебя цель?",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return GOAL

    if txt == "Нет вредных привычек":
        user_habits[uid] = ["Нет"]
    elif txt not in user_habits[uid]:
        user_habits[uid].append(txt)

    await update.message.reply_text(
        f"✅ Отмечено: {', '.join(user_habits[uid])}\n"
        f"Добавь ещё или нажми \"Далее\""
    )
    return HABITS

async def goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["goal"] = update.message.text
    keyboard = [["Нет ограничений"], ["Без глютена", "Веганство"], ["Пост", "Аллергии"]]
    await update.message.reply_text(
        "Есть ли ограничения в питании?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return DIET

async def diet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["diet"] = update.message.text
    uid = update.effective_user.id
    d   = context.user_data

    # BMR + активность + цель
    bmr = 10*d["weight"] + 6.25*d["height"] - 5*d["age"] + (5 if d["gender"].startswith("М") else -161)
    factors = {"сидячий":1.2, "легкая":1.375, "средняя":1.55, "высокая":1.725}
    factor = factors.get(d["activity"].split()[0].lower(), 1.2)
    tdee   = round(bmr * factor)
    if "похуд" in d["goal"].lower(): tdee -= 300
    elif "мас" in d["goal"].lower(): tdee += 300

    protein = round(d["weight"] * 1.6)
    fat     = round(d["weight"] * 0.9)
    carb    = round((tdee - (protein*4 + fat*9)) / 4)

    user_profiles[uid] = {
        "profile": d,
        "norma": {"cal": tdee, "protein": protein, "fat": fat, "carb": carb}
    }

    await update.message.reply_text(
        f"✅ Анкета сохранена!\n"
        f"🔥 Калории: {tdee} ккал\n"
        f"🥩 Белки: {protein} г\n"
        f"🥑 Жиры: {fat} г\n"
        f"🍞 Углеводы: {carb} г\n\n"
        f"Теперь отправь фото еды для анализа!"
    )
    return ConversationHandler.END

# --- Обработка фото и GPT-анализ ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    photo = update.message.photo[-1]
    file  = await photo.get_file()
    bio   = io.BytesIO()
    await file.download_to_memory(out=bio)
    image = Image.open(bio).convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    img_b64 = base64.b64encode(buffer.getvalue()).decode()

    await update.message.reply_text("🤖 Анализирую фото...")

    resp = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role":"system","content":
             "Ты — эксперт по питанию. Ответь строго по шаблону:\n"
             "1. Название блюда\n2. Калории на 100 г\n"
             "3. Белки, Жиры, Углеводы\nНа русском языке."},
            {"role":"user","content":[
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{img_b64}"}},
                {"type":"text","text":"Что на фото?"}
            ]}
        ],
        temperature=0.2,
        max_tokens=300
    )

    text = resp.choices[0].message.content
    name = re.search(r"1\.\s*(.+)", text)
    cal  = re.search(r"(\d+)[^\d]*ккал", text.lower())
    prot = re.search(r"белк.*?(\d+)", text.lower())
    fat  = re.search(r"жир.*?(\d+)", text.lower())
    carb = re.search(r"углевод.*?(\d+)", text.lower())

    data = {
        "dish": name.group(1).strip() if name else "Не распознано",
        "cal":  int(cal.group(1)) if cal else 0,
        "prot": int(prot.group(1)) if prot else 0,
        "fat":  int(fat.group(1)) if fat else 0,
        "carb": int(carb.group(1)) if carb else 0
    }
    last_meal[uid] = data

    await update.message.reply_text(
        f"🍽 Блюдо: {data['dish']}\n"
        f"🔥 Калории: {data['cal']} ккал / 100 г\n"
        f"🥩 Белки: {data['prot']} г\n"
        f"🥑 Жиры: {data['fat']} г\n"
        f"🍞 Углеводы: {data['carb']} г\n\n"
        f"Если знаешь вес порции, напиши его в граммах."
    )

# --- Пересчёт и советы с корректными % ---
async def handle_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    txt  = update.message.text.strip()
    if uid not in last_meal or not txt.isdigit():
        return
    weight = int(txt)
    meal   = last_meal[uid]
    norma  = user_profiles.get(uid, {}).get("norma", {})

    # расчёт
    cal  = scale(meal["cal"],  weight)
    prot = scale(meal["prot"], weight)
    fat  = scale(meal["fat"],  weight)
    carb = scale(meal["carb"], weight)

    pct = lambda v, n: round(v / n * 100, 1) if n else 0

    lines = [f"🍽 {meal['dish']} — {weight} г"]

    # калории
    line = f"🔥 {cal} ккал"
    if norma.get("cal"):
        line += f" ({pct(cal, norma['cal'])}%)"
    lines.append(line)

    # белки
    line = f"🥩 {prot} г"
    if norma.get("protein"):
        line += f" ({pct(prot, norma['protein'])}%)"
    lines.append(line)

    # жиры
    line = f"� 🥑 {fat} г"
    if norma.get("fat"):
        line += f" ({pct(fat, norma['fat'])}%)"
    lines.append(line)

    # углеводы
    line = f"🍞 {carb} г"
    if norma.get("carb"):
        line += f" ({pct(carb, norma['carb'])}%)"
    lines.append(line)

    # советы
    comments = []
    goal = user_profiles.get(uid, {}).get("profile", {}).get("goal","").lower()
    if norma.get("fat") and pct(fat, norma["fat"]) > 40 and "похуд" in goal:
        comments.append("Много жиров для похудения.")
    if norma.get("protein") and pct(prot, norma["protein"]) < 15:
        comments.append("Низкий белок — добавь яйца или бобовые.")
    if norma.get("carb") and pct(carb, norma["carb"]) > 60:
        comments.append("Много углеводов — подумай о клетчатке.")

    if comments:
        lines.append("")  # пустая строка
        lines.extend(comments)

    await update.message.reply_text("\n".join(lines))

# --- Сборка бота и запуск ---
def main():
    app = ApplicationBuilder() \
        .token(TELEGRAM_TOKEN) \
        .build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            GENDER:   [MessageHandler(filters.TEXT & ~filters.COMMAND, gender)],
            AGE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, age)],
            HEIGHT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, height)],
            WEIGHT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, weight)],
            WAIST:    [MessageHandler(filters.TEXT & ~filters.COMMAND, waist)],
            HIPS:     [MessageHandler(filters.TEXT & ~filters.COMMAND, hips)],
            ACTIVITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, activity)],
            HABITS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, habits)],
            GOAL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, goal)],
            DIET:     [MessageHandler(filters.TEXT & ~filters.COMMAND, diet)],
        },
        fallbacks=[]
    )

    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_weight))

    print("✅ GPT Vision бот с диетологом запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
    