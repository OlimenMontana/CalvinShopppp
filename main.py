import asyncio
import logging
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
from aiogram.fsm.storage.memory import MemoryStorage

# --- КОНФИГ ---
BOT_TOKEN = "8583363803:AAFtkD-J0vq8uR6kyJPxO00SH1TSn8fIDUo"
ADMIN_IDS = [1945747968, 6928797177]
DB_URL = "postgresql://shop_db_user_uefj_user:7qQoHt898FxFN7gXwLZa2ye4aC2nJ8O1@dpg-d5cqlaf5r7bs73besps0-a.virginia-postgres.render.com/shop_db_user_uefj" 
PAYMENT_CARDS = ["5355 2800 2484 3821", "5232 4410 2403 2182"]

# --- СОСТОЯНИЯ ---
class Order(StatesGroup): prod = State(); weight = State(); promo_choice = State(); promo_enter = State(); check = State()
class UserSup(StatesGroup): wait_q = State()
class AdminSup(StatesGroup): in_chat = State(); target = State()
class Auth(StatesGroup): captcha = State()
class AdminFSM(StatesGroup): broadcast = State(); promo_name = State(); promo_perc = State()

router = Router()

# --- БД POSTGRESQL ---
def db_query(sql, params=(), fetch=False, fetch_all=False):
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(sql, params)
                if fetch: return dict(cur.fetchone()) if cur.rowcount > 0 else None
                if fetch_all: return [dict(r) for r in cur.fetchall()]
                conn.commit()
                return cur.rowcount
    except Exception as e:
        logging.error(f"DB Error: {e}")
        return None

def init_db():
    queries = [
        "CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, username TEXT, referrer_id BIGINT, referral_count INTEGER DEFAULT 0, has_purchased BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, short_id TEXT, user_id BIGINT, username TEXT, product TEXT, weight TEXT, final_price INTEGER, check_file_id TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS products (id SERIAL PRIMARY KEY, product_name TEXT, weight TEXT, price INTEGER)",
        "CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, discount INTEGER)",
        "CREATE TABLE IF NOT EXISTS blacklist (user_id BIGINT PRIMARY KEY, reason TEXT)"
    ]
    for q in queries: db_query(q)
    
    db_query("DELETE FROM products")
    items = [
        ("Шишки АК-47 (ИНДИКА)", "1.0г", 400), ("Шишки АК-47 (ИНДИКА)", "2.0г", 750),
        ("Шишки АК-47 (САТИВА)", "1.0г", 450), ("Шишки АК-47 (САТИВА)", "2.0г", 850),
        ("Гашиш АФГАН", "1.0г", 500), ("Гашиш АФГАН", "3.0г", 1350),
        ("Киф АФГАН", "1.0г", 600), ("Амфетамин VHQ", "1.0г", 700),
        ("Мефедрон VHQ", "1.0г", 700), ("Метадон Уличный", "1.0г", 800),
        ("Экстази Домино", "1 шт", 450), ("Грибы", "1.0г", 450),
        ("ЛСД-300", "1 шт", 500), ("МДМА", "1.0г", 500),
        ("Alfa pvp", "1.0г", 600), ("Гер", "0.5г", 900),
        ("Винт", "5мг", 1200), ("Мушрум", "1 шт", 450),
        ("Кетамин", "1.0г", 500), ("D-meth", "0.25г", 600),
        ("Кокаїн", "0.25г", 1000)
    ]
    for p in items: db_query("INSERT INTO products (product_name, weight, price) VALUES (%s, %s, %s)", p)

# --- КЛАВИАТУРЫ ---
def kb_main():
    b = InlineKeyboardBuilder()
    b.button(text="🛍️ КАТАЛОГ", callback_data="catalog")
    b.button(text="👤 МОЙ ПРОФИЛЬ", callback_data="profile")
    b.button(text="💬 ПОДДЕРЖКА", callback_data="support")
    return b.adjust(1).as_markup()

