
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, 
    MessageHandler, filters, CallbackContext, 
    ConversationHandler
)
from telegram import Update, ReplyKeyboardMarkup
from dotenv import load_dotenv
from loguru import logger
import requests
import sys
import os


# --- Настроики логов ---
logger.remove()
logger.add(sys.stdout, format="<level>{message}</level>")

# --- Настройки ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


N = 12  # 12 цен за час (каждые 5 минут)
PRICE_CHECK_INTERVAL = int(3600/N)  # 5 минут
REPORT_INTERVAL = 60 * 60 * 2  # 2 часа
BUTTON_REPORT_LABEL = "Отчёт"
BUTTON_ADD_TOKEN_LABEL = "Добавить токен"
BUTTON_CANCEL_LABEL = "Отмена"
PERCENT = 1.03


class Token:
    def __init__(self, name: str, address: str, chain: str) -> None:
        self.name = name
        self.address: str = address
        self.chain: str = chain

        self.prices: list[float] = []
        self.caps: list[float] = []
        self.last_report_price: float = None # type: ignore
        self.last_report_cap: float = None # type: ignore

        price, cap = self.fetch_token_data()
        if price and cap:
            for _ in range(N):
                self.prices.append(price)
                self.caps.append(cap)
            self.last_report_cap = cap
            self.last_report_price = price
    
    # --- Получение цены и капитализации ---
    def fetch_token_data(self) -> tuple[float, float] | tuple[None, None]:
        try:
            resp = requests.get(get_api_url(self), timeout=10)
            data = resp.json()
            # Dexscreener может возвращать несколько пар, берём первую
            pair = data["pairs"][0]
            price = float(pair["priceUsd"])
            cap = float(pair.get("fdv", 0))  # fully diluted valuation
            return price, round(cap / 1_000_000, 3)
        except Exception as e:
            return None, None
    
    def get_price_change_percent(self, price: float):
        return ((price - self.last_report_price) / self.last_report_price) * 100
    
    # --- Получить среднюю цену
    def get_avg_price(self):
        return sum(self.prices) / len(self.prices)

# --- Класс пользователя ---
class User:
    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id
        self.tokens_for_tracking: set[Token] = set()
    
    def add_token(self, token: Token) -> None:
        self.tokens_for_tracking.add(token)

def get_api_url(token: Token) -> str:
    return f"https://api.dexscreener.com/latest/dex/pairs/{token.chain}/{token.address}"

# --- Глобальные переменные ---
users: list[User] = []
# Состояния
GETTING_ADDRESS, GETTING_CHAIN, GETTING_NAME = range(3)

def add_user(new_user: User) -> bool:
    global users

    for user in users:
        if new_user.chat_id == user.chat_id:
            break
    else:
        users.append(new_user)
        return True

    return False

# Клавиатура отмены
def get_cancel_keyboard():
    return ReplyKeyboardMarkup([[BUTTON_CANCEL_LABEL]], resize_keyboard=True)

def get_main_keyboard():
    keyboard_label = [[BUTTON_REPORT_LABEL, BUTTON_ADD_TOKEN_LABEL]]
    return ReplyKeyboardMarkup(keyboard_label, resize_keyboard=True)

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        return
    
    chat_id = update.effective_chat.id
    
    if (add_user(User(chat_id))):
        msg = "Привет в первый раз!"
    else:
        msg = "И снова здравствуй!"

    
    await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=get_main_keyboard(), parse_mode="HTML")

# --- Форматирует цену ---
def format_price(price: float) -> str:
    str_price = str(price)
    _, frac_part = str_price.split('.')
    # Убираем ведущие нули в дробной части, чтобы найти значащие цифры
    leading_zeros_len = len(frac_part) - len(frac_part.lstrip('0'))
    return f"0.{"0"*leading_zeros_len}<b>{frac_part.lstrip('0')}</b>"

# --- Регулярный отчёт ---
async def send_report(context: CallbackContext):
    logger.info("🕓 Отправка отчёта")

    for user in users:
        msg = ""
        for token in user.tokens_for_tracking:
            price, cap = token.fetch_token_data()
            msg += f"<b>{token.name}</b>:\n"
            if price is None or cap is None:
                msg += (
                    "\nНе удалось получить текущие данные"
                )
            elif token.last_report_price is None:
                msg += (
                    "Не удалось получить прошлые данные"
                )
            else:
                price_change_percent = token.get_price_change_percent(price)                
                icon = "🟢" if price_change_percent > 0 else "🔴" if price_change_percent < 0 else ""
                msg += (
                    f"Отчёт {icon} {price_change_percent:.2f}%\n"
                    f"💸: ${format_price(price)}\n"
                    f"💰: ${cap}\n"
                    "\n"
                )

                token.last_report_cap = cap
                token.last_report_price = price

        if msg == "":
            msg = "Отсутствуют токены для слежки"
        await context.bot.send_message(chat_id=user.chat_id, text=msg, parse_mode="HTML")  

