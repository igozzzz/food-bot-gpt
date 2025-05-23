# food_coach_bot.py — полный код для Telegram-бота-диетолога
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

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
openai.api_key = os.getenv("OPENAI_API_KEY")

# --- States ---
GENDER, AGE, HEIGHT, WEIGHT, WAIST, HIPS, ACTIVITY, HABITS, GOAL, DIET = range(10)
user_profiles = {}
user_habits = {}
last_meal = {}

def scale(v, w): return round(v * w / 100, 1)

# --- Анкета ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Мужской", "Женский"]]
    await update.message.reply_text("👋 Привет! Я — AI-диетолог. Выбери пол:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
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
    await update.message.reply_text("Уровень физической активности:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return ACTIVITY

async def activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["activity"] = update.message.text
    user_habits[update.effective_user.id] = []
    keyboard = [["Курю", "Алкоголь"], ["Нет вредных привычек", "Далее"]]
    await update.message.reply_text("Есть ли вредные привычки?", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return HABITS

async def habits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    if text == "Далее":
        context.user_data["habits"] = ", ".join(user_habits[uid]) or "Нет"
        keyboard = [["Похудеть", "Поддерживать вес"], ["Набрать массу", "Улучшить здоровье"]]
        await update.message.reply_text("Какая у тебя цель?", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
        return GOAL
    if text == "Нет вредных привычек":
        user_habits[uid] = ["Нет"]
    elif text not in user_habits[uid]:
        user_habits[uid].append(text)
    await update.message.reply_text(f"✅ Отмечено: {', '.join(user_habits[uid])}
Добавь ещё или нажми 'Далее'")
    return HABITS

async def goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["goal"] = update.message.text
    keyboard = [["Нет ограничений"], ["Без глютена", "Веганство"], ["Пост", "Аллергии"]]
    await update.message.reply_text("Есть ли ограничения в еде?", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return DIET

async def diet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["diet"] = update.message.text
    user_id = update.effective_user.id
    d = context.user_data
    bmr = 10*d["weight"] + 6.25*d["height"] - 5*d["age"] + (5 if d["gender"].startswith("М") else -161)
    factor = {"сидячий": 1.2, "легкая": 1.375, "средняя": 1.55, "высокая": 1.725}
    tdee = round(bmr * factor.get(d["activity"].split()[0].lower(), 1.2))
    if "похуд" in d["goal"].lower(): tdee -= 300
    elif "мас" in d["goal"].lower(): tdee += 300
    prot = round(d["weight"] * 1.6)
    fat = round(d["weight"] * 0.9)
    carb = round((tdee - prot*4 - fat*9)/4)
    user_profiles[user_id] = {"profile": d, "norma": {"cal": tdee, "protein": prot, "fat": fat, "carb": carb}}
    await update.message.reply_text(
        f"✅ Анкета сохранена!
"
        f"🔥 Калории: {tdee} ккал
🥩 Белки: {prot} г
🥑 Жиры: {fat} г
🍞 Углеводы: {carb} г

"
        f"Теперь пришли мне фото еды для анализа!"
    )
    return ConversationHandler.END
