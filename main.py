import asyncio
import logging
import uuid 
import random 
import sqlite3
import os
import html # Для экранирования текста
from aiohttp import web 
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8583363803:AAFtkD-J0vq8uR6kyJPxO00SH1TSn8fIDUo" 

ADMIN_IDS = [
    1945747968,   
    6928797177    
]

PAYMENT_CARDS = [
    "5355 2800 2484 3821",  
    "5232 4410 2403 2182"   
]

DB_FILE = "shop.db"

# --- WEB SERVER (Для Render) ---
async def handle(request):
    return web.Response(text="Bot is alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- БАЗА ДАННЫХ ---
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            referral_count INTEGER DEFAULT 0
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            short_id TEXT,
            user_id INTEGER,
            username TEXT,
            product TEXT,
            weight TEXT,
            final_price INTEGER,
            contact_info TEXT,
            check_file_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            discount_percent INTEGER,
            is_reusable INTEGER DEFAULT 1
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_name TEXT,
            product_name TEXT,
            weight TEXT,
            price INTEGER
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY
        )
        """)
        conn.commit()
        populate_initial_products()

def populate_initial_products():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products")
        if cursor.fetchone()[0] > 0: return
        
        # Начальные товары
        OLD_PRODUCTS = {
            "Шишки АК-47 (ИНДИКА)": { "1.0г": 400 },
            "Мефедрон VHQ": { "1.0г": 700 },
            "Alfa pvp": { "1.0г": 600 }
        }
        for full_name, weights in OLD_PRODUCTS.items():
            cat = full_name.split()[0]
            for w, p in weights.items():
                cursor.execute("INSERT INTO products (category_name, product_name, weight, price) VALUES (?, ?, ?, ?)",
                               (cat, full_name, w, p))
        conn.commit()

# --- SQL WRAPPERS ---
def add_user(uid, uname, ref=None):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (uid, uname))
        if ref and ref != uid:
             conn.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (ref,))

def get_user(uid):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone() or {})

def get_stats():
    with sqlite3.connect(DB_FILE) as conn:
        u = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        b = conn.execute("SELECT COUNT(*) FROM blacklist").fetchone()[0]
        return u, b

def add_order(data):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""INSERT INTO orders (order_id, short_id, user_id, username, product, weight, final_price, contact_info, check_file_id)
        VALUES (:order_id, :short_id, :user_id, :username, :product, :weight, :final_price, :contact_info, :check_file_id)""", data)

def get_cats():
    with sqlite3.connect(DB_FILE) as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT category_name FROM products").fetchall()]

def get_prods(cat):
    with sqlite3.connect(DB_FILE) as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT product_name FROM products WHERE category_name=?", (cat,)).fetchall()]

def get_weights(name):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM products WHERE product_name=?", (name,)).fetchall()]

def get_prod_by_id(pid):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone())

def add_product_db(cat, name, weight, price):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT INTO products (category_name, product_name, weight, price) VALUES (?,?,?,?)", (cat, name, weight, price))

def del_product_db(pid):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM products WHERE id=?", (pid,))

def get_all_products():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM products").fetchall()]

# --- FSM STATES ---
class UserState(StatesGroup):
    captcha = State()
    cat = State()
    prod = State()
    weight = State()
    promo = State()
    pay = State()
    contact = State()
    support = State()

class AdminState(StatesGroup):
    broadcast = State()
    # Promo
    promo_code = State()
    promo_percent = State()
    promo_del = State()
    # Product
    prod_cat = State()
    prod_name = State()
    prod_weight = State()
    prod_price = State()
    # Block
    block_id = State()
    unblock_id = State()
    # Support
    answer_user = State()

# --- KEYBOARDS ---
def main_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🛍️ Каталог", callback_data="catalog")
    b.button(text="👤 Профиль", callback_data="profile")
    b.button(text="💬 Поддержка", callback_data="support")
    return b.adjust(1).as_markup()

def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статистика", callback_data="adm:stats")
    b.button(text="📣 Рассылка", callback_data="adm:broadcast")
    b.button(text="🎟️ Промокоды", callback_data="adm:promo")
    b.button(text="📦 Товары", callback_data="adm:products")
    b.button(text="🚫 Бан", callback_data="adm:ban")
    return b.adjust(1).as_markup()

def cancel_kb(cb):
    return InlineKeyboardBuilder().button(text="⬅️ Назад", callback_data=cb).as_markup()

# --- ROUTER ---
router = Router()

