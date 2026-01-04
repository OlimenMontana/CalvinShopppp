import os
import asyncio
import logging
import uuid 
import random 
import psycopg2
from datetime import datetime
from psycopg2.extras import RealDictCursor
from flask import Flask
from threading import Thread
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import CommandStart, StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ReplyKeyboardMarkup, KeyboardButton

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ТВОЙ_ТОКЕН")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_IDS = [1945747968, 6928797177]
PAYMENT_CARDS = ["5355 2800 2484 3821", "5232 4410 2403 2182"]

# --- WEB SERVER FOR RENDER ---
app = Flask('')
@app.route('/')
def home(): return "Бот работает 24/7"

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# --- DATABASE ENGINE (POSTGRESQL) ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, username TEXT, first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, referrer_id BIGINT, referral_count INTEGER DEFAULT 0, balance INTEGER DEFAULT 0, blocked_bot INTEGER DEFAULT 0)")
            cur.execute("CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, short_id TEXT, user_id BIGINT, product TEXT, weight TEXT, price INTEGER, status TEXT DEFAULT 'pending', check_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS products (id SERIAL PRIMARY KEY, category TEXT, name TEXT, weight TEXT, price INTEGER)")
            cur.execute("CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, discount INTEGER, uses INTEGER DEFAULT 1)")
            cur.execute("CREATE TABLE IF NOT EXISTS blacklist (user_id BIGINT PRIMARY KEY, reason TEXT)")
            conn.commit()

# --- ВСЕ ФУНКЦИИ БД (МАКСИМАЛЬНО ПОЛНЫЕ) ---
def add_user(user_id, username, ref_id=None):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (user_id, username, referrer_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (user_id, username, ref_id))
            conn.commit()

