import os, asyncio, logging, uuid, random, psycopg2
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
from aiogram.fsm.storage.memory import MemoryStorage

# --- КОНФИГ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_IDS = [1945747968, 6928797177] 
PAYMENT_CARDS = ["5355 2800 2484 3821", "5232 4410 2403 2182"]

# --- ВЕБ-СЕРВЕР (KEEP-ALIVE) ---
app = Flask('')
@app.route('/')
def home(): return "Бот в сети и пашет на Postgres"

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

# --- РАБОТА С БД ---
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY, username TEXT, balance INTEGER DEFAULT 0, 
                referrer_id BIGINT, referral_count INTEGER DEFAULT 0, joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY, category TEXT, name TEXT, weight TEXT, price INTEGER
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY, short_id TEXT, user_id BIGINT, product TEXT, 
                price INTEGER, status TEXT DEFAULT 'pending', check_img TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            cur.execute("CREATE TABLE IF NOT EXISTS blacklist (user_id BIGINT PRIMARY KEY, reason TEXT)")
            conn.commit()

# --- СОСТОЯНИЯ (FSM) ---
class BotStates(StatesGroup):
    captcha = State()
    # Админка
    add_cat = State()
    add_name = State()
    add_weight = State()
    add_price = State()
    broadcast_msg = State()
    # Юзер
    wait_check = State()
    support_msg = State()

# --- КЛАВИАТУРЫ ---
def kb_main(uid):
    b = InlineKeyboardBuilder()
    b.button(text="🛍 КАТАЛОГ", callback_data="catalog")
    b.button(text="👤 ПРОФИЛЬ", callback_data="profile")
    b.button(text="💬 ПОДДЕРЖКА", callback_data="support")
    if uid in ADMIN_IDS:
        b.button(text="⚙️ АДМИНКА", callback_data="admin_panel")
    b.adjust(2)
    return b.as_markup()

def kb_admin():
    b = InlineKeyboardBuilder()
    b.button(text="📦 ДОБАВИТЬ ТОВАР", callback_data="adm_add")
    b.button(text="📣 РАССЫЛКА", callback_data="adm_spam")
    b.button(text="📊 СТАТИСТИКА", callback_data="adm_stats")
    b.button(text="🏠 В МЕНЮ", callback_data="to_main")
    b.adjust(1)
    return b.as_markup()

# --- ЛОГИКА ---
router = Router()

@router.message(CommandStart())
async def cmd_start(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM blacklist WHERE user_id = %s", (uid,))
            if cur.fetchone(): return 
            
            cur.execute("SELECT 1 FROM users WHERE user_id = %s", (uid,))
            if not cur.fetchone():
                ref_id = None
                if len(m.text.split()) > 1 and m.text.split()[1].isdigit():
                    ref_id = int(m.text.split()[1])
                cur.execute("INSERT INTO users (user_id, username, referrer_id) VALUES (%s, %s, %s)", (uid, m.from_user.username, ref_id))
                if ref_id: cur.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = %s", (ref_id,))
                conn.commit()
    
    n1, n2 = random.randint(1, 10), random.randint(1, 10)
    await state.update_data(res=n1+n2)
    await state.set_state(BotStates.captcha)
    await m.answer(f"🤖 ПРОВЕРКА: {n1} + {n2} = ?")

@router.message(BotStates.captcha)
async def captcha_done(m: types.Message, state: FSMContext):
    d = await state.get_data()
    if m.text == str(d.get('res')):
        await m.answer("✅ Добро пожаловать!", reply_markup=kb_main(m.from_user.id))
        await state.clear()
    else: await m.answer("❌ Неверно.")

# --- КАТАЛОГ ---
@router.callback_query(F.data == "catalog")
async def show_cats(c: types.CallbackQuery):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT category FROM products")
            cats = cur.fetchall()
    await c.answer()
    if not cats: return await c.message.edit_text("❌ Каталог пуст!", reply_markup=kb_main(c.from_user.id))
    b = InlineKeyboardBuilder()
    for cat in cats: b.button(text=cat[0], callback_data=f"cat_{cat[0]}")
    b.button(text="⬅️ НАЗАД", callback_data="to_main")
    await c.message.edit_text("📂 Выберите категорию:", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("cat_"))
async def show_prods(c: types.CallbackQuery):
    cat = c.data.split("_")[1]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT name FROM products WHERE category = %s", (cat,))
            prods = cur.fetchall()
    await c.answer()
    b = InlineKeyboardBuilder()
    for p in prods: b.button(text=p[0], callback_data=f"prod_{p[0]}")
    b.button(text="⬅️ НАЗАД", callback_data="catalog")
    await c.message.edit_text(f"🛍 Товары в {cat}:", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("prod_"))
async def show_items(c: types.CallbackQuery):
    p_name = c.data.split("_")[1]
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE name = %s", (p_name,))
            items = cur.fetchall()
    await c.answer()
    b = InlineKeyboardBuilder()
    for i in items: b.button(text=f"{i['weight']} — {i['price']}грн", callback_data=f"buy_{i['id']}")
    b.button(text="⬅️ НАЗАД", callback_data="catalog")
    await c.message.edit_text(f"⚖️ Фасовки {p_name}:", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("buy_"))
async def start_buy(c: types.CallbackQuery, state: FSMContext):
    p_id = c.data.split("_")[1]
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (p_id,))
            p = cur.fetchone()
    await c.answer()
    await state.update_data(b_price=p['price'], b_name=p['name'], b_weight=p['weight'])
    await state.set_state(BotStates.wait_check)
    card = random.choice(PAYMENT_CARDS)
    await c.message.edit_text(f"💳 ОПЛАТА: {p['price']}грн\nТовар: {p['name']} ({p['weight']})\n\nКарта: `{card}`\nПришлите СКРИН чека:")