# 1. START & CAPTCHA
@router.message(CommandStart())
async def cmd_start(m: types.Message, state: FSMContext):
    user = get_user(m.from_user.id)
    if user:
        await m.answer(f"👋 Привет, <b>{html.escape(m.from_user.first_name)}</b>!", reply_markup=main_kb())
    else:
        n1, n2 = random.randint(1,5), random.randint(1,5)
        await state.update_data(cap=n1+n2)
        ref = m.text.split()[1] if len(m.text.split()) > 1 else None
        await state.update_data(ref=ref)
        await state.set_state(UserState.captcha)
        await m.answer(f"🤖 Проверка: сколько будет {n1} + {n2}?")

@router.message(UserState.captcha)
async def captcha_check(m: types.Message, state: FSMContext):
    d = await state.get_data()
    if m.text.strip() == str(d['cap']):
        add_user(m.from_user.id, m.from_user.username, d.get('ref'))
        await state.clear()
        await m.answer("✅ Доступ разрешен!", reply_markup=main_kb())
    else:
        await m.answer("❌ Ошибка. Попробуйте /start заново.")

# 2. MAIN MENU
@router.callback_query(F.data == "main_menu")
async def back_main(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text(f"👋 Привет, <b>{html.escape(c.from_user.first_name)}</b>!", reply_markup=main_kb())

@router.callback_query(F.data == "profile")
async def show_profile(c: types.CallbackQuery, bot: Bot):
    u = get_user(c.from_user.id)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={c.from_user.id}"
    await c.message.edit_text(
        f"👤 <b>Ваш ID:</b> <code>{c.from_user.id}</code>\n"
        f"👥 <b>Рефералов:</b> {u.get('referral_count', 0)}\n"
        f"🔗 <b>Ссылка:</b> <code>{link}</code>",
        reply_markup=cancel_kb("main_menu"), parse_mode="HTML"
    )

# 3. CATALOG & BUYING
@router.callback_query(F.data == "catalog")
async def show_cats(c: types.CallbackQuery):
    cats = get_cats()
    b = InlineKeyboardBuilder()
    for cat in cats: b.button(text=cat, callback_data=f"cat:{cat}")
    b.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu"))
    await c.message.edit_text("🛍️ Выберите категорию:", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("cat:"))
async def show_prods(c: types.CallbackQuery, state: FSMContext):
    cat = c.data.split(":")[1]
    await state.update_data(cat=cat)
    prods = get_prods(cat)
    b = InlineKeyboardBuilder()
    for p in prods: b.button(text=p, callback_data=f"prod:{p}")
    b.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog"))
    await c.message.edit_text(f"📂 Категория: {cat}", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("prod:"))
async def show_weights(c: types.CallbackQuery, state: FSMContext):
    pname = c.data.split(":")[1]
    weights = get_weights(pname)
    b = InlineKeyboardBuilder()
    for w in weights: 
        b.button(text=f"{w['weight']} - {w['price']} грн", callback_data=f"w:{w['id']}")
    b.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"cat:{weights[0]['category_name']}"))
    await c.message.edit_text(f"💊 Товар: {pname}", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("w:"))
async def ask_promo(c: types.CallbackQuery, state: FSMContext):
    pid = c.data.split(":")[1]
    prod = get_prod_by_id(pid)
    await state.update_data(prod=prod, final_price=prod['price'])
    await state.set_state(UserState.promo)
    b = InlineKeyboardBuilder().button(text="Пропустить", callback_data="skip_promo")
    await c.message.edit_text(f"💳 К оплате: <b>{prod['price']} грн</b>\n👇 Введите промокод или нажмите Пропустить:", reply_markup=b.as_markup(), parse_mode="HTML")

@router.message(UserState.promo)
async def check_promo(m: types.Message, state: FSMContext):
    code = m.text.strip()
    with sqlite3.connect(DB_FILE) as conn:
        res = conn.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
    
    if res:
        d = await state.get_data()
        disc = int(res[1])
        new_price = int(d['final_price'] * (1 - disc/100))
        await state.update_data(final_price=new_price, promo=code)
        await m.answer(f"✅ Скидка {disc}% применена!")
        await send_pay_info(m, state, new_price)
    else:
        await m.answer("❌ Неверный код. Попробуйте еще или нажмите Пропустить.")