def is_banned(user_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM blacklist WHERE user_id = %s", (user_id,))
            return cur.fetchone() is not None

def get_stats():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            u_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'confirmed'")
            o_count = cur.fetchone()[0]
            return u_count, o_count

# --- СОСТОЯНИЯ ---
class FSMS(StatesGroup):
    captcha = State()
    broadcast = State()
    add_cat = State()
    add_name = State()
    add_weight = State()
    add_price = State()
    support = State()
    promo = State()
    send_check = State()

# --- КЛАВИАТУРЫ ---
def main_kb(user_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍 КАТАЛОГ", callback_data="catalog")
    builder.button(text="👤 ПРОФИЛЬ", callback_data="profile")
    builder.button(text="ℹ️ О НАС", callback_data="about")
    builder.button(text="💬 ПОДДЕРЖКА", callback_data="support")
    if user_id in ADMIN_IDS:
        builder.button(text="⚙️ АДМИНКА", callback_data="admin_menu")
    builder.adjust(2)
    return builder.as_markup()

def admin_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="📣 РАССЫЛКА", callback_data="adm_spam")
    builder.button(text="📦 ДОБАВИТЬ ТОВАР", callback_data="adm_add")
    builder.button(text="🎫 ПРОМОКОДЫ", callback_data="adm_promo")
    builder.button(text="🚫 БАН-ЛИСТ", callback_data="adm_ban")
    builder.button(text="📊 СТАТИСТИКА", callback_data="adm_stats")
    builder.adjust(1)
    return builder.as_markup()

# --- РОУТЕР И ЛОГИКА ---
router = Router()

@router.message(CommandStart())
async def start(m: types.Message, state: FSMContext):
    if is_banned(m.from_user.id): return
    ref_id = int(m.text.split()[1]) if len(m.text.split()) > 1 and m.text.split()[1].isdigit() else None
    add_user(m.from_user.id, m.from_user.username, ref_id)
    
    n1, n2 = random.randint(1, 10), random.randint(1, 10)
    await state.update_data(res=n1+n2)
    await state.set_state(FSMS.captcha)
    await m.answer(f"👋 Привет! Докажи что ты не бот: {n1} + {n2} = ?")

@router.message(FSMS.captcha)
async def captcha_done(m: types.Message, state: FSMContext):
    data = await state.get_data()
    if m.text == str(data['res']):
        await m.answer("✅ Доступ разрешен!", reply_markup=main_kb(m.from_user.id))
        await state.clear()
    else:
        await m.answer("❌ Неверно.")

# --- КАТАЛОГ ---
@router.callback_query(F.data == "catalog")
async def catalog(c: types.CallbackQuery):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT category FROM products")
            cats = cur.fetchall()
    if not cats: return await c.answer("Товаров нет")
    builder = InlineKeyboardBuilder()
    for cat in cats: builder.button(text=cat[0], callback_data=f"cat_{cat[0]}")
    builder.adjust(1)
    await c.message.edit_text("Выберите категорию:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("cat_"))
async def subcat(c: types.CallbackQuery):
    cat = c.data.split("_")[1]
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT name FROM products WHERE category = %s", (cat,))
            prods = cur.fetchall()
    builder = InlineKeyboardBuilder()
    for p in prods: builder.button(text=p[0], callback_data=f"prod_{p[0]}")
    builder.button(text="⬅️ Назад", callback_data="catalog")
    builder.adjust(1)
    await c.message.edit_text(f"Товары в {cat}:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("prod_"))
async def select_weight(c: types.CallbackQuery, state: FSMContext):
    p_name = c.data.split("_")[1]
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE name = %s", (p_name,))
            items = cur.fetchall()
    builder = InlineKeyboardBuilder()
    for i in items:
        builder.button(text=f"{i['weight']} - {i['price']}грн", callback_data=f"buy_{i['id']}")
    builder.adjust(1)
    await c.message.edit_text(f"Выберите фасовку {p_name}:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("buy_"))
async def buy_item(c: types.CallbackQuery, state: FSMContext):
    p_id = c.data.split("_")[1]
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (p_id,))
            p = cur.fetchone()
    await state.update_data(p_id=p_id, price=p['price'], p_name=p['name'], p_weight=p['weight'])
    await state.set_state(FSMS.send_check)
    card = random.choice(PAYMENT_CARDS)
    await c.message.edit_text(f"💳 ОПЛАТА\n\nТовар: {p['name']} ({p['weight']})\nЦена: {p['price']} грн\n\nКарта: `{card}`\n\nПосле оплаты отправьте СКРИНШОТ чека:")

@router.message(FSMS.send_check, F.photo)
async def get_check(m: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    oid = str(uuid.uuid4())[:8]
    # Сохраняем заказ
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO orders (order_id, short_id, user_id, product, weight, price, check_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (str(uuid.uuid4()), oid, m.from_user.id, data['p_name'], data['p_weight'], data['price'], m.photo[-1].file_id))
            conn.commit()
    
    await m.answer(f"✅ Чек принят! Номер заказа: #{oid}\nОжидайте проверки оператором.")
    for aid in ADMIN_IDS:
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ ПОДТВЕРДИТЬ", callback_data=f"adm_ok_{oid}")
        builder.button(text="❌ ОТКЛОНИТЬ", callback_data=f"adm_no_{oid}")
        await bot.send_photo(aid, photo=m.photo[-1].file_id, caption=f"Заказ #{oid}\nТовар: {data['p_name']}\nЦена: {data['price']}", reply_markup=builder.as_markup())
    await state.clear()

# --- АДМИНКА ОБРАБОТКА ---
@router.callback_query(F.data.startswith("adm_ok_"))
async def confirm_order(c: types.CallbackQuery, bot: Bot):
    oid = c.data.split("_")[2]
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE orders SET status = 'confirmed' WHERE short_id = %s RETURNING user_id", (oid,))
            uid = cur.fetchone()[0]
            conn.commit()
    await bot.send_message(uid, f"🎉 Ваш заказ #{oid} подтвержден! Ссылка на адрес/фото: [ССЫЛКА_ТУТ]")
    await c.message.edit_caption(caption=f"✅ Заказ {oid} ОДОБРЕН")

# --- ПРОФИЛЬ И РЕФЕРАЛКА ---
@router.callback_query(F.data == "profile")
async def profile(c: types.CallbackQuery, bot: Bot):
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (c.from_user.id,))
            u = cur.fetchone()
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start={c.from_user.id}"
    text = f"👤 ПРОФИЛЬ\n\nID: `{c.from_user.id}`\nРефералов: {u['referral_count']}\nБаланс: {u['balance']} грн\n\n🔗 Твоя ссылка:\n{ref_link}"
    await c.message.edit_text(text, reply_markup=main_kb(c.from_user.id))

# --- РАССЫЛКА ---
@router.callback_query(F.data == "adm_spam")
async def spam_start(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(FSMS.broadcast)
    await c.message.answer("Введите текст рассылки (можно с фото):")

@router.message(FSMS.broadcast)
async def spam_do(m: types.Message, state: FSMContext, bot: Bot):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users")
            uids = cur.fetchall()
    count = 0
    for u in uids:
        try:
            await m.copy_to(u[0])
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await m.answer(f"📢 Рассылка окончена! Получили: {count} человек.")
    await state.clear()

# --- MAIN RUN ---
async def main():
    logging.basicConfig(level=logging.INFO)
    keep_alive()
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
