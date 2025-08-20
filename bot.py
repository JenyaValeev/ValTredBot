import os
import logging
import asyncio
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from crypto_manager import CryptoManager
from db import init_db, add_pair, remove_pair, list_trades, create_run
from exchange import create_exchange, fetch_ohlcv
from strategy import Strategy
from exec_layer import ExecLayer
from pairs_loader import load_pairs, save_pairs   # 👈 загрузка/сохранение пар

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("bybit_bot")

# read env
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TESTNET = os.getenv("TESTNET", "true").lower() in ("1","true","yes")
MODE = os.getenv("MODE", "paper")
DEFAULT_MARKET_TYPE = os.getenv("DEFAULT_MARKET_TYPE", "swap")

# init db
default_params = {}
init_db(default_params)

crypto = CryptoManager()
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
if not API_KEY and os.getenv("ENCRYPTED_API_KEY"):
    try:
        API_KEY = crypto.decrypt(os.getenv("ENCRYPTED_API_KEY"))
        API_SECRET = crypto.decrypt(os.getenv("ENCRYPTED_API_SECRET")) if os.getenv("ENCRYPTED_API_SECRET") else ""
    except Exception as e:
        log.warning("Не удалось расшифровать API_KEY: %s", e)

exchange = create_exchange(API_KEY, API_SECRET, testnet=TESTNET, default_type=DEFAULT_MARKET_TYPE)
bot = Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None

RUNNING = False
PAIR_TASKS = {}

# Telegram UI
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Статус", callback_data="status"), InlineKeyboardButton("📊 Отчёт", callback_data="report")],
        [InlineKeyboardButton("▶️ Запустить", callback_data="start"), InlineKeyboardButton("⏹️ Остановить", callback_data="stop")],
        [InlineKeyboardButton("➕ Добавить пару", callback_data="add_pair"), InlineKeyboardButton("➖ Удалить пару", callback_data="remove_pair")],
        [InlineKeyboardButton("📂 Список пар", callback_data="show_pairs"), InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("📈 PnL", callback_data="pnl"), InlineKeyboardButton("🔁 Перезагрузить пары", callback_data="reload_pairs")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ])

async def tg_send(text: str, reply_markup=None):
    if not bot or not TELEGRAM_CHAT_ID:
        log.info("TG: %s", text)
        return
    try:
        await bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=text, reply_markup=reply_markup)
    except Exception as e:
        log.exception("TG send error: %s", e)

# handlers
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await (update.effective_message or update.callback_query.message).reply_text("🤖 Bybit Бот", reply_markup=main_keyboard())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "ℹ️ Управление ботом:\n\n"
        "🔄 Статус — показать состояние и активные пары\n"
        "📊 Отчёт — показать сделки\n"
        "📂 Список пар — пары для мониторинга\n"
        "💰 Баланс — показать баланс на бирже\n"
        "📈 PnL — показать открытые позиции и доходность\n"
        "▶️ Запустить / ⏹️ Остановить — управление мониторингом\n"
        "➕ / ➖ — добавить или удалить пару\n"
        "🔁 Перезагрузить пары — перечитать файл pairs.json"
    )
    await update.effective_message.reply_text(txt, reply_markup=main_keyboard())

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pairs = load_pairs()
    pairs_text = ", ".join([f"{s}({t})" for s, t in pairs]) or "—"
    txt = f"📌 Статус: {'🟢 РАБОТАЕТ' if RUNNING else '🔴 ОСТАНОВЛЕН'}\n📊 Пары: {pairs_text}"
    await update.effective_message.reply_text(txt, reply_markup=main_keyboard())

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trades = list_trades(1000)
    await update.effective_message.reply_text(f"📑 Сделок в базе: {len(trades)}", reply_markup=main_keyboard())