@router.callback_query(F.data == "skip_promo")
async def skip_promo_cb(c: types.CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await send_pay_info(c.message, state, d['final_price'])

async def send_pay_info(m: types.Message, state: FSMContext, price):
    card = random.choice(PAYMENT_CARDS)
    await state.set_state(UserState.pay)
    msg = (f"💳 Переведите <b>{price} грн</b> на карту:\n"
           f"<code>{card}</code>\n\n"
           f"⚠️ После оплаты пришлите <b>скриншот чека</b>.")
    if isinstance(m, types.CallbackQuery): await m.message.edit_text(msg, parse_mode="HTML") # Fix for callback
    else: await m.answer(msg, parse_mode="HTML")

@router.message(UserState.pay, F.photo)
async def get_check(m: types.Message, state: FSMContext, bot: Bot):
    await state.update_data(check_id=m.photo[-1].file_id)
    await state.set_state(UserState.contact)
    await m.answer("📞 Теперь отправьте ваш номер телефона или @username для связи.")

@router.message(UserState.contact)
async def finish_order(m: types.Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    contact = m.text or "No contact"
    oid = str(uuid.uuid4())
    sid = oid[:8]
    
    add_order({
        "order_id": oid, "short_id": sid, "user_id": m.from_user.id,
        "username": m.from_user.username, "product": d['prod']['product_name'],
        "weight": d['prod']['weight'], "final_price": d['final_price'],
        "contact_info": contact, "check_file_id": d['check_id']
    })
    
    # Notify Admins
    for admin in ADMIN_IDS:
        try:
            txt = f"🚨 <b>НОВЫЙ ЗАКАЗ #{sid}</b>\n👤 {contact}\n🛒 {d['prod']['product_name']} ({d['prod']['weight']})\n💰 {d['final_price']} грн"
            kb = InlineKeyboardBuilder()
            kb.button(text="✅ Подтвердить", callback_data=f"adm_ok:{oid}")
            await bot.send_photo(admin, d['check_id'], caption=txt, reply_markup=kb.as_markup(), parse_mode="HTML")
        except: pass
        
    await m.answer("✅ Заказ принят! Ожидайте подтверждения.", reply_markup=main_kb())
    await state.clear()

# 4. SUPPORT
@router.callback_query(F.data == "support")
async def start_sup(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(UserState.support)
    await c.message.edit_text("✍️ Напишите ваш вопрос:", reply_markup=cancel_kb("main_menu"))

@router.message(UserState.support)
async def send_to_admin(m: types.Message, bot: Bot):
    kb = InlineKeyboardBuilder().button(text="Ответить", callback_data=f"ans:{m.from_user.id}").as_markup()
    for admin in ADMIN_IDS:
        try: await m.copy_to(admin, reply_markup=kb)
        except: pass
    await m.answer("Сообщение отправлено!")

# --- ADMIN PANEL LOGIC ---

@router.message(Command("admin"))
async def admin_start(m: types.Message):
    if m.from_user.id in ADMIN_IDS:
        await m.answer("🛡️ Админ-панель", reply_markup=admin_kb())

@router.callback_query(F.data == "admin_menu")
async def back_admin(c: types.CallbackQuery):
    await c.message.edit_text("🛡️ Админ-панель", reply_markup=admin_kb())

# A. Stats
@router.callback_query(F.data == "adm:stats")
async def show_stats(c: types.CallbackQuery):
    u, b = get_stats()
    await c.message.edit_text(f"📊 <b>Статистика:</b>\n👥 Юзеров: {u}\n🚫 В бане: {b}", 
                              reply_markup=cancel_kb("admin_menu"), parse_mode="HTML")

# B. Products
@router.callback_query(F.data == "adm:products")
async def adm_prods(c: types.CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить", callback_data="prod:add")
    b.button(text="➖ Удалить", callback_data="prod:del_menu")
    b.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu"))
    await c.message.edit_text("📦 Управление товарами", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data == "prod:add")
async def add_prod_start(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.prod_cat)
    await c.message.edit_text("Введите категорию товара:")

@router.message(AdminState.prod_cat)
async def get_cat(m: types.Message, state: FSMContext):
    await state.update_data(cat=m.text)
    await state.set_state(AdminState.prod_name)
    await m.answer("Введите название товара:")

@router.message(AdminState.prod_name)
async def get_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    await state.set_state(AdminState.prod_weight)
    await m.answer("Введите вес/фасовку (напр. 1.0г):")

@router.message(AdminState.prod_weight)
async def get_weight(m: types.Message, state: FSMContext):
    await state.update_data(weight=m.text)
    await state.set_state(AdminState.prod_price)
    await m.answer("Введите цену (число):")

@router.message(AdminState.prod_price)
async def save_prod(m: types.Message, state: FSMContext):
    try:
        price = int(m.text)
        d = await state.get_data()
        add_product_db(d['cat'], d['name'], d['weight'], price)
        await m.answer("✅ Товар добавлен!", reply_markup=admin_kb())
        await state.clear()
    except:
        await m.answer("❌ Цена должна быть числом.")

@router.callback_query(F.data == "prod:del_menu")
async def del_prod_menu(c: types.CallbackQuery):
    prods = get_all_products()
    b = InlineKeyboardBuilder()
    for p in prods:
        b.button(text=f"❌ {p['product_name']} ({p['weight']})", callback_data=f"del_p:{p['id']}")
    b.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:products"))
    await c.message.edit_text("Нажми, чтобы удалить:", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("del_p:"))
async def delete_process(c: types.CallbackQuery):
    pid = c.data.split(":")[1]
    del_product_db(pid)
    await c.answer("Удалено!")
    await adm_prods(c) # Refresh menu

# C. Promo Codes
@router.callback_query(F.data == "adm:promo")
async def adm_promo(c: types.CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text="➕ Создать промокод", callback_data="promo:new")
    b.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu"))
    await c.message.edit_text("🎟️ Промокоды", reply_markup=b.as_markup())

@router.callback_query(F.data == "promo:new")
async def new_promo(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.promo_code)
    await c.message.edit_text("Введите код (слово):")

@router.message(AdminState.promo_code)
async def save_promo_code(m: types.Message, state: FSMContext):
    await state.update_data(code=m.text)
    await state.set_state(AdminState.promo_percent)
    await m.answer("Введите процент скидки (1-99):")

@router.message(AdminState.promo_percent)
async def save_promo_fin(m: types.Message, state: FSMContext):
    try:
        perc = int(m.text)
        d = await state.get_data()
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("INSERT INTO promo_codes (code, discount_percent) VALUES (?,?)", (d['code'], perc))
        await m.answer("✅ Промокод создан!", reply_markup=admin_kb())
        await state.clear()
    except: await m.answer("Ошибка. Введите число.")

# D. Ban System
@router.callback_query(F.data == "adm:ban")
async def adm_ban(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.block_id)
    await c.message.edit_text("Введите ID пользователя для бана:", reply_markup=cancel_kb("admin_menu"))

@router.message(AdminState.block_id)
async def ban_user(m: types.Message, state: FSMContext):
    try:
        uid = int(m.text)
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("INSERT OR REPLACE INTO blacklist (user_id) VALUES (?)", (uid,))
        await m.answer(f"🚫 Пользователь {uid} забанен.", reply_markup=admin_kb())
        await state.clear()
    except: await m.answer("Нужен цифровой ID.")

# E. Admin Reply to Support
@router.callback_query(F.data.startswith("ans:"))
async def adm_answer_start(c: types.CallbackQuery, state: FSMContext):
    uid = c.data.split(":")[1]
    await state.update_data(uid=uid)
    await state.set_state(AdminState.answer_user)
    await c.message.answer(f"✍️ Введите ответ для {uid}:")

@router.message(AdminState.answer_user)
async def adm_send_answer(m: types.Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    try:
        await bot.send_message(d['uid'], f"🔔 <b>Ответ поддержки:</b>\n{m.text}", parse_mode="HTML")
        await m.answer("Отправлено!")
    except:
        await m.answer("Не удалось отправить (юзер заблочил бота).")
    await state.clear()

# F. Confirm Order
@router.callback_query(F.data.startswith("adm_ok:"))
async def confirm_order(c: types.CallbackQuery, bot: Bot):
    oid = c.data.split(":")[1]
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE orders SET status='confirmed' WHERE order_id=?", (oid,))
        row = conn.execute("SELECT user_id, short_id FROM orders WHERE order_id=?", (oid,)).fetchone()
    
    if row:
        try: await bot.send_message(row[0], f"✅ Ваш заказ #{row[1]} подтвержден! Ждите клад/доставку.")
        except: pass
    await c.message.edit_caption(caption=c.message.caption + "\n\n✅ <b>ПОДТВЕРЖДЕНО</b>", parse_mode="HTML")

# --- MIDDLEWARE (Blacklist) ---
class BlacklistMiddleware:
    async def __call__(self, handler, event, data):
        if isinstance(event, (types.Message, types.CallbackQuery)):
            uid = event.from_user.id
            if uid not in ADMIN_IDS:
                with sqlite3.connect(DB_FILE) as conn:
                    if conn.execute("SELECT 1 FROM blacklist WHERE user_id=?", (uid,)).fetchone():
                        return
        return await handler(event, data)

# --- STARTUP ---
async def main():
    init_db()
    asyncio.create_task(start_webserver())
    
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.update.middleware(BlacklistMiddleware())
    dp.include_router(router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
