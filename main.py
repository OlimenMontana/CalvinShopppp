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
DB_URL = "postgresql://shop_db_user_user:PbLeivrMYwfcB8qFfL2VdbVXRKFNbZ89@dpg-d5cq2heuk2gs738ej7og-a/shop_db_user" # ВСТАВЬ СВОЙ URL ИЗ RENDER
PAYMENT_CARDS = ["5355 2800 2484 3821", "5232 4410 2403 2182"]

# --- БД POSTGRESQL ---
def init_db():
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, username TEXT, referrer_id BIGINT, referral_count INTEGER DEFAULT 0, has_purchased BOOLEAN DEFAULT FALSE)")
            cur.execute("CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, short_id TEXT, user_id BIGINT, username TEXT, product TEXT, weight TEXT, final_price INTEGER, contact_info TEXT, check_file_id TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS products (id SERIAL PRIMARY KEY, category_name TEXT, product_name TEXT, weight TEXT, price INTEGER)")
            cur.execute("CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, discount_percent INTEGER)")
            cur.execute("CREATE TABLE IF NOT EXISTS blacklist (user_id BIGINT PRIMARY KEY, reason TEXT)")
            
            cur.execute("SELECT COUNT(*) FROM products")
            if cur.fetchone()[0] == 0:
                prods = [("Шишки", "АК-47", "1.0г", 400), ("Гашиш", "АФГАН", "1.0г", 500), ("VHQ", "Мефедрон", "1.0г", 700)]
                for p in prods: cur.execute("INSERT INTO products (category_name, product_name, weight, price) VALUES (%s, %s, %s, %s)", p)
        conn.commit()

def db_query(sql, params=(), fetch=False, fetch_all=False):
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(sql, params)
            if fetch:
                row = cur.fetchone()
                return dict(row) if row else None
            if fetch_all:
                return [dict(row) for row in cur.fetchall()]
            conn.commit()
            return cur.rowcount

# --- СОСТОЯНИЯ ---
class Order(StatesGroup): cat = State(); prod = State(); weight = State(); promo = State(); check = State(); contact = State()
class UserSup(StatesGroup): wait_q = State(); in_chat = State()
class AdminSup(StatesGroup): in_chat = State()
class Auth(StatesGroup): captcha = State()

router = Router()

# --- КЛАВИАТУРЫ ---
def kb_main():
    b = InlineKeyboardBuilder()
    b.button(text="🛍️ Каталог", callback_data="catalog")
    b.button(text="👤 Профиль", callback_data="profile")
    b.button(text="💬 Поддержка", callback_data="support")
    return b.adjust(1).as_markup()

# --- ХЕНДЛЕРЫ ---
@router.message(CommandStart())
async def cmd_start(m: types.Message, state: FSMContext):
    u = db_query("SELECT * FROM users WHERE user_id=%s", (m.from_user.id,), fetch=True)
    if u:
        await m.answer(f"С возвращением, {m.from_user.first_name}!", reply_markup=kb_main())
    else:
        n1, n2 = random.randint(1,9), random.randint(1,9)
        ref = int(m.text.split()[1]) if len(m.text.split()) > 1 and m.text.split()[1].isdigit() else None
        await state.update_data(ans=n1+n2, ref=ref)
        await state.set_state(Auth.captcha)
        await m.answer(f"Подтвердите, что вы не робот: {n1} + {n2} = ?")

@router.message(Auth.captcha)
async def check_captcha(m: types.Message, state: FSMContext):
    data = await state.get_data()
    if m.text == str(data['ans']):
        db_query("INSERT INTO users (user_id, username, referrer_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", 
                 (m.from_user.id, m.from_user.username, data['ref']))
        await state.clear()
        await m.answer("✅ Доступ разрешен!", reply_markup=kb_main())
    else:
        await m.answer("❌ Неверно. Попробуй /start еще раз.")

@router.callback_query(F.data == "profile")
async def profile(call: types.CallbackQuery):
    u = db_query("SELECT * FROM users WHERE user_id=%s", (call.from_user.id,), fetch=True)
    bot_name = (await call.bot.get_me()).username
    txt = f"👤 ID: `{u['user_id']}`\n👥 Рефералов: {u['referral_count']}\n🔗 Ссылка: `t.me/{bot_name}?start={u['user_id']}`"
    await call.message.edit_text(txt, reply_markup=kb_main())

# --- КАТАЛОГ И ПОКУПКА ---
@router.callback_query(F.data == "catalog")
async def show_cats(call: types.CallbackQuery, state: FSMContext):
    cats = db_query("SELECT DISTINCT category_name FROM products", fetch_all=True)
    b = InlineKeyboardBuilder()
    for c in cats: b.button(text=c['category_name'], callback_data=f"cat:{c['category_name']}")
    await state.set_state(Order.cat)
    await call.message.edit_text("📂 Выберите категорию:", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("cat:"), Order.cat)
