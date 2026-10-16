# language: Python, file: bot.py, target: Render Web Service, python-telegram-bot v20
# Кофейня "Шамиля 26А", Махачкала. Тел: 79887882901
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8443))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не задан в переменных окружения")

# ---------- МЕНЮ ----------

COFFEE_ITEMS = [
    ("Эспрессо", 120),
    ("Американо", 150),
    ("Капучино", 180),
    ("Латте", 190),
    ("Флэт Уайт", 200),
    ("Раф", 220),
    ("Мокачино", 230),
]

COLD_ITEMS = [
    ("Колд Брю", 200),
    ("Айс Латте", 210),
    ("Айс Американо", 170),
    ("Матча Латте", 250),
    ("Фраппе", 240),
    ("Лимонад", 150),
]

BEANS_ITEMS = [
    ("Арабика Эфиопия", 450, 1600),
    ("Арабика Колумбия", 420, 1500),
    ("Робуста", 350, 1200),
    ("Бленд (Арабика+Робуста)", 380, 1300),
    ("Декаф", 500, 1800),
]

CONTACT_TEXT = (
    "📍 *Кофейня «Шамиля 26А»*\n"
    "Адрес: г. Махачкала, ул. Шамиля 26А\n"
    "📞 Телефон: +7 988 788-29-01\n\n"
    "Режим работы: ежедневно 08:00 – 22:00\n"
    "Самовывоз и доставка по городу."
)

# ---------- КЛАВИАТУРЫ ----------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("☕ Кофе", callback_data="coffee")],
        [InlineKeyboardButton("🧊 Холодные напитки", callback_data="cold")],
        [InlineKeyboardButton("🫘 Зерна", callback_data="beans")],
        [InlineKeyboardButton("📞 Контакты", callback_data="contacts")],
    ]
    return InlineKeyboardMarkup(keyboard)

def back_keyboard() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
    return InlineKeyboardMarkup(keyboard)

# ---------- ТЕКСТЫ ----------

def coffee_text() -> str:
    lines = ["☕ *Кофе*\n"]
    for name, price in COFFEE_ITEMS:
        lines.append(f"• {name} — {price} ₽")
    lines.append("\n_Цены указаны в рублях._")
    return "\n".join(lines)

def cold_text() -> str:
    lines = ["🧊 *Холодные напитки*\n"]
    for name, price in COLD_ITEMS:
        lines.append(f"• {name} — {price} ₽")
    lines.append("\n_Цены указаны в рублях._")
    return "\n".join(lines)

def beans_text() -> str:
    lines = ["🫘 *Зерна на продажу*\n"]
    for name, price250, price1kg in BEANS_ITEMS:
        lines.append(f"• {name}\n   250 г — {price250} ₽ | 1 кг — {price1kg} ₽")
    lines.append("\n_Цены указаны в рублях._")
    return "\n".join(lines)

# ---------- ХЕНДЛЕРЫ ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Добро пожаловать в кофейню «Шамиля 26А»!\n"
        "Выберите категорию:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "coffee":
        await query.edit_message_text(
            coffee_text(), reply_markup=back_keyboard(), parse_mode="Markdown"
        )
    elif data == "cold":
        await query.edit_message_text(
            cold_text(), reply_markup=back_keyboard(), parse_mode="Markdown"
        )
    elif data == "beans":
        await query.edit_message_text(
            beans_text(), reply_markup=back_keyboard(), parse_mode="Markdown"
        )
    elif data == "contacts":
        await query.edit_message_text(
            CONTACT_TEXT, reply_markup=back_keyboard(), parse_mode="Markdown"
        )
    elif data == "back":
        await query.edit_message_text(
            "Выберите категорию:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Команды:\n/start — главное меню\n/help — эта справка"
    )

# ---------- ЗАПУСК ----------

def main() -> None:
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))

    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/{TOKEN}"
        logger.info("Запуск webhook на %s", webhook_url)
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=webhook_url,
        )
    else:
        logger.info("RENDER_EXTERNAL_URL не задан, запуск polling")
        application.run_polling()

if __name__ == "__main__":
    main()