# --- ХЕНДЛЕРЫ ---
@router.message(CommandStart())
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    u = db_query("SELECT * FROM users WHERE user_id=%s", (m.from_user.id,), fetch=True)
    if u: await m.answer(f"👋 Привет, {m.from_user.first_name}!", reply_markup=kb_main())
    else:
        n1, n2 = random.randint(1,9), random.randint(1,9)
        ref = int(m.text.split()[1]) if len(m.text.split()) > 1 and m.text.split()[1].isdigit() else None
        await state.update_data(ans=n1+n2, ref=ref)
        await state.set_state(Auth.captcha)
        await m.answer(f"🛡️ Решите пример: `{n1} + {n2} = ?`", parse_mode="Markdown")

@router.message(Auth.captcha)
async def check_captcha(m: types.Message, state: FSMContext):
    data = await state.get_data()
    if m.text == str(data.get('ans')):
        db_query("INSERT INTO users (user_id, username, referrer_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (m.from_user.id, m.from_user.username, data.get('ref')))
        await state.clear(); await m.answer("✅ Доступ открыт!", reply_markup=kb_main())
    else: await m.answer("❌ Ошибка. /start")

# --- КАТАЛОГ ---
@router.callback_query(F.data == "catalog")
async def catalog(call: types.CallbackQuery, state: FSMContext):
    prods = db_query("SELECT DISTINCT product_name FROM products", fetch_all=True)
    b = InlineKeyboardBuilder()
    for p in prods: b.button(text=p['product_name'], callback_data=f"p:{p['product_name']}")
    await state.set_state(Order.prod)
    await call.message.edit_text("🛒 Выберите товар:", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("p:"), Order.prod)
async def weights(call: types.CallbackQuery, state: FSMContext):
    pn = call.data.split(":")[1]
    vs = db_query("SELECT id, weight, price FROM products WHERE product_name=%s", (pn,), fetch_all=True)
    b = InlineKeyboardBuilder()
    for v in vs: b.button(text=f"{v['weight']} — {v['price']} грн", callback_data=f"w:{v['id']}")
    await state.set_state(Order.weight)
    await call.message.edit_text(f"💎 {pn}:", reply_markup=b.adjust(1).as_markup())

# --- НОВАЯ СИСТЕМА ПРОМОКОДОВ ---
@router.callback_query(F.data.startswith("w:"), Order.weight)
async def ask_promo(call: types.CallbackQuery, state: FSMContext):
    it = db_query("SELECT * FROM products WHERE id=%s", (int(call.data.split(":")[1]),), fetch=True)
    await state.update_data(it=it, price=it['price'])
    
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, есть", callback_data="promo:yes")
    b.button(text="❌ Нет", callback_data="promo:no")
    await state.set_state(Order.promo_choice)
    await call.message.edit_text("🎫 У вас есть промокод на скидку?", reply_markup=b.as_markup())

@router.callback_query(F.data == "promo:yes", Order.promo_choice)
async def enter_promo(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(Order.promo_enter)
    await call.message.edit_text("⌨️ Введите ваш промокод:")

@router.message(Order.promo_enter)
async def check_promo(m: types.Message, state: FSMContext):
    promo = db_query("SELECT * FROM promo_codes WHERE code=%s", (m.text.strip(),), fetch=True)
    data = await state.get_data()
    
    if promo:
        discount = promo['discount']
        new_price = int(data['price'] * (1 - discount / 100))
        await state.update_data(price=new_price)
        await m.answer(f"✅ Промокод принят! Скидка {discount}%\nНовая цена: {new_price} грн")
    else:
        await m.answer("❌ Промокод не найден или истек. Цена остается прежней.")
    
    await proceed_to_payment(m, state)

@router.callback_query(F.data == "promo:no", Order.promo_choice)
async def no_promo(call: types.CallbackQuery, state: FSMContext):
    await proceed_to_payment(call.message, state)

async def proceed_to_payment(m, state: FSMContext):
    data = await state.get_data()
    card = random.choice(PAYMENT_CARDS)
    await state.set_state(Order.check)
    txt = (f"💳 **ОПЛАТА**\n\nТовар: {data['it']['product_name']} ({data['it']['weight']})\n"
           f"💰 К оплате: `{data['price']} грн`\n\nРеквизиты:\n`{card}`\n\nПришлите фото чека:")
    if isinstance(m, types.Message): await m.answer(txt, parse_mode="Markdown")
    else: await m.edit_text(txt, parse_mode="Markdown")

# --- ХЕНДЛЕР ЧЕКА ---
@router.message(Order.check, F.photo)
async def get_check(m: types.Message, state: FSMContext, bot: Bot):
    d = await state.get_data(); oid = str(uuid.uuid4())[:8]
    db_query("INSERT INTO orders (order_id, short_id, user_id, username, product, weight, final_price, check_file_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
             (str(uuid.uuid4()), oid, m.from_user.id, m.from_user.username, d['it']['product_name'], d['it']['weight'], d['price'], m.photo[-1].file_id))
    
    kb = InlineKeyboardBuilder().button(text="✅ Одобрить", callback_data=f"ok:{oid}").button(text="❌ Отклонить", callback_data=f"no:{oid}")
    for a in ADMIN_IDS:
        try: await bot.send_photo(a, m.photo[-1].file_id, caption=f"🆕 #{oid}\n💰 {d['price']} грн\n👤 @{m.from_user.username}", reply_markup=kb.as_markup())
        except: pass
    await m.answer(f"⏳ Чек #{oid} отправлен на проверку!"); await state.clear()

# --- АДМИНКА: СОЗДАНИЕ ПРОМОКОДОВ ---
@router.message(Command("admin"), F.from_user.id.in_(ADMIN_IDS))
async def adm_panel(m: types.Message):
    b = InlineKeyboardBuilder()
    b.button(text="📢 Рассылка", callback_data="adm:bc")
    b.button(text="🎫 Создать промокод", callback_data="adm:promo")
    b.button(text="📊 Статистика", callback_data="adm:stats")
    await m.answer("🔧 АДМИН-ПАНЕЛЬ", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data == "adm:promo", F.from_user.id.in_(ADMIN_IDS))
async def adm_promo_start(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminFSM.promo_name)
    await call.message.answer("Введите название промокода (например, SALE20):")

@router.message(AdminFSM.promo_name)
async def adm_promo_name(m: types.Message, state: FSMContext):
    await state.update_data(p_name=m.text.strip())
    await state.set_state(AdminFSM.promo_perc)
    await m.answer("Введите процент скидки (только число, например 15):")

@router.message(AdminFSM.promo_perc)
async def adm_promo_final(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Введите число!")
    data = await state.get_data()
    db_query("INSERT INTO promo_codes (code, discount) VALUES (%s, %s) ON CONFLICT (code) DO UPDATE SET discount=%s", 
             (data['p_name'], int(m.text), int(m.text)))
    await state.clear()
    await m.answer(f"✅ Промокод `{data['p_name']}` на {m.text}% успешно создан!", parse_mode="Markdown")

# --- ОСТАЛЬНЫЕ АДМИН-ФУНКЦИИ (Рассылка, Одобрение) ---
@router.callback_query(F.data == "adm:bc", F.from_user.id.in_(ADMIN_IDS))
async def bc_start(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminFSM.broadcast); await call.message.answer("📢 Введите текст рассылки:")

@router.message(AdminFSM.broadcast, F.from_user.id.in_(ADMIN_IDS))
async def bc_do(m: types.Message, state: FSMContext):
    users = db_query("SELECT user_id FROM users", fetch_all=True)
    for u in users:
        try: await m.copy_to(u['user_id']); await asyncio.sleep(0.05)
        except: pass
    await state.clear(); await m.answer("✅ Рассылка завершена.")

@router.callback_query(F.data.startswith("ok:"), F.from_user.id.in_(ADMIN_IDS))
async def approve(call: types.CallbackQuery, bot: Bot):
    oid = call.data.split(":")[1]
    o = db_query("SELECT * FROM orders WHERE short_id=%s", (oid,), fetch=True)
    if o:
        db_query("UPDATE orders SET status='ok' WHERE short_id=%s", (oid,))
        try: await bot.send_message(o['user_id'], f"✅ Заказ #{oid} подтвержден!")
        except: pass
        await call.message.edit_caption(caption=f"✅ Заказ {oid} Одобрен")

# --- СТАРТ ---
async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
    dp = Dispatcher(storage=MemoryStorage()); dp.include_router(router); init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