async def show_pairs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pairs = load_pairs()
    if not pairs:
        txt = "⚠️ Нет пар. Добавь их через меню или файл pairs.json"
    else:
        txt = "📂 Список пар:\n" + "\n".join([f"• {s} {t}" for s, t in pairs])
    await update.effective_message.reply_text(txt, reply_markup=main_keyboard())

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bal = await exchange.fetch_balance()
        total = bal.get("total", {})
        free = bal.get("free", {})
        used = bal.get("used", {})
        lines = ["💰 Баланс:"]
        for asset, amount in total.items():
            if amount > 0:
                lines.append(f"• {asset}: {amount:.4f} (Свободно: {free.get(asset,0):.4f}, В ордерах: {used.get(asset,0):.4f})")
        txt = "\n".join(lines) if len(lines) > 1 else "⚠️ Баланс пуст"
    except Exception as e:
        txt = f"❌ Ошибка получения баланса: {e}"
    await update.effective_message.reply_text(txt, reply_markup=main_keyboard())

async def pnl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        positions = await exchange.fetch_positions()
        if not positions:
            txt = "⚠️ Нет открытых позиций"
        else:
            lines = ["📈 PnL по позициям:"]
            for pos in positions:
                if float(pos.get("contracts", 0)) > 0:
                    sym = pos["symbol"]
                    side = pos["side"]
                    entry = float(pos.get("entryPrice", 0))
                    size = float(pos.get("contracts", 0))
                    mark = float(pos.get("markPrice", 0))
                    unreal = float(pos.get("unrealizedPnl", 0))
                    lines.append(
                        f"• {sym} {side} {size} контрактов\n"
                        f"   Вход: {entry}, Текущая: {mark}\n"
                        f"   PnL: {unreal:.4f} USDT"
                    )
            txt = "\n".join(lines)
    except Exception as e:
        txt = f"❌ Ошибка получения PnL: {e}"
    await update.effective_message.reply_text(txt, reply_markup=main_keyboard())

# buttons
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "main_menu":
        await query.edit_message_text("Меню", reply_markup=main_keyboard()); return
    if data == "status":
        await status_cmd(update, context); return
    if data == "report":
        await report_cmd(update, context); return
    if data == "show_pairs":
        await show_pairs_cmd(update, context); return
    if data == "balance":
        await balance_cmd(update, context); return
    if data == "pnl":
        await pnl_cmd(update, context); return
    if data == "start":
        global RUNNING, PAIR_TASKS
        if RUNNING:
            await query.edit_message_text("⚠️ Уже запущен", reply_markup=main_keyboard())
        else:
            await query.edit_message_text("▶️ Запуск...", reply_markup=main_keyboard())
            RUNNING = True
            asyncio.create_task(start_monitors())
        return
    if data == "stop":
        if not RUNNING:
            await query.edit_message_text("⚠️ Уже остановлен", reply_markup=main_keyboard())
        else:
            await query.edit_message_text("⏹️ Остановка...", reply_markup=main_keyboard())
            await stop_monitors()
        return
    if data == "add_pair":
        await query.edit_message_text("Введите пару в формате: BTC/USDT 15m", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]]))
        context.user_data["awaiting_input"] = "add_pair"
        return
    if data == "remove_pair":
        await query.edit_message_text("Введите символ для удаления, например: BTC/USDT", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]]))
        context.user_data["awaiting_input"] = "remove_pair"
        return
    if data == "reload_pairs":
        pairs = load_pairs()
        await query.edit_message_text(f"🔁 Пары перезагружены: {[s for s,t in pairs]}", reply_markup=main_keyboard())
        return
    if data == "help":
        await help_cmd(update, context); return

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("awaiting_input")
    txt = update.message.text.strip()
    if action == "add_pair":
        parts = txt.split()
        symbol = parts[0].upper()
        timeframe = parts[1] if len(parts) > 1 else os.getenv("TIMEFRAME", "15m")
        pairs = load_pairs()
        pairs.append((symbol, timeframe))
        save_pairs(pairs)
        add_pair(symbol, timeframe)  # в БД тоже
        await update.message.reply_text(f"✅ Добавлена пара: {symbol} {timeframe}", reply_markup=main_keyboard())
        context.user_data.pop("awaiting_input", None)
        return
    if action == "remove_pair":
        symbol = txt.upper()
        pairs = [(s,t) for s,t in load_pairs() if s != symbol]
        save_pairs(pairs)
        remove_pair(symbol)  # в БД тоже
        await update.message.reply_text(f"❌ Удалена пара: {symbol}", reply_markup=main_keyboard())
        context.user_data.pop("awaiting_input", None)
        return
    await update.message.reply_text("❓ Используй меню", reply_markup=main_keyboard())