@router.message(BotStates.wait_check, F.photo)
async def check_handler(m: types.Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    sid = str(uuid.uuid4())[:8]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO orders (order_id, short_id, user_id, product, price, check_img) VALUES (%s, %s, %s, %s, %s, %s)",
                        (str(uuid.uuid4()), sid, m.from_user.id, f"{d['b_name']} {d['b_weight']}", d['b_price'], m.photo[-1].file_id))
            conn.commit()
    await m.answer(f"✅ Чек принят! Заказ #{sid} на проверке.")
    for aid in ADMIN_IDS:
        b = InlineKeyboardBuilder().button(text="ОДОБРИТЬ", callback_data=f"adm_ok_{sid}").button(text="ОТКАЗ", callback_data=f"adm_no_{sid}")
        await bot.send_photo(aid, m.photo[-1].file_id, caption=f"Заказ #{sid}\nСумма: {d['b_price']}грн", reply_markup=b.adjust(2).as_markup())
    await state.clear()

# --- АДМИНКА ---
@router.callback_query(F.data == "admin_panel")
async def adm_menu(c: types.CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return
    await c.message.edit_text("⚙️ ПАНЕЛЬ УПРАВЛЕНИЯ", reply_markup=kb_admin())

@router.callback_query(F.data == "adm_add")
async def adm_add_1(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.add_cat)
    await c.message.answer("📁 Введите категорию:")

@router.message(BotStates.add_cat)
async def adm_add_2(m: types.Message, state: FSMContext):
    await state.update_data(ac_cat=m.text)
    await state.set_state(BotStates.add_name)
    await m.answer("📦 Введите название товара:")

@router.message(BotStates.add_name)
async def adm_add_3(m: types.Message, state: FSMContext):
    await state.update_data(ac_name=m.text)
    await state.set_state(BotStates.add_weight)
    await m.answer("⚖️ Введите вес:")

@router.message(BotStates.add_weight)
async def adm_add_4(m: types.Message, state: FSMContext):
    await state.update_data(ac_weight=m.text)
    await state.set_state(BotStates.add_price)
    await m.answer("💰 Введите цену (число):")

@router.message(BotStates.add_price)
async def adm_add_5(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Нужно число!")
    d = await state.get_data()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO products (category, name, weight, price) VALUES (%s, %s, %s, %s)",
                        (d['ac_cat'], d['ac_name'], d['ac_weight'], int(m.text)))
            conn.commit()
    await m.answer("✅ Товар добавлен!", reply_markup=kb_admin())
    await state.clear()

@router.callback_query(F.data.startswith("adm_ok_"))
async def adm_confirm(c: types.CallbackQuery, bot: Bot):
    sid = c.data.split("_")[2]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE orders SET status = 'done' WHERE short_id = %s RETURNING user_id", (sid,))
            uid = cur.fetchone()[0]
            conn.commit()
    await bot.send_message(uid, f"✅ Заказ #{sid} одобрен! Ожидайте адрес.")
    await c.message.edit_caption(caption=f"✅ Заказ {sid} ОДОБРЕН")

# --- ПРОФИЛЬ ---
@router.callback_query(F.data == "profile")
async def profile_show(c: types.CallbackQuery, bot: Bot):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (c.from_user.id,))
            u = cur.fetchone()
    me = await bot.get_me()
    ref = f"https://t.me/{me.username}?start={c.from_user.id}"
    await c.message.edit_text(f"👤 ПРОФИЛЬ\nID: `{u['user_id']}`\nРефералов: {u['referral_count']}\n\n🔗 Реф. ссылка:\n`{ref}`", reply_markup=kb_main(c.from_user.id))

@router.callback_query(F.data == "to_main")
async def to_main_h(c: types.CallbackQuery):
    await c.message.edit_text("🏠 Главное меню:", reply_markup=kb_main(c.from_user.id))

# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    Thread(target=run_web, daemon=True).start()
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
