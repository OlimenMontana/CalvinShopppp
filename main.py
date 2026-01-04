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

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_IDS = [1945747968, 6928797177]
PAYMENT_CARDS = ["5355 2800 2484 3821", "5232 4410 2403 2182"]

# --- WEB SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Бот пашет на Postgres"

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

# --- DATABASE ---
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
            cur.execute("CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, discount INTEGER, uses INTEGER DEFAULT 1)")
            cur.execute("CREATE TABLE IF NOT EXISTS blacklist (user_id BIGINT PRIMARY KEY, reason TEXT)")
            conn.commit()

# --- STATES ---
class BotStates(StatesGroup):
    captcha = State()
    # Админские стейты
    add_cat = State()
    add_name = State()
    add_weight = State()
    add_price = State()
    broadcast_text = State()
    ban_user_id = State()
    # Юзерские стейты
    wait_check = State()
    support_msg = State()
    use_promo = State()

# --- KEYBOARDS ---
def main_kb(uid):
    b = InlineKeyboardBuilder()
    b.button(text="🛍 КАТАЛОГ", callback_data="catalog")
    b.button(text="👤 ПРОФИЛЬ", callback_data="profile")
    b.button(text="🎟 ПРОМОКОД", callback_data="promo")
    b.button(text="💬 ПОМОЩЬ", callback_data="support")
    if uid in ADMIN_IDS: b.button(text="🛠 АДМИН-ПАНЕЛЬ", callback_data="admin_main")
    b.adjust(2); return b.as_markup()

def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="➕ ДОБАВИТЬ ТОВАР", callback_data="adm_add_product")
    b.button(text="📢 РАССЫЛКА", callback_data="adm_broadcast")
    b.button(text="📊 СТАТИСТИКА", callback_data="adm_stats")
    b.button(text="🚫 БАН-СИСТЕМА", callback_data="adm_ban")
    b.button(text="📝 СПИСОК ЗАКАЗОВ", callback_data="adm_orders")
    b.button(text="⬅️ ВЫХОД", callback_data="to_main")
    b.adjust(1); return b.as_markup()

router = Router()

# --- START & CAPTCHA ---
@router.message(CommandStart())
async def cmd_start(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM blacklist WHERE user_id = %s", (uid,))
            if cur.fetchone(): return
            cur.execute("SELECT 1 FROM users WHERE user_id = %s", (uid,))
            if not cur.fetchone():
                ref_id = int(m.text.split()[1]) if len(m.text.split()) > 1 and m.text.split()[1].isdigit() else None
                cur.execute("INSERT INTO users (user_id, username, referrer_id) VALUES (%s, %s, %s)", (uid, m.from_user.username, ref_id))
                if ref_id: cur.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = %s", (ref_id,))
                conn.commit()
    n1, n2 = random.randint(1, 10), random.randint(1, 10)
    await state.update_data(c_res=n1+n2)
    await state.set_state(BotStates.captcha)
    await m.answer(f"🛡 ПРОВЕРКА: {n1} + {n2} = ?")

@router.message(BotStates.captcha)
async def proc_captcha(m: types.Message, state: FSMContext):
    d = await state.get_data()
    if m.text == str(d.get('c_res')):
        await m.answer("✅ Приветствуем в нашем магазине!", reply_markup=main_kb(m.from_user.id))
        await state.clear()
    else: await m.answer("❌ Неверно. Попробуй еще раз.")

# --- АДМИНКА: ДОБАВЛЕНИЕ ТОВАРА (РАЗВЕРНУТО) ---
@router.callback_query(F.data == "adm_add_product")
async def adm_add_1(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.add_cat)
    await c.message.edit_text("📁 Введите название КАТЕГОРИИ:")

@router.message(BotStates.add_cat)
async def adm_add_2(m: types.Message, state: FSMContext):
    await state.update_data(ac_cat=m.text)
    await state.set_state(BotStates.add_name)
    await m.answer("📦 Введите название ТОВАРА:")

@router.message(BotStates.add_name)
async def adm_add_3(m: types.Message, state: FSMContext):
    await state.update_data(ac_name=m.text)
    await state.set_state(BotStates.add_weight)
    await m.answer("⚖️ Введите ВЕС/КОЛИЧЕСТВО (например, 1г):")

@router.message(BotStates.add_weight)
async def adm_add_4(m: types.Message, state: FSMContext):
    await state.update_data(ac_weight=m.text)
    await state.set_state(BotStates.add_price)
    await m.answer("💰 Введите ЦЕНУ (только число):")

@router.message(BotStates.add_price)
async def adm_add_5(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Введите ЧИСЛО!")
    d = await state.get_data()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO products (category, name, weight, price) VALUES (%s, %s, %s, %s)",
                        (d['ac_cat'], d['ac_name'], d['ac_weight'], int(m.text)))
            conn.commit()
    await m.answer("✅ ТОВАР ДОБАВЛЕН!", reply_markup=admin_kb())
    await state.clear()

# --- КАТАЛОГ (ТВОЯ ЛОГИКА) ---
@router.callback_query(F.data == "catalog")
async def show_catalog(c: types.CallbackQuery):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT category FROM products")
            cats = cur.fetchall()
    if not cats: return await c.answer("❌ Магазин пуст!", show_alert=True)
    b = InlineKeyboardBuilder()
    for cat in cats: b.button(text=f"📂 {cat[0]}", callback_data=f"cat_{cat[0]}")
    b.button(text="⬅️ НАЗАД", callback_data="to_main")
    await c.message.edit_text("🌟 ВЫБЕРИТЕ КАТЕГОРИЮ:", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("cat_"))
async def show_category_prods(c: types.CallbackQuery):
    cat = c.data.split("_")[1]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT name FROM products WHERE category = %s", (cat,))
            prods = cur.fetchall()
    b = InlineKeyboardBuilder()
    for p in prods: b.button(text=f"📍 {p[0]}", callback_data=f"prod_{p[0]}")
    b.button(text="⬅️ НАЗАД", callback_data="catalog")
    await c.message.edit_text(f"🛍 ТОВАРЫ В '{cat}':", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("prod_"))
async def show_product_weights(c: types.CallbackQuery):
    p_name = c.data.split("_")[1]
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE name = %s", (p_name,))
            items = cur.fetchall()
    b = InlineKeyboardBuilder()
    for i in items: b.button(text=f"💎 {i['weight']} — {i['price']} грн", callback_data=f"buy_{i['id']}")
    b.button(text="⬅️ НАЗАД", callback_data="catalog")
    await c.message.edit_text(f"⚖️ ФАСОВКИ '{p_name}':", reply_markup=b.adjust(1).as_markup())

# --- ОФОРМЛЕНИЕ ЗАКАЗА ---
@router.callback_query(F.data.startswith("buy_"))
async def buy_process(c: types.CallbackQuery, state: FSMContext):
    p_id = c.data.split("_")[1]
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (p_id,))
            p = cur.fetchone()
    await state.update_data(b_id=p_id, b_price=p['price'], b_name=p['name'], b_weight=p['weight'])
    await state.set_state(BotStates.wait_check)
    card = random.choice(PAYMENT_CARDS)
    await c.message.edit_text(f"💵 ОПЛАТА ЗАКАЗА\n\n📝 Товар: {p['name']} ({p['weight']})\n💰 Сумма: {p['price']} грн\n\n💳 Карта для оплаты:\n`{card}`\n\n⚠️ После оплаты отправьте СКРИНШОТ чека боту!")

