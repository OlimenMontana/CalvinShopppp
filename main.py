import asyncio
import logging
import re
import uuid 
import random 
import psycopg2 
from psycopg2.extras import DictCursor
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import CommandStart, StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError
from aiogram.types import BotCommand, TelegramObject
from aiogram.enums import ChatAction
from aiogram.fsm.storage.memory import MemoryStorage

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8583363803:AAFtkD-J0vq8uR6kyJPxO00SH1TSn8fIDUo" 
ADMIN_IDS = [1945747968, 6928797177]
PAYMENT_CARDS = ["5355 2800 2484 3821", "5232 4410 2403 2182"]

# ВСТАВЬ СВОЙ URL ПОСТГРЕСА ТУТ
DB_URL = "postgresql://shop_db_user_user:PbLeivrMYwfcB8qFfL2VdbVXRKFNbZ89@dpg-d5cq2heuk2gs738ej7og-a/shop_db_user"

# ----------------------------------------------------------------------
# --- ЛОГИКА БД (PostgreSQL) ---
# ----------------------------------------------------------------------

def get_db_connection():
    # Исправляем протокол для psycopg2
    url = DB_URL.replace("postgres://", "postgresql://")
    return psycopg2.connect(url)

def db_query(sql, params=(), fetch=False, fetch_all=False, commit=True):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(sql, params)
            if fetch:
                row = cur.fetchone()
                return dict(row) if row else None
            if fetch_all:
                return [dict(r) for r in cur.fetchall()]
            if commit:
                conn.commit()
            return cur.rowcount
    except Exception as e:
        logging.error(f"DATABASE ERROR: {e}")
        return None
    finally:
        if conn: conn.close()

