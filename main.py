
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ReplyKeyboardMarkup

from dotenv import load_dotenv
import requests
import os


# --- Настройки ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

TOKEN_ADDRESS = "0x597d9816Ddb9624824591360180A70BE6fD26182"
CHAIN = "bsc"
PAIR_API_URL = f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN}/{TOKEN_ADDRESS}"
N = 12  # 12 цен за час (каждые 5 минут)
PRICE_CHECK_INTERVAL = int(3600/N)  # 5 минут
REPORT_INTERVAL = 4 * 60 * 60  # 4 часа
BUTTON_LABEL = "Отчёт"
PERCENT = 1.03


# --- Глобальные переменные ---
prices = []
caps = []
subscribers = set()
last_report_price = 1
last_report_cap = 1


# --- Обновить значения последнего отчёта ---
def update_last_report_date():
    global last_report_price, last_report_cap
    new_report_price, new_report_cap = fetch_token_data()
    if new_report_cap:
        last_report_price = new_report_price
        last_report_cap = new_report_cap

# --- Получение цены и капитализации ---
def fetch_token_data() -> tuple[float, float] | tuple[None, None]:
    try:
        resp = requests.get(PAIR_API_URL, timeout=10)
        data = resp.json()
        # Dexscreener может возвращать несколько пар, берём первую
        pair = data["pairs"][0]
        price = float(pair["priceUsd"])
        cap = float(pair.get("fdv", 0))  # fully diluted valuation
        return price, round(cap / 1_000_000, 3)
    except Exception as e:
        return None, None

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        return
    chat_id = update.effective_chat.id
    subscribers.add(chat_id)
    price, cap = fetch_token_data()
    if price:
        msg = (
            f"Мониторинг мемкоина\n"
            f"💸: ${format_price(price)}\n"
            f"💰: ${cap}\n"
        )
    else:
        msg = "Не удалось получить данные"
    keyboard_label = [[BUTTON_LABEL]]
    reply_markup = ReplyKeyboardMarkup(keyboard_label, resize_keyboard=True)
    await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=reply_markup, parse_mode="HTML")

def format_price(price: float) -> str:
    str_price = str(price)
    _, frac_part = str_price.split('.')
    # Убираем ведущие нули в дробной части, чтобы найти значащие цифры
    leading_zeros_len = len(frac_part) - len(frac_part.lstrip('0'))
    return f"0.{"0"*leading_zeros_len}<b>{frac_part.lstrip('0')}</b>"


# --- Регулярный отчёт ---
async def send_report(app):
    global last_report_price, last_report_cap
    price, cap = fetch_token_data()
    if price is None:
        msg = "Не удалось получить текущие данные для отчёта"
    elif last_report_price is None:
        msg = "Не удалось получить прошлые данные для отчёта"
    else:
        price_change_percent = ((price - last_report_price) / last_report_price) * 100
        icon = "🟢" if price_change_percent > 0 else "🔴" if price_change_percent < 0 else ""
        msg = (
            f"Отчёт {icon} {price_change_percent:.2f}%\n"
            f"💸: ${format_price(price)}\n"
            f"💰: ${cap}\n"
        )
        last_report_cap = cap
        last_report_price = price
    
    for chat_id in subscribers:
        await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")  
    
    update_last_report_date()

# --- Получить среднюю цену
def get_avg_price():
    return sum(prices) / len(prices)

# --- Получить данные и в случае чего отправить если ---
async def check_and_notify(app):
    price, cap = fetch_token_data()
    if price:
        avg_price = get_avg_price()
        prices.append(price)
        caps.append(cap)
        prices.pop(0)
        caps.pop(0)
        if price > avg_price * PERCENT:
            price_change_percent = ((price - avg_price) / avg_price) * 100
            msg = (
                f"🟢🟢🟢🟢🟢🟢\n",
                f"Цена выросла на <b>{avg_price}%<b> за час!\n"
                f"💸: ${format_price(price)}\n"
                f"💰: ${cap}\n"
            )
            for chat_id in subscribers:
                await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")

# --- Обработка сообщений для нажатия кнопки ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text # type: ignore
    if text == BUTTON_LABEL:
        await send_report(context.application)


# --- Основная функция ---
def main():
    if TELEGRAM_TOKEN is None:
        raise ValueError("TELEGRAM_TOKEN is not set in environment variables.")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Добавить начальные значения для отчёта
    update_last_report_date()

    # Инициализация средней цены
    price, cap = fetch_token_data()
    if price:
        for _ in range(N):
            prices.append(price)
            caps.append(cap)

    app.run_polling()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: send_report(app), "interval", seconds=REPORT_INTERVAL)
    scheduler.add_job(lambda: check_and_notify(app), "interval", seconds=PRICE_CHECK_INTERVAL)
    scheduler.start()


if __name__ == "__main__":
    main()