@router.message(BotStates.wait_check, F.photo)
async def process_check(m: types.Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    sid = str(uuid.uuid4())[:8]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO orders (order_id, short_id, user_id, product, price, check_img) VALUES (%s, %s, %s, %s, %s, %s)",
                        (str(uuid.uuid4()), sid, m.from_user.id, f"{d['b_name']} ({d['b_weight']})", d['b_price'], m.photo[-1].file_id))
            conn.commit()
    await m.answer(f"📥 ЧЕК ПРИНЯТ!\nНомер заказа: #{sid}\nОжидайте подтверждения оператором.")
    for aid in ADMIN_IDS:
        b = InlineKeyboardBuilder().button(text="✅ ПРИНЯТЬ", callback_data=f"adm_ok_{sid}").button(text="❌ ОТКЛОНИТЬ", callback_data=f"adm_no_{sid}")
        await bot.send_photo(aid, m.photo[-1].file_id, caption=f"🚀 НОВЫЙ ЗАКАЗ #{sid}\nСумма: {d['b_price']} грн\nЮзер: @{m.from_user.username}", reply_markup=b.as_markup())
    await state.clear()

# --- ПРОФИЛЬ & РЕФЕРАЛКА ---
@router.callback_query(F.data == "profile")
async def profile_menu(c: types.CallbackQuery, bot: Bot):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (c.from_user.id,))
            u = cur.fetchone()
    b_info = await bot.get_me()
    ref_link = f"https://t.me/{b_info.username}?start={c.from_user.id}"
    text = (f"👤 МОЙ ПРОФИЛЬ\n\n🆔 Твой ID: `{u['user_id']}`\n💰 Баланс: {u['balance']} грн\n"
            f"👥 Рефералов: {u['referral_count']}\n\n🔗 Реферальная ссылка:\n{ref_link}")
    await c.message.edit_text(text, reply_markup=main_kb(c.from_user.id))

# --- ВСПОМОГАТЕЛЬНЫЕ ---
@router.callback_query(F.data == "to_main")
async def back_main(c: types.CallbackQuery):
    await c.message.edit_text("🏠 Главное меню:", reply_markup=main_kb(c.from_user.id))

@router.callback_query(F.data == "admin_main")
async def adm_panel(c: types.CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return
    await c.message.edit_text("🛠 ПАНЕЛЬ УПРАВЛЕНИЯ:", reply_markup=admin_kb())

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