# monitoring loop (без изменений)
async def monitor_pair(symbol: str, timeframe: str):
    log.info("Запущен мониторинг %s %s", symbol, timeframe)
    strat_cfg = {}
    import yaml
    if os.path.exists("config.yaml"):
        with open("config.yaml", "r") as f:
            cfg = yaml.safe_load(f)
            strat_cfg = cfg.get("strategy", {})

    strat = Strategy(strat_cfg, param_getter=lambda k, d=None: os.getenv(k, d))
    execL = ExecLayer(exchange, MODE, create_run(f"run {MODE} {symbol}"))
    pos = None

    while RUNNING:
        try:
            df = await fetch_ohlcv(exchange, symbol, timeframe, limit=500)
            if df.empty:
                await asyncio.sleep(10); continue
            df = strat.compute_indicators(df)
            sig = strat.generate_signal(df, equity_usdt=10000.0)
            price = float(df.iloc[-1]["close"])
            if not pos and sig.side != "hold":
                usdt = sig.info["usdt_size"]
                ok, msg, qty, px = await execL.open(symbol, sig.side, usdt)
                if ok:
                    pos = {"side": sig.side, "qty": qty, "entry": px, "stop": sig.stop_price, "tp": sig.tp_price}
                    await tg_send(f"📈 Открыта позиция {symbol} {sig.side} {qty}@{px}")
            elif pos:
                if pos["side"]=="long":
                    if price <= pos["stop"] or price >= pos["tp"]:
                        await execL.close(symbol, pos["side"], pos["qty"])
                        await tg_send(f"📉 Закрыт лонг {symbol} {pos['qty']}@{price}")
                        pos = None
                else:
                    if price >= pos["stop"] or price <= pos["tp"]:
                        await execL.close(symbol, pos["side"], pos["qty"])
                        await tg_send(f"📉 Закрыт шорт {symbol} {pos['qty']}@{price}")
                        pos = None
        except Exception as e:
            log.exception("Ошибка мониторинга %s: %s", symbol, e)
        await asyncio.sleep(30)
    log.info("Мониторинг остановлен %s", symbol)

async def start_monitors():
    global RUNNING, PAIR_TASKS
    pairs = load_pairs()
    if not pairs:
        await tg_send("⚠️ Пары не загружены. Добавь их в pairs.json или через меню.")
        return
    RUNNING = True
    PAIR_TASKS = {}
    for symbol, timeframe in pairs:
        t = asyncio.create_task(monitor_pair(symbol, timeframe))
        PAIR_TASKS[f"{symbol}|{timeframe}"] = t
    await tg_send(f"▶️ Запущен мониторинг {len(PAIR_TASKS)} пар.")

async def stop_monitors():
    global RUNNING, PAIR_TASKS
    RUNNING = False
    for _, t in list(PAIR_TASKS.items()):
        try:
            t.cancel()
        except Exception:
            pass
    PAIR_TASKS.clear()
    await tg_send("⏹️ Мониторинг остановлен.")

def main():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN не задан — проверь .env")
        return
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    async def on_startup(app_):
        await tg_send("🤖 Бот запущен", reply_markup=main_keyboard())
    app.post_init = on_startup
    app.run_polling()

if __name__ == "__main__":
    main()