def init_db():
    queries = [
        """CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY, username TEXT, referrer_id BIGINT,
            referral_count INTEGER DEFAULT 0, has_purchased INTEGER DEFAULT 0,
            blocked_bot INTEGER DEFAULT 0, first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY, short_id TEXT, user_id BIGINT, username TEXT,
            product TEXT, weight TEXT, final_price INTEGER, original_price INTEGER,
            promo_code_used TEXT, contact_info TEXT, check_file_id TEXT,
            status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY, category_name TEXT, product_name TEXT, weight TEXT, price INTEGER
        )""",
        """CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY, discount_percent INTEGER, is_reusable INTEGER DEFAULT 1
        )""",
        """CREATE TABLE IF NOT EXISTS blacklist (
            user_id BIGINT PRIMARY KEY, reason TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    ]
    for q in queries: db_query(q)
    
    # Первичные товары если пусто
    if not db_query("SELECT 1 FROM products LIMIT 1", fetch=True):
        db_query("INSERT INTO products (category_name, product_name, weight, price) VALUES (%s, %s, %s, %s)", 
                 ("Шишки", "AK-47", "1.0г", 400))

# ----------------------------------------------------------------------
# --- СОСТОЯНИЯ (FSM) ---
# ----------------------------------------------------------------------

class AuthStates(StatesGroup): waiting_for_captcha = State()
class UserSupport(StatesGroup): waiting_for_question = State(); in_support = State()
class AdminStates(StatesGroup): 
    in_support = State(); waiting_for_broadcast = State()
    add_prod_cat = State(); add_prod_name = State(); add_prod_weight = State(); add_prod_price = State()
class OrderStates(StatesGroup): 
    waiting_for_category = State(); waiting_for_product = State()
    waiting_for_weight = State(); waiting_for_promo = State()
    waiting_for_pay_check = State(); waiting_for_contact = State()

# ----------------------------------------------------------------------
# --- КЛАВИАТУРЫ ---
# ----------------------------------------------------------------------

def get_main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🛍️ Каталог", callback_data="show_catalog")
    kb.button(text="👤 Профиль / Рефералы", callback_data="show_profile")
    kb.button(text="💬 Написать Админу", callback_data="start_support")
    return kb.adjust(1).as_markup()

def get_admin_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="adm_stats")
    kb.button(text="📣 Рассылка", callback_data="adm_broadcast")
    kb.button(text="📦 Добавить Товар", callback_data="adm_add_prod")
    kb.button(text="🚫 Бан по ID", callback_data="adm_ban")
    return kb.adjust(1).as_markup()

# ----------------------------------------------------------------------
# --- ХЕНДЛЕРЫ ---
# ----------------------------------------------------------------------

router = Router()

@router.message(CommandStart())
async def cmd_start(m: types.Message, state: FSMContext):
    user = db_query("SELECT * FROM users WHERE user_id = %s", (m.from_user.id,), fetch=True)
    if user:
        await m.answer(f"С возвращением, {m.from_user.first_name}!", reply_markup=get_main_menu_kb())
    else:
        ref_id = None
        args = m.text.split()
        if len(args) > 1 and args[1].isdigit(): ref_id = int(args[1])
        
        n1, n2 = random.randint(1, 10), random.randint(1, 10)
        await state.update_data(ans=n1+n2, ref=ref_id)
        await state.set_state(AuthStates.waiting_for_captcha)
        await m.answer(f"🛡️ Капча: {n1} + {n2} = ?")

@router.message(AuthStates.waiting_for_captcha)
async def captcha_done(m: types.Message, state: FSMContext):
    data = await state.get_data()
    if m.text == str(data.get('ans')):
        db_query("INSERT INTO users (user_id, username, referrer_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                 (m.from_user.id, m.from_user.username, data.get('ref')))
        await state.clear()
        await m.answer("✅ Доступ открыт!", reply_markup=get_main_menu_kb())
    else: await m.answer("❌ Неверно.")

# --- КАТАЛОГ ---
@router.callback_query(F.data == "show_catalog")
async def cat_list(call: types.CallbackQuery, state: FSMContext):
    cats = db_query("SELECT DISTINCT category_name FROM products", fetch_all=True)
    kb = InlineKeyboardBuilder()
    for c in cats: kb.button(text=c['category_name'], callback_data=f"cat:{c['category_name']}")
    await call.message.edit_text("Выберите категорию:", reply_markup=kb.adjust(1).as_markup())
    await state.set_state(OrderStates.waiting_for_category)

@router.callback_query(F.data.startswith("cat:"), OrderStates.waiting_for_category)
async def prod_list(call: types.CallbackQuery, state: FSMContext):
    cat = call.data.split(":")[1]
    prods = db_query("SELECT DISTINCT product_name FROM products WHERE category_name = %s", (cat,), fetch_all=True)
    kb = InlineKeyboardBuilder()
    for p in prods: kb.button(text=p['product_name'], callback_data=f"prod:{p['product_name']}")
    await call.message.edit_text(f"Товары в {cat}:", reply_markup=kb.adjust(1).as_markup())
    await state.set_state(OrderStates.waiting_for_product)

@router.callback_query(F.data.startswith("prod:"), OrderStates.waiting_for_product)
async def weight_list(call: types.CallbackQuery, state: FSMContext):
    p_name = call.data.split(":")[1]
    items = db_query("SELECT id, weight, price FROM products WHERE product_name = %s", (p_name,), fetch_all=True)
    kb = InlineKeyboardBuilder()
    for i in items: kb.button(text=f"{i['weight']} - {i['price']} грн", callback_data=f"id:{i['id']}")
    await state.update_data(cur_prod=p_name)
    await call.message.edit_text("Выберите фасовку:", reply_markup=kb.adjust(1).as_markup())
    await state.set_state(OrderStates.waiting_for_weight)

@router.callback_query(F.data.startswith("id:"), OrderStates.waiting_for_weight)
async def ask_promo(call: types.CallbackQuery, state: FSMContext):
    p_id = call.data.split(":")[1]
    item = db_query("SELECT * FROM products WHERE id = %s", (p_id,), fetch=True)
    await state.update_data(p_name=item['product_name'], p_weight=item['weight'], p_price=item['price'])
    await state.set_state(OrderStates.waiting_for_promo)
    kb = InlineKeyboardBuilder().button(text="Пропустить", callback_data="skip_promo").as_markup()
    await call.message.edit_text("🎟️ Введите промокод (если есть) или нажмите пропустить:", reply_markup=kb)

@router.callback_query(F.data == "skip_promo", OrderStates.waiting_for_promo)
async def skip_pr(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    card = random.choice(PAYMENT_CARDS)
    await call.message.edit_text(f"💳 К оплате: {data['p_price']} грн\nРеквизиты: `{card}`\n\nПришлите ФОТО чека.")
    await state.set_state(OrderStates.waiting_for_pay_check)

# --- ПРОФИЛЬ И РЕФЕРАЛЫ ---
@router.callback_query(F.data == "show_profile")
async def profile(call: types.CallbackQuery, bot: Bot):
    u = db_query("SELECT * FROM users WHERE user_id = %s", (call.from_user.id,), fetch=True)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={u['user_id']}"
    text = (f"👤 Профиль: {u['user_id']}\n"
            f"📈 Рефералов: {u['referral_count']} / 5\n"
            f"🎁 Сделаешь 5 рефов - получишь купон 75%\n\n"
            f"🔗 Твоя ссылка: `{link}`")
    await call.message.edit_text(text, reply_markup=get_main_menu_kb())

# --- ПОДДЕРЖКА (LIVE CHAT) ---
@router.callback_query(F.data == "start_support")
async def sup_start(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(UserSupport.waiting_for_question)
    await call.message.edit_text("💬 Опишите ваш вопрос одним сообщением:")

@router.message(UserSupport.waiting_for_question)
async def sup_msg(m: types.Message, state: FSMContext, bot: Bot):
    for adm in ADMIN_IDS:
        kb = InlineKeyboardBuilder().button(text="Ответить", callback_data=f"ans_u:{m.from_user.id}").as_markup()
        await bot.send_message(adm, f"❓ Вопрос от @{m.from_user.username} ({m.from_user.id}):\n{m.text}", reply_markup=kb)
    await m.answer("✅ Отправлено. Ждите ответа.")
    await state.set_state(UserSupport.in_support)

@router.callback_query(F.data.startswith("ans_u:"), F.from_user.id.in_(ADMIN_IDS))
async def adm_ans_start(call: types.CallbackQuery, state: FSMContext):
    uid = int(call.data.split(":")[1])
    await state.update_data(chat_uid=uid)
    await state.set_state(AdminStates.in_support)
    await call.message.answer(f"Пиши ответ для {uid}. Чтобы выйти - /cancel")

@router.message(AdminStates.in_support, F.from_user.id.in_(ADMIN_IDS))
async def adm_sending(m: types.Message, state: FSMContext, bot: Bot):
    if m.text == "/cancel": 
        await state.clear(); await m.answer("Вышли из чата"); return
    data = await state.get_data()
    try:
        await bot.send_message(data['chat_uid'], f"👨‍💻 Ответ от админа:\n{m.text}")
        await m.answer("✅ Отправлено")
    except: await m.answer("❌ Ошибка")

# --- ОПЛАТА И ПРОВЕРКА ЗАКАЗА ---
@router.message(F.photo, OrderStates.waiting_for_pay_check)
async def get_check(m: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    oid = str(uuid.uuid4())[:8]
    db_query("INSERT INTO orders (order_id, short_id, user_id, username, product, weight, final_price, check_file_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
             (oid, oid, m.from_user.id, m.from_user.username, data['p_name'], data['p_weight'], data['p_price'], m.photo[-1].file_id))
    
    for adm in ADMIN_IDS:
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Одобрить", callback_data=f"ok:{oid}")
        kb.button(text="❌ Отказ", callback_data=f"no:{oid}")
        await bot.send_photo(adm, m.photo[-1].file_id, caption=f"📦 Заказ #{oid}\nЮзер: @{m.from_user.username}\n{data['p_name']} {data['p_weight']}", reply_markup=kb.adjust(2).as_markup())
    
    await m.answer("⏳ Чек принят. Ожидайте подтверждения.")
    await state.clear()

@router.callback_query(F.data.startswith("ok:"), F.from_user.id.in_(ADMIN_IDS))
async def order_ok(call: types.CallbackQuery, bot: Bot):
    oid = call.data.split(":")[1]
    order = db_query("SELECT * FROM orders WHERE order_id = %s", (oid,), fetch=True)
    db_query("UPDATE orders SET status = 'confirmed' WHERE order_id = %s", (oid,))
    
    # Рефералка: если первая покупка
    u = db_query("SELECT * FROM users WHERE user_id = %s", (order['user_id'],), fetch=True)
    if u['has_purchased'] == 0 and u['referrer_id']:
        db_query("UPDATE users SET has_purchased = 1 WHERE user_id = %s", (order['user_id'],))
        ref_id = u['referrer_id']
        db_query("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = %s", (ref_id,))
        
        # Проверка на 5 рефов
        ref_data = db_query("SELECT referral_count FROM users WHERE user_id = %s", (ref_id,), fetch=True)
        if ref_data['referral_count'] >= 5:
            promo = f"REF-{random.randint(100,999)}"
            db_query("INSERT INTO promo_codes (code, discount_percent, is_reusable) VALUES (%s, 75, 0)", (promo,))
            db_query("UPDATE users SET referral_count = 0 WHERE user_id = %s", (ref_id,))
            try: await bot.send_message(ref_id, f"🎁 Твой бонус за 5 друзей! Купон 75%: `{promo}`")
            except: pass

    await bot.send_message(order['user_id'], f"✅ Твой заказ #{oid} одобрен! Курьер выезжает.")
    await call.message.edit_caption(caption=call.message.caption + "\n\nСТАТУС: ✅ ОДОБРЕНО")

# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # Middleware проверки бана
    @dp.message.middleware()
    async def check_ban(handler, event, data):
        if db_query("SELECT 1 FROM blacklist WHERE user_id = %s", (event.from_user.id,), fetch=True):
            return
        return await handler(event, data)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
