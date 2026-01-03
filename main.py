import asyncio
import logging
import re
import uuid 
import random 
import sqlite3
import os
from aiohttp import web # Для веб-сервера (Render требует порт)
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import CommandStart, StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Dict, Any, Callable, Awaitable
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError 
from aiogram.types import BotCommand, TelegramObject 
from aiogram.enums import ChatAction 
from aiogram.fsm.storage.memory import MemoryStorage

# --- Конфигурация ---
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

# --- Веб-сервер для Render (чтобы сервис не засыпал) ---
async def handle(request):
    return web.Response(text="Bot is alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    # Render передает порт в переменную окружения PORT
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# ----------------------------------------------------------------------
# --- ЛОГИКА БАЗЫ ДАННЫХ ---
# ----------------------------------------------------------------------
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            referrer_id INTEGER,
            referral_count INTEGER DEFAULT 0,
            has_purchased INTEGER DEFAULT 0,
            referral_reward_claimed INTEGER DEFAULT 0,
            blocked_bot INTEGER DEFAULT 0 
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
            original_price INTEGER,
            final_price INTEGER,
            promo_code_used TEXT,
            contact_info TEXT,
            check_file_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY NOT NULL UNIQUE,
            discount_percent INTEGER NOT NULL,
            is_reusable INTEGER DEFAULT 1,
            owner_id INTEGER
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_name TEXT NOT NULL,
            product_name TEXT NOT NULL,
            weight TEXT NOT NULL,
            price INTEGER NOT NULL
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        populate_initial_products()

def populate_initial_products():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products")
        if cursor.fetchone()[0] > 0:
            return
        
        OLD_PRODUCTS = {
            "Шишки АК-47 (ИНДИКА)": { "1.0г": 400 },
            "Шишки АК-47(САТИВА)": { "1.0г": 450 },
            "Гашиш АФГАН": { "1.0г": 500 },
            "Киф АФГАН": { "1.0г": 600 },
            "Амфетамин VHQ": { "1.0г": 700 },
            "Мефедрон VHQ": { "1.0г": 700 },
            "Метадон Уличный": { "1.0г": 800 },
            "Экстази Домино": { "1 шт": 450 },
            "Грибы": { "1.0г": 450 },
            "ЛСД-300": { "1 шт.": 500 },
            "МДМА": { "1.0г.": 500 },
            "Alfa pvp": { "1.0г": 600 },
            "Гер": { "0.5г": 900 },
            "Винт": { "5мг": 1200 },
            "Мушрум": { "1шт": 450 },
            "Кетамин": { "1.0г": 500 },
            "D-mesth": { "0.25г": 600 },
            "Кокаин": { "0.25": 1000 },
        }
        for full_name, weights_dict in OLD_PRODUCTS.items():
            parts = full_name.split(maxsplit=1)
            category = parts[0] if len(parts) == 2 else full_name
            product_name = parts[1] if len(parts) == 2 else full_name
            for weight, price in weights_dict.items():
                cursor.execute(
                    "INSERT INTO products (category_name, product_name, weight, price) VALUES (?, ?, ?, ?)",
                    (category.strip(), product_name.strip(), weight.strip(), price)
                )
        conn.commit()

# --- Вспомогательные функции БД ---
def add_user_to_db(user_id: int, username: str, referrer_id: int | None = None):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)", (user_id, username, referrer_id))
        conn.commit()

def is_user_verified(user_id: int) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

def get_user_data_db(user_id: int) -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_user_count() -> int:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(user_id) FROM users")
        return cursor.fetchone()[0]

def get_all_user_ids_db() -> list[int]:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        return [row[0] for row in cursor.fetchall()]

def set_user_has_purchased(user_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET has_purchased = 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def increment_referrer_count(referrer_id: int) -> int:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (referrer_id,))
        conn.commit()
        cursor.execute("SELECT referral_count FROM users WHERE user_id = ?", (referrer_id,))
        result = cursor.fetchone()
        return result[0] if result else 0