async def show_prods(call: types.CallbackQuery, state: FSMContext):
    cat = call.data.split(":")[1]
    items = db_query("SELECT DISTINCT product_name FROM products WHERE category_name=%s", (cat,), fetch_all=True)
    b = InlineKeyboardBuilder()
    for i in items: b.button(text=i['product_name'], callback_data=f"prod:{i['product_name']}")
    await state.set_state(Order.prod)
    await call.message.edit_text(f"📦 {cat}:", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("prod:"), Order.prod)
async def show_weights(call: types.CallbackQuery, state: FSMContext):
    pname = call.data.split(":")[1]
    variants = db_query("SELECT id, weight, price FROM products WHERE product_name=%s", (pname,), fetch_all=True)
    b = InlineKeyboardBuilder()
    for v in variants: b.button(text=f"{v['weight']} - {v['price']}грн", callback_data=f"w:{v['id']}")
    await state.set_state(Order.weight)
    await call.message.edit_text(f"💎 {pname}. Выберите вес:", reply_markup=b.adjust(1).as_markup())

@router.callback_query(F.data.startswith("w:"), Order.weight)
async def get_pay(call: types.CallbackQuery, state: FSMContext):
    pid = int(call.data.split(":")[1])
    item = db_query("SELECT * FROM products WHERE id=%s", (pid,), fetch=True)
    card = random.choice(PAYMENT_CARDS)
    await state.update_data(item=item, price=item['price'])
    await state.set_state(Order.check)
    await call.message.edit_text(f"💳 Оплата {item['price']}грн на карту:\n`{card}`\n\nПришлите скриншот чека:")

@router.message(Order.check, F.photo)
async def process_check(m: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    oid = str(uuid.uuid4())[:8]
    db_query("INSERT INTO orders (order_id, short_id, user_id, username, product, weight, final_price, check_file_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
             (str(uuid.uuid4()), oid, m.from_user.id, m.from_user.username, data['item']['product_name'], data['item']['weight'], data['price'], m.photo[-1].file_id))
    
    kb = InlineKeyboardBuilder().button(text="Подтвердить", callback_data=f"ok:{oid}").button(text="Отклонить", callback_data=f"no:{oid}")
    for adm in ADMIN_IDS:
        try: await bot.send_photo(adm, m.photo[-1].file_id, caption=f"🆕 Заказ #{oid}\n💰 {data['price']}грн\n👤 @{m.from_user.username}", reply_markup=kb.as_markup())
        except: pass
    await m.answer(f"⏳ Чек получен! Заказ #{oid} проверяется.")
    await state.clear()

# --- АДМИНКА ---
@router.callback_query(F.data.startswith("ok:"), F.from_user.id.in_(ADMIN_IDS))
async def approve(call: types.CallbackQuery, bot: Bot):
    oid = call.data.split(":")[1]
    o = db_query("SELECT * FROM orders WHERE short_id=%s", (oid,), fetch=True)
    if o:
        db_query("UPDATE orders SET status='ok' WHERE short_id=%s", (oid,))
        # Рефералка
        u = db_query("SELECT referrer_id, has_purchased FROM users WHERE user_id=%s", (o['user_id'],), fetch=True)
        if u and u['referrer_id'] and not u['has_purchased']:
            db_query("UPDATE users SET has_purchased=TRUE WHERE user_id=%s", (o['user_id'],))
            db_query("UPDATE users SET referral_count = referral_count + 1 WHERE user_id=%s", (u['referrer_id'],))
        
        await bot.send_message(o['user_id'], f"✅ Ваш заказ #{oid} подтвержден!")
        await call.message.edit_caption(caption=f"✅ Заказ {oid} Одобрен")

# --- ПОДДЕРЖКА ---
@router.callback_query(F.data == "support")
async def supp(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(UserSup.wait_q)
    await call.message.edit_text("Опишите ваш вопрос:")

@router.message(UserSup.wait_q)
async def supp_send(m: types.Message, state: FSMContext, bot: Bot):
    kb = InlineKeyboardBuilder().button(text="Ответить", callback_data=f"ans:{m.from_user.id}")
    for adm in ADMIN_IDS:
        await m.copy_to(adm, reply_markup=kb.as_markup())
    await m.answer("Отправлено поддержке.")
    await state.clear()

@router.callback_query(F.data.startswith("ans:"), F.from_user.id.in_(ADMIN_IDS))
async def adm_reply(call: types.CallbackQuery, state: FSMContext):
    uid = int(call.data.split(":")[1])
    await state.update_data(target=uid)
    await state.set_state(AdminSup.in_chat)
    await call.message.answer("Пишите ответ пользователю:")

@router.message(AdminSup.in_chat)
async def adm_send_reply(m: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    try:
        await bot.send_message(data['target'], f"👨‍💻 Ответ от поддержки:\n\n{m.text}")
        await m.answer("Отправлено!")
    except: await m.answer("Ошибка отправки.")
    await state.clear()

async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    init_db()
    
    # Middleware для Чек-листа (Blacklist)
    @dp.update.outer_middleware
    async def check_ban(handler, event, data):
        uid = event.from_user.id if event.from_user else None
        if uid and db_query("SELECT 1 FROM blacklist WHERE user_id=%s", (uid,), fetch=True):
            return 
        return await handler(event, data)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