def get_user_by_chat_id(chat_id: int):
    for user in users:
        if user.chat_id == chat_id:
            return user

# --- Получить данные и в случае чего отправить если ---
async def check_and_notify(context: CallbackContext):
    for user in users:
        for token in user.tokens_for_tracking:
            price, cap = token.fetch_token_data()
            if price and cap:
                logger.info("📊 Сбор статистики")        
                avg_price = token.get_avg_price()
                token.prices.append(price)
                token.prices.pop(0)
                token.caps.append(cap)
                token.caps.pop(0)
                if price > avg_price * PERCENT:
                    logger.info("💹 Отправка письма счастья")
                    price_change_percent = ((price - avg_price) / avg_price) * 100
                    msg = (
                        f"🟢🟢🟢🟢🟢🟢\n",
                        f"Цена выросла на <b>{price_change_percent}%<b> за час!\n"
                        f"💸: ${format_price(price)}\n"
                        f"💰: ${cap}\n"
                    )

                    await context.bot.send_message(chat_id=user.chat_id, text=msg, parse_mode="HTML")

# --- Обработка сообщений для нажатия кнопки ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text # type: ignore
    if text == BUTTON_REPORT_LABEL:
        logger.info("🔽 Кнопка нажата")
        await send_report(context)
    if text == "Отмена":
        return await cancel(update, context)

async def start_getting_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == BUTTON_CANCEL_LABEL: # type: ignore
        return await cancel(update, context)
        

    await context.bot.send_message(
        chat_id=update.effective_chat.id, # type: ignore
        text="Введите адрес токена:",
        reply_markup=get_cancel_keyboard()
    )

    return GETTING_ADDRESS

async def getting_address(update: Update, context: ContextTypes.DEFAULT_TYPE): 
    if update.message.text == BUTTON_CANCEL_LABEL: # type: ignore
        return await cancel(update, context)
       
    context.user_data['token_address'] = update.message.text # type: ignore

    await context.bot.send_message(
        chat_id=update.effective_chat.id, # type: ignore
        text="Введите сеть токена (bsc, solana...):",
        reply_markup=get_cancel_keyboard()
    )

    return GETTING_CHAIN

async def getting_chain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == BUTTON_CANCEL_LABEL: # type: ignore
        return await cancel(update, context)
        
    
    context.user_data['token_chain'] = update.message.text # type: ignore

    await context.bot.send_message(
        chat_id=update.effective_chat.id, # type: ignore
        text="Введите название токена:",
        reply_markup=get_cancel_keyboard()
    )

    return GETTING_NAME

async def getting_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == BUTTON_CANCEL_LABEL: # type: ignore
        return await cancel(update, context)
    
    context.user_data['token_name'] = update.message.text # type: ignore

    user: User = get_user_by_chat_id(update.effective_chat.id) # type: ignore
    user.add_token(Token(
        context.user_data['token_name'], # type: ignore
        context.user_data['token_address'], # type: ignore
        context.user_data['token_chain'], # type: ignore
    ))

    await context.bot.send_message(
        chat_id=user.chat_id, # type: ignore
        text="Токен добавлен",
        reply_markup=get_main_keyboard()
    )

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Отмена")

    await context.bot.send_message(
        chat_id=update.effective_chat.id, # type: ignore
        text="Ввод отменён",
        reply_markup=get_main_keyboard()
    )

    return ConversationHandler.END


# --- Основная функция ---
def main():
    logger.info("▶️ Начало роботы")
    if TELEGRAM_TOKEN is None:
        logger.info("⚠️ Незагружены переменные среды")
        return
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
   
    if app.job_queue is None:
        logger.info(f"⚠️ Отсутствует планировщик задач")    
        return
    
    app.job_queue.run_repeating(send_report, REPORT_INTERVAL)
    app.job_queue.run_repeating(check_and_notify, PRICE_CHECK_INTERVAL)

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f'^{BUTTON_ADD_TOKEN_LABEL}$'), start_getting_token)
        ],
        states={
            GETTING_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, getting_address)
            ],
            GETTING_CHAIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, getting_chain)
            ],
            GETTING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, getting_name)
            ],
        },
        fallbacks=[MessageHandler(filters.Regex("^Отмена$"), cancel)]
    )

    app.add_handler(conv_handler)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()