def reset_referral_count(user_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET referral_count = 0, referral_reward_claimed = referral_reward_claimed + 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def set_user_blocked_bot_db(user_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET blocked_bot = 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def get_blocked_bot_count_db() -> int:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE blocked_bot = 1")
        return cursor.fetchone()[0]

def add_to_blacklist_db(user_id: int, reason: str = 'Blocked by admin') -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT OR REPLACE INTO blacklist (user_id, reason) VALUES (?, ?)", (user_id, reason))
            conn.commit()
            return True
        except: return False

def remove_from_blacklist_db(user_id: int) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0

def is_user_blacklisted_db(user_id: int) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

def get_blocked_user_count_db() -> int:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM blacklist")
        return cursor.fetchone()[0]

def create_db_order(order_data: dict) -> str:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO orders (order_id, short_id, user_id, username, product, weight, 
                            original_price, final_price, promo_code_used, 
                            contact_info, check_file_id, status)
        VALUES (:order_id, :short_id, :user_id, :username, :product, :weight, 
                :original_price, :final_price, :promo_code_used,
                :contact_info, :check_file_id, 'pending')
        """, order_data)
        conn.commit()
    return order_data['short_id']

def get_pending_orders_db() -> list:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row 
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at ASC")
        return [dict(row) for row in cursor.fetchall()]

def get_order_db(order_id: str) -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_order_status_db(order_id: str, status: str) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))
        conn.commit()
        return cursor.rowcount > 0

def add_promo_db(code: str, percent: int, is_reusable: bool = True, owner_id: int | None = None) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT OR REPLACE INTO promo_codes (code, discount_percent, is_reusable, owner_id) VALUES (?, ?, ?, ?)", 
                (code.upper(), percent, int(is_reusable), owner_id))
            conn.commit()
            return True
        except: return False

def del_promo_db(code: str) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM promo_codes WHERE code = ?", (code.upper(),))
        conn.commit()
        return cursor.rowcount > 0 

def get_promo_db(code: str) -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM promo_codes WHERE code = ?", (code.upper(),))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_all_promos_db() -> list:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM promo_codes")
        return [dict(row) for row in cursor.fetchall()]

def get_product_categories_db() -> list[str]:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT category_name FROM products ORDER BY category_name")
        return [row[0] for row in cursor.fetchall()]

def get_products_by_category_db(category_name: str) -> list[str]:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT product_name FROM products WHERE category_name = ? ORDER BY product_name", (category_name,))
        return [row[0] for row in cursor.fetchall()]

def get_weights_for_product_db(product_name: str) -> list[dict]:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, weight, price, category_name FROM products WHERE product_name = ? ORDER BY price", (product_name,))
        return [dict(row) for row in cursor.fetchall()]

def get_product_by_id_db(product_id: int) -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def add_product_db(category: str, name: str, weight: str, price: int) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO products (category_name, product_name, weight, price) VALUES (?, ?, ?, ?)", (category, name, weight, price))
            conn.commit()
            return True
        except: return False

def get_all_products_full_db() -> list[dict]:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM products ORDER BY category_name, product_name, price")
        return [dict(row) for row in cursor.fetchall()]

def delete_product_db(product_id: int) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        return cursor.rowcount > 0

# --- FSM СОСТОЯНИЯ ---
class AuthStates(StatesGroup):
    waiting_for_captcha = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast_message = State()
    waiting_for_promo_code_name = State()
    waiting_for_promo_code_percent = State()
    waiting_for_promo_code_delete = State()
    in_support = State()
    waiting_for_product_category = State()
    waiting_for_product_name = State()
    waiting_for_product_weight = State()
    waiting_for_product_price = State()
    waiting_for_product_delete = State()
    waiting_for_block_id = State()
    waiting_for_unblock_id = State()

class UserSupport(StatesGroup):
    waiting_for_question = State()
    in_support = State()

class OrderStates(StatesGroup):
    waiting_for_category = State()
    waiting_for_product = State()
    waiting_for_weight = State()
    waiting_for_promo_code = State() 
    waiting_for_payment_check = State()
    waiting_for_contact = State() 

# --- КЛАВИАТУРЫ ---
def get_main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍️ Каталог Товаров", callback_data="show_catalog")
    builder.button(text="👤 Мой Профиль / Рефералы", callback_data="show_profile")
    builder.button(text="💬 Написать Админу", callback_data="start_support")
    builder.adjust(1)
    return builder.as_markup()

def get_categories_keyboard(categories: list[str]):
    builder = InlineKeyboardBuilder()
    for cat in categories: builder.button(text=cat, callback_data=f"category:{cat}")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu_start"))
    builder.adjust(1)
    return builder.as_markup()

def get_products_keyboard(products: list[str]):
    builder = InlineKeyboardBuilder()
    for prod in products: builder.button(text=prod, callback_data=f"product:{prod}")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="show_catalog"))
    builder.adjust(1)
    return builder.as_markup()

def get_weights_keyboard(weights: list[dict]):
    builder = InlineKeyboardBuilder()
    cat = ""
    for item in weights:
        builder.button(text=f"{item['weight']} | {item['price']} грн", callback_data=f"weight:{item['id']}")
        cat = item['category_name']
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"category:{cat}"))
    builder.adjust(1)
    return builder.as_markup()

def get_promo_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить", callback_data="promo:skip")
    return builder.as_markup()

def get_user_cancel_support_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Отменить обращение ❌", callback_data="cancel_support")
    return builder.as_markup()

def get_user_close_chat_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Завершить чат 💬", callback_data="user_close_chat")
    return builder.as_markup()

def get_admin_close_chat_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Завершить чат ❌", callback_data="admin_close_chat")
    return builder.as_markup()

def get_client_back_to_main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад в Меню", callback_data="main_menu_start")
    return builder.as_markup()

def get_admin_order_keyboard(order_id: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=f"admin:confirm:{order_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin:decline:{order_id}")
    return builder.as_markup()

def get_admin_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="admin:stats")
    builder.button(text="📣 Рассылка", callback_data="admin:broadcast")
    builder.button(text="🎟️ Промокоды", callback_data="admin:promo_menu")
    builder.button(text="📦 Товары", callback_data="admin:prod_menu")
    builder.button(text="🚫 Бан", callback_data="admin:block_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_promo_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить", callback_data="promo:add")
    builder.button(text="➖ Удалить", callback_data="promo:delete")
    builder.button(text="📋 Список", callback_data="promo:list")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:main_menu"))
    builder.adjust(1)
    return builder.as_markup()

def get_admin_back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data="admin:main_menu")
    return builder.as_markup()

def get_product_admin_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить", callback_data="prod:add")
    builder.button(text="➖ Удалить", callback_data="prod:delete_list")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:main_menu"))
    builder.adjust(1)
    return builder.as_markup()

def get_product_delete_keyboard(products: list[dict]):
    builder = InlineKeyboardBuilder()
    if not products: builder.button(text="Пусто", callback_data="noop")
    else:
        for p in products: builder.button(text=f"❌ {p['product_name']} ({p['weight']})", callback_data=f"prod:del:{p['id']}")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:prod_menu"))
    builder.adjust(1)
    return builder.as_markup()

def get_block_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Бан по ID", callback_data="block:add")
    builder.button(text="➖ Разбан по ID", callback_data="block:remove")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:main_menu"))
    builder.adjust(1)
    return builder.as_markup()

# --- ХЕНДЛЕРЫ ---
router = Router()

async def send_captcha(message: types.Message, state: FSMContext, referrer_id: int | None = None):
    n1, n2 = random.randint(1, 10), random.randint(1, 10)
    await state.update_data(captcha_answer=n1+n2, referrer_id=referrer_id)
    await state.set_state(AuthStates.waiting_for_captcha)
    await message.answer(f"🤖 Привет, {message.from_user.first_name}!\nРеши пример: **{n1} + {n2} = ?**")

async def show_main_menu(m_or_c: types.Message | types.CallbackQuery, state: FSMContext, name: str):
    await state.clear()
    txt, kb = f"🛍️ **{name}, Главное меню**", get_main_menu_keyboard()
    if isinstance(m_or_c, types.CallbackQuery):
        try:
            await m_or_c.message.answer(txt, reply_markup=kb)
            await m_or_c.message.delete()
        except: pass
    else: await m_or_c.answer(txt, reply_markup=kb)

async def show_catalog(cb_or_m: types.CallbackQuery | types.Message, state: FSMContext, bot: Bot):
    await state.set_state(OrderStates.waiting_for_category)
    cats = get_product_categories_db()
    txt, kb = ("🛍️ Выберите категорию:", get_categories_keyboard(cats)) if cats else ("Пусто", get_client_back_to_main_menu_keyboard())
    if isinstance(cb_or_m, types.CallbackQuery): await cb_or_m.message.edit_text(txt, reply_markup=kb)
    else: await cb_or_m.answer(txt, reply_markup=kb)

async def send_payment_instructions(message: types.Message, state: FSMContext, bot: Bot):
    await state.set_state(OrderStates.waiting_for_payment_check)
    d = await state.get_data()
    card = random.choice(PAYMENT_CARDS)
    txt = (f"🔥 **Заказ:** {d['chosen_product']} ({d['chosen_weight']})\n"
           f"Цена: **{d['final_price']} грн**\n\n"
           f"Карта для оплаты: `{card}`\n\n"
           f"Пришли скриншот чека.")
    await message.answer(txt, reply_markup=get_client_back_to_main_menu_keyboard())

async def process_new_order(message: types.Message, state: FSMContext, bot: Bot, contact: str):
    d = await state.get_data()
    oid, sid = str(uuid.uuid4()), str(uuid.uuid4())[:8]
    order_data = {
        "order_id": oid, "short_id": sid, "user_id": message.from_user.id, "username": message.from_user.username or 'Нет',
        "product": d['chosen_product'], "weight": d['chosen_weight'], "original_price": d['original_price'],
        "final_price": d['final_price'], "promo_code_used": d.get('promo_code_used'),
        "contact_info": contact, "check_file_id": d['payment_check_file_id']
    }
    create_db_order(order_data)
    cap = f"🚨 **ЗАКАЗ #{sid}**\n{d['chosen_product']} | {d['final_price']}грн\nКонтакт: {contact}"
    for aid in ADMIN_IDS:
        try: await bot.send_photo(aid, d['payment_check_file_id'], caption=cap, reply_markup=get_admin_order_keyboard(oid))
        except: pass
    await message.answer(f"🎉 Заказ #{sid} принят!")
    await show_main_menu(message, state, message.from_user.first_name)

@router.message(CommandStart())
async def cmd_start(m: types.Message, s: FSMContext):
    ref = None
    try: ref = int(m.text.split()[1]) if len(m.text.split()) > 1 else None
    except: pass
    if is_user_verified(m.from_user.id): await show_main_menu(m, s, m.from_user.first_name)
    else: await send_captcha(m, s, ref)

@router.message(AuthStates.waiting_for_captcha, F.text)
async def proc_captcha(m: types.Message, s: FSMContext):
    d = await s.get_data()
    if m.text.strip() == str(d.get('captcha_answer')):
        add_user_to_db(m.from_user.id, m.from_user.username, d.get('referrer_id'))
        await m.answer("✅ Верно!")
        await show_main_menu(m, s, m.from_user.first_name)
    else: await m.answer("❌ Неверно"); await send_captcha(m, s, d.get('referrer_id'))

@router.callback_query(F.data == "main_menu_start")
async def cb_main(c: types.CallbackQuery, s: FSMContext): await show_main_menu(c, s, c.from_user.first_name)

@router.callback_query(F.data == "show_catalog")
async def cb_cat(c: types.CallbackQuery, s: FSMContext, bot: Bot): await show_catalog(c, s, bot)

@router.callback_query(F.data == "show_profile")
async def cb_prof(c: types.CallbackQuery, bot: Bot):
    u = get_user_data_db(c.from_user.id)
    me = await bot.get_me()
    lnk = f"https://t.me/{me.username}?start={c.from_user.id}"
    txt = f"👤 ID: `{c.from_user.id}`\nРефералов: {u['referral_count']}/5\nСсылка: `{lnk}`"
    await c.message.edit_text(txt, reply_markup=get_client_back_to_main_menu_keyboard())

@router.callback_query(F.data.startswith("category:"))
async def cb_sel_cat(c: types.CallbackQuery, s: FSMContext):
    cat = c.data.split(":")[1]
    await s.update_data(chosen_category=cat)
    await s.set_state(OrderStates.waiting_for_product)
    prods = get_products_by_category_db(cat)
    await c.message.edit_text(f"Выбрано: {cat}. Выберите товар:", reply_markup=get_products_keyboard(prods))

@router.callback_query(F.data.startswith("product:"))
async def cb_sel_prod(c: types.CallbackQuery, s: FSMContext):
    p = c.data.split(":")[1]
    await s.set_state(OrderStates.waiting_for_weight)
    ws = get_weights_for_product_db(p)
    await c.message.edit_text(f"Товар: {p}. Выберите вес:", reply_markup=get_weights_keyboard(ws))

@router.callback_query(F.data.startswith("weight:"))
async def cb_sel_w(c: types.CallbackQuery, s: FSMContext, bot: Bot):
    pid = int(c.data.split(":")[1])
    p = get_product_by_id_db(pid)
    await s.update_data(chosen_product=p['product_name'], chosen_weight=p['weight'], original_price=p['price'], final_price=p['price'])
    await s.set_state(OrderStates.waiting_for_promo_code)
    await c.message.edit_text(f"Цена: {p['price']}грн. Введите промокод или пропустите:", reply_markup=get_promo_keyboard())

@router.callback_query(F.data == "promo:skip")
async def cb_skip_p(c: types.CallbackQuery, s: FSMContext, bot: Bot): await send_payment_instructions(c.message, s, bot)

@router.message(OrderStates.waiting_for_promo_code, F.text)
async def proc_promo(m: types.Message, s: FSMContext, bot: Bot):
    p = get_promo_db(m.text.strip())
    if p:
        d = await s.get_data()
        new_p = round(d['original_price'] * (1 - p['discount_percent']/100))
        await s.update_data(final_price=new_p, promo_code_used=m.text.strip().upper())
        if not p['is_reusable']: del_promo_db(m.text.strip())
        await m.answer(f"✅ Скидка {p['discount_percent']}%!")
        await send_payment_instructions(m, s, bot)
    else: await m.answer("❌ Нет такого кода.")

@router.message(F.photo, OrderStates.waiting_for_payment_check)
async def proc_check(m: types.Message, s: FSMContext, bot: Bot):
    await s.update_data(payment_check_file_id=m.photo[-1].file_id)
    if m.from_user.username: await process_new_order(m, s, bot, f"@{m.from_user.username}")
    else:
        await s.set_state(OrderStates.waiting_for_contact)
        await m.answer("Пришли номер телефона для связи.")

@router.message(OrderStates.waiting_for_contact)
async def proc_cont(m: types.Message, s: FSMContext, bot: Bot):
    await process_new_order(m, s, bot, m.text or m.contact.phone_number)

# --- ПОДДЕРЖКА ---
@router.callback_query(F.data == "start_support")
async def cb_sup(c: types.CallbackQuery, s: FSMContext):
    await s.set_state(UserSupport.waiting_for_question)
    await c.message.edit_text("Опиши проблему:", reply_markup=get_client_back_to_main_menu_keyboard())

@router.message(UserSupport.waiting_for_question)
async def sup_q(m: types.Message, s: FSMContext, bot: Bot):
    kb = InlineKeyboardBuilder().button(text="Ответить", callback_data=f"admin_reply_to:{m.from_user.id}").as_markup()
    for aid in ADMIN_IDS:
        try: await m.copy_to(aid); await bot.send_message(aid, f"От: @{m.from_user.username}", reply_markup=kb)
        except: pass
    await s.set_state(UserSupport.in_support)
    await m.answer("Отправлено.")

@router.callback_query(F.data.startswith("admin_reply_to:"), F.from_user.id.in_(ADMIN_IDS))
async def adm_r(c: types.CallbackQuery, s: FSMContext, bot: Bot, dp: Dispatcher):
    uid = int(c.data.split(":")[1])
    await s.set_state(AdminStates.in_support)
    await s.update_data(chatting_with_user_id=uid)
    await c.message.answer(f"Чат с {uid}", reply_markup=get_admin_close_chat_keyboard())
    await bot.send_message(uid, "Админ на связи.")

@router.message(AdminStates.in_support, F.from_user.id.in_(ADMIN_IDS))
async def adm_msg(m: types.Message, s: FSMContext, bot: Bot):
    d = await s.get_data()
    try: await m.copy_to(d['chatting_with_user_id'], reply_markup=get_user_close_chat_keyboard())
    except: pass

@router.message(UserSupport.in_support)
async def usr_msg(m: types.Message, s: FSMContext, bot: Bot):
    for aid in ADMIN_IDS:
        try: await m.copy_to(aid)
        except: pass

@router.callback_query(F.data == "admin_close_chat")
async def adm_cls(c: types.CallbackQuery, s: FSMContext, bot: Bot):
    d = await s.get_data(); await s.clear(); await bot.send_message(d['chatting_with_user_id'], "Чат закрыт."); await c.message.answer("Закрыто.")

# --- АДМИНКА ---
@router.message(Command("admin"), F.from_user.id.in_(ADMIN_IDS))
async def adm_p(m: types.Message): await m.answer("🛡️ Админка", reply_markup=get_admin_main_keyboard())

@router.callback_query(F.data == "admin:main_menu")
async def adm_m(c: types.CallbackQuery, s: FSMContext): await s.clear(); await c.message.edit_text("🛡️ Админка", reply_markup=get_admin_main_keyboard())

@router.callback_query(F.data == "admin:stats")
async def adm_s(c: types.CallbackQuery):
    txt = f"Юзеров: {get_user_count()}\nБанов: {get_blocked_user_count_db()}"
    await c.message.edit_text(txt, reply_markup=get_admin_back_keyboard())

@router.callback_query(F.data == "admin:broadcast")
async def adm_b(c: types.CallbackQuery, s: FSMContext):
    await s.set_state(AdminStates.waiting_for_broadcast_message)
    await c.message.edit_text("Пришли сообщение для рассылки:")

@router.message(AdminStates.waiting_for_broadcast_message)
async def proc_b(m: types.Message, s: FSMContext, bot: Bot):
    ids = get_all_user_ids_db()
    for uid in ids:
        try: await m.copy_to(uid); await asyncio.sleep(0.05)
        except: pass
    await m.answer("Готово!"); await s.clear()

@router.callback_query(F.data.startswith("admin:confirm:"))
async def adm_conf(c: types.CallbackQuery, bot: Bot):
    oid = c.data.split(":")[2]
    o = get_order_db(oid)
    if o and o['status'] == 'pending':
        update_order_status_db(oid, "confirmed")
        try: await bot.send_message(o['user_id'], f"✅ Заказ #{o['short_id']} подтвержден!")
        except: pass
        await c.message.edit_caption(caption=c.message.caption + "\n✅ ОК")

# --- MIDDLEWARES ---
class DpMiddleware:
    def __init__(self, dp: Dispatcher): self.dp = dp
    async def __call__(self, handler, event, data):
        data['dp'] = self.dp
        return await handler(event, data)

class BlacklistMiddleware:
    async def __call__(self, handler, event, data):
        uid = event.from_user.id if hasattr(event, 'from_user') else None
        if uid and uid not in ADMIN_IDS and is_user_blacklisted_db(uid): return
        return await handler(event, data)

# --- ЗАПУСК ---
async def main():
    init_db()
    # Запускаем веб-сервер
    asyncio.create_task(start_webserver())
    
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    dp.update.middleware(DpMiddleware(dp))
    dp.update.middleware(BlacklistMiddleware())
    
    await bot.set_my_commands([BotCommand(command="start", description="Старт"), BotCommand(command="admin", description="Админ")])
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
