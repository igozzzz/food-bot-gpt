import os
import io
import re
import base64
from PIL import Image
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
import openai

# Загрузка ключей из .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
openai.api_key = os.getenv("OPENAI_API_KEY")

# Состояния для анкеты
GENDER, AGE, HEIGHT, WEIGHT, WAIST, HIPS, ACTIVITY, HABITS, GOAL, DIET = range(10)

# Хранилища для данных
user_profiles = {}
user_habits = {}
last_meal = {}

# Утилита пересчёта КБЖУ по весу
def scale(value: float, weight: int) -> float:
    return round(value * weight / 100, 1)

# --- Анкета пользователя ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Мужской", "Женский"]]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("👋 Привет! Я — AI‑диетолог. Выбери пол:", reply_markup=markup)
    return GENDER

async def gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["gender"] = update.message.text
    await update.message.reply_text("Сколько тебе лет?", reply_markup=ReplyKeyboardRemove())
    return AGE

async def age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["age"] = int(update.message.text)
    await update.message.reply_text("Рост (см):")
    return HEIGHT

async def height(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["height"] = int(update.message.text)
    await update.message.reply_text("Вес (кг):")
    return WEIGHT

async def weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["weight"] = int(update.message.text)
    await update.message.reply_text("Обхват талии (можно пропустить):")
    return WAIST

async def waist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waist"] = update.message.text
    await update.message.reply_text("Обхват бедер (можно пропустить):")
    return HIPS

async def hips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["hips"] = update.message.text
    keyboard = [["Сидячий", "Легкая активность"], ["Средняя активность", "Высокая активность"]]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Уровень физической активности:", reply_markup=markup)
    return ACTIVITY

async def activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["activity"] = update.message.text
    uid = update.effective_user.id
    user_habits[uid] = []
    keyboard = [["Курю", "Алкоголь"], ["Нет вредных привычек", "Далее"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Есть ли вредные привычки?", reply_markup=markup)
    return HABITS

async def habits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    if text == "Далее":
        context.user_data["habits"] = ", ".join(user_habits[uid]) or "Нет"
        keyboard = [["Похудеть", "Поддерживать вес"], ["Набрать массу", "Улучшить здоровье"]]
        markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text("Какая у тебя цель?", reply_markup=markup)
        return GOAL
    if text == "Нет вредных привычек":
        user_habits[uid] = ["Нет"]
    elif text not in user_habits[uid]:
        user_habits[uid].append(text)
    # ответ после выбора
    await update.message.reply_text(
        f"✅ Отмечено: {', '.join(user_habits[uid])}\nДобавь ещё или нажми \"Далее\""
    )
    return HABITS

async def goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["goal"] = update.message.text
    keyboard = [["Нет ограничений"], ["Без глютена", "Веганство"], ["Пост", "Аллергии"]]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Есть ли ограничения в еде?", reply_markup=markup)
    return DIET

async def diet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["diet"] = update.message.text
    uid = update.effective_user.id
    d = context.user_data
    # BMR и TDEE
    bmr = 10*d["weight"] + 6.25*d["height"] - 5*d["age"] + (5 if d["gender"].startswith("М") else -161)
    factor_map = {"сидячий":1.2, "легкая":1.375, "средняя":1.55, "высокая":1.725}
    activity_key = d["activity"].split()[0].lower()
    tdee = round(bmr * factor_map.get(activity_key, 1.2))
    if "похуд" in d["goal"].lower():
        tdee -= 300
    elif "мас" in d["goal"].lower():
        tdee += 300
    protein = round(d["weight"] * 1.6)
    fat = round(d["weight"] * 0.9)
    carbs = round((tdee - (protein*4 + fat*9)) / 4)
    user_profiles[uid] = {"profile": d, "norma": {"cal":tdee, "protein":protein, "fat":fat, "carb":carbs}}
    await update.message.reply_text(
        f"✅ Анкета сохранена!\n"
        f"🔥 Калории: {tdee} ккал\n"
        f"🥩 Белки: {protein} г\n"
        f"🥑 Жиры: {fat} г\n"
        f"🍞 Углеводы: {carbs} г\n\n"
        f"Теперь пришли мне фото еды для анализа!"
    )
    return ConversationHandler.END

# --- Обработка фото ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    photo = update.message.photo[-1]
    file = await photo.get_file()
    bio = io.BytesIO()
    await file.download_to_memory(out=bio)
    image = Image.open(bio).convert("RGB")
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    img_b64 = base64.b64encode(buffered.getvalue()).decode()
    await update.message.reply_text("🤖 Анализирую фото...")
    resp = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role":"system","content":"Ты — эксперт по питанию. Ответь списком: 1. Название блюд..."},
            {"role":"user","content":[{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{img_b64}"}},{"type":"text","text":"Что на фото?"}]}
        ],
        temperature=0.2, max_tokens=250
    )
    text = resp.choices[0].message.content
    name = re.search(r"1\.\s*(.+)", text)
    cal = re.search(r"(\d+)[^\d]*ккал", text.lower())
    prot = re.search(r"белк.*?(\d+)", text.lower())
    fat = re.search(r"жир.*?(\d+)", text.lower())
    carb = re.search(r"углевод.*?(\d+)", text.lower())
    data = {"dish": name.group(1).strip() if name else "Не распознано",
            "cal": int(cal.group(1)) if cal else 0,
            "prot": int(prot.group(1)) if prot else 0,
            "fat": int(fat.group(1)) if fat else 0,
            "carb": int(carb.group(1)) if carb else 0}
    last_meal[uid] = data
    await update.message.reply_text(
        f"🍽 Блюдо: {data['dish']}\n"
        f"🔥 Калории: {data['cal']} ккал / 100 г\n"
        f"🥩 Белки: {data['prot']} г\n"
        f"🥑 Жиры: {data['fat']} г\n"
        f"🍞 Углеводы: {data['carb']} г\n\n"
        f"Если знаешь вес — напиши мне его цифрой (грамм)."
    )

async def handle_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in last_meal or not update.message.text.isdigit():
        return
    weight = int(update.message.text)
    meal = last_meal[uid]
    norm = user_profiles.get(uid, {}).get("norma", {})
    cal = scale(meal['cal'], weight)
    prot = scale(meal['prot'], weight)
    fat = scale(meal['fat'], weight)
    carb = scale(meal['carb'], weight)
    pct = lambda v,k: round(v/k*100,1) if k else 0
    comments = []
    goal = user_profiles.get(uid, {}).get('profile', {}).get('goal','').lower()
    if norm:
        if pct(fat, norm['fat'])>40 and 'похуд' in goal:
            comments.append('Много жиров для похудения')
    await update.message.reply_text(
        f"🍽 {meal['dish']} — {weight} г\n"
        f"🔥 {cal} ккал ({pct(cal,norm.get('cal'))}%)\n"
        f"🥩 {prot} г ({pct(prot,norm.get('protein'))}%)\n"
        f"🥑 {fat} г ({pct(fat,norm.get('fat'))}%)\n"
        f"🍞 {carb} г ({pct(carb,norm.get('carb'))}%)\n\n"
        + ('\n'.join(comments) if comments else 'Нет рекомендаций')
    )

# --- Запуск бота ---

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            GENDER:[MessageHandler(filters.TEXT & ~filters.COMMAND, gender)],
            AGE:[MessageHandler(filters.TEXT & ~filters.COMMAND, age)],
            HEIGHT:[MessageHandler(filters.TEXT & ~filters.COMMAND, height)],
            WEIGHT:[MessageHandler(filters.TEXT & ~filters.COMMAND, weight)],
            WAIST:[MessageHandler(filters.TEXT & ~filters.COMMAND, waist)],
            HIPS:[MessageHandler(filters.TEXT & ~filters.COMMAND, hips)],
            ACTIVITY:[MessageHandler(filters.TEXT & ~filters.COMMAND, activity)],
            HABITS:[MessageHandler(filters.TEXT & ~filters.COMMAND, habits)],
            GOAL:[MessageHandler(filters.TEXT & ~filters.COMMAND, goal)],
            DIET:[MessageHandler(filters.TEXT & ~filters.COMMAND, diet)]
        },
        fallbacks=[]
    )
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_weight))
    print('✅ GPT Vision бот с диетологом запущен...')
    app.run_polling()

if __name__=='__main__':
    main()
