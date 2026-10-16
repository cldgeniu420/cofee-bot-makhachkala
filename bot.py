# CoffeeBro — клиентский бот + админ-бот в одном процессе
# python-telegram-bot >= 21.7, Python 3.11+
import os, asyncio, sqlite3, logging
from datetime import datetime
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, KeyboardButton)
from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters, ContextTypes)
from telegram.constants import ParseMode
from aiohttp import web

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("coffeebro")

CLIENT_TOKEN = os.environ.get("CLIENT_BOT_TOKEN")
ADMIN_TOKEN  = os.environ.get("ADMIN_BOT_TOKEN")
PORT         = int(os.environ.get("PORT", 10000))
DB_PATH      = os.environ.get("DB_PATH", "coffeebro.db")

if not CLIENT_TOKEN or not ADMIN_TOKEN:
    raise SystemExit("CLIENT_BOT_TOKEN и ADMIN_BOT_TOKEN обязательны")

admin_bot = None

# ---------- DB ----------
def _db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = _db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        banned INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
        name TEXT, phone TEXT, address TEXT, comment TEXT, items TEXT,
        total INTEGER, status TEXT DEFAULT 'new', created_at TEXT);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    """)
    c.commit(); c.close()

def get_setting(k, default=None):
    c=_db(); r=c.execute("SELECT v FROM settings WHERE k=?",(k,)).fetchone(); c.close()
    return r["v"] if r else default

def set_setting(k,v):
    c=_db(); c.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)",(k,str(v))); c.commit(); c.close()

def upsert_user(uid,un,fn):
    c=_db(); c.execute("""INSERT INTO users(user_id,username,first_name) VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name""",
        (uid,un,fn)); c.commit(); c.close()

def is_banned(uid):
    c=_db(); r=c.execute("SELECT banned FROM users WHERE user_id=?",(uid,)).fetchone(); c.close()
    return bool(r and r["banned"])

def set_ban(uid,b):
    c=_db(); c.execute("UPDATE users SET banned=? WHERE user_id=?",(1 if b else 0, uid)); c.commit(); c.close()

def all_users():
    c=_db(); r=c.execute("SELECT * FROM users ORDER BY banned DESC, user_id").fetchall(); c.close(); return r

def add_order(uid,un,name,phone,addr,cmt,items,total):
    c=_db(); cur=c.execute("""INSERT INTO orders(user_id,username,name,phone,address,comment,items,total,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",(uid,un,name,phone,addr,cmt,items,total,datetime.now().isoformat()))
    oid=cur.lastrowid; c.commit(); c.close(); return oid

def recent_orders(n=10):
    c=_db(); r=c.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?",(n,)).fetchall(); c.close(); return r

def set_order_status(oid,s):
    c=_db(); c.execute("UPDATE orders SET status=? WHERE id=?",(s,oid)); c.commit(); c.close()

def stats():
    c=_db()
    o=c.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"]
    u=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    b=c.execute("SELECT COUNT(*) n FROM users WHERE banned=1").fetchone()["n"]
    r=c.execute("SELECT COALESCE(SUM(total),0) s FROM orders WHERE status='accepted'").fetchone()["s"]
    c.close(); return {"orders":o,"users":u,"banned":b,"revenue":r}

# ---------- МЕНЮ ----------
MENU = {
    "coffee": [("Эспрессо",130),("Американо",160),("Капучино",190),
               ("Латте",200),("Флэт Уайт",210),("Раф",230),("Мокачино",240)],
    "cold":   [("Колд Брю",210),("Айс Латте",220),("Айс Американо",180),
               ("Матча Латте",260),("Фраппе",250),("Лимонад",160)],
    "beans":  [("Арабика Эфиопия",{"250 г":450,"1 кг":1600}),
               ("Арабика Колумбия",{"250 г":420,"1 кг":1500}),
               ("Робуста",{"250 г":350,"1 кг":1200}),
               ("Бленд Арабика+Робуста",{"250 г":380,"1 кг":1300}),
               ("Декаф",{"250 г":500,"1 кг":1800})],
}
CAT_T = {"coffee":"☕ Кофе","cold":"🧊 Холодные","beans":"🫘 Зерна"}

CARTS = {}   # user_id -> [ {name, qty, price, total} ]
STATES = {}  # user_id -> {state, data}

CONTACTS = ("📍 *CoffeeBro*\n"
            "г. Махачкала, ул. Шамиля 26А\n"
            "📞 +7 988 788-29-01\n"
            "🕗 Ежедневно 08:00 – 22:00")

def main_kb():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("☕ Кофе"), KeyboardButton("🧊 Холодные")],
         [KeyboardButton("🫘 Зерна"), KeyboardButton("🛒 Корзина")],
         [KeyboardButton("📞 Контакты")]],
        resize_keyboard=True, is_persistent=True)

def cart_total(uid): return sum(i["total"] for i in CARTS.get(uid,[]))

def cart_text(uid):
    items=CARTS.get(uid,[])
    if not items: return "🛒 Корзина пуста."
    lines=["🛒 *Корзина:*",""]
    for it in items:
        lines.append(f"• {it['name']} × {it['qty']} = {it['total']} ₽")
    lines.append(f"\n*Итого: {cart_total(uid)} ₽*")
    return "\n".join(lines)

def cart_kb(uid):
    items=CARTS.get(uid,[])
    rows=[]
    if items:
        for i,it in enumerate(items):
            rows.append([InlineKeyboardButton(f"❌ {it['name']} × {it['qty']}", callback_data=f"rm:{i}")])
        rows.append([InlineKeyboardButton("✅ Оформить заказ", callback_data="co:start")])
        rows.append([InlineKeyboardButton("🗑 Очистить", callback_data="cart:clear")])
    return InlineKeyboardMarkup(rows) if rows else None

# ---------- КЛИЕНТСКИЙ БОТ ----------
async def c_start(update, context):
    u=update.effective_user
    upsert_user(u.id,u.username or "",u.first_name or "")
    if is_banned(u.id):
        await update.message.reply_text("⛔ Доступ к боту заблокирован."); return
    await update.message.reply_text(
        "Добро пожаловать в *CoffeeBro* ☕\n\n"
        "Свежий кофе, холодные напитки и зёрна на продажу.\n"
        "Заказ без предоплаты — оплата при получении.",
        reply_markup=main_kb(), parse_mode=ParseMode.MARKDOWN)

def _cat_rows(cat):
    rows=[]
    for i,it in enumerate(MENU[cat]):
        if cat=="beans":
            mn=min(it[1].values())
            rows.append([InlineKeyboardButton(f"{it[0]} — от {mn} ₽", callback_data=f"itm:{cat}:{i}")])
        else:
            rows.append([InlineKeyboardButton(f"{it[0]} — {it[1]} ₽", callback_data=f"itm:{cat}:{i}")])
    rows.append([InlineKeyboardButton("🛒 Корзина", callback_data="cart:show")])
    return rows

async def send_category(update, cat):
    await update.message.reply_text(f"*{CAT_T[cat]}*\nВыберите позицию:",
        reply_markup=InlineKeyboardMarkup(_cat_rows(cat)), parse_mode=ParseMode.MARKDOWN)

async def cb_cat(update, context):
    q=update.callback_query; await q.answer()
    cat=q.data.split(":")[1]
    await q.edit_message_text(f"*{CAT_T[cat]}*\nВыберите позицию:",
        reply_markup=InlineKeyboardMarkup(_cat_rows(cat)), parse_mode=ParseMode.MARKDOWN)

async def cb_item(update, context):
    q=update.callback_query; await q.answer()
    _,cat,idx=q.data.split(":"); idx=int(idx)
    name=MENU[cat][idx][0]
    if cat=="beans":
        sizes=MENU[cat][idx][1]
        rows=[[InlineKeyboardButton(f"{sz} — {p} ₽", callback_data=f"badd:{idx}:{sz}")] for sz,p in sizes.items()]
        rows.append([InlineKeyboardButton("◀️ Назад", callback_data=f"cat:{cat}")])
        await q.edit_message_text(f"*{name}*\nВыберите фасовку:",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)
    else:
        price=MENU[cat][idx][1]
        rows=[[InlineKeyboardButton(f"{n} шт — {n*price} ₽", callback_data=f"qadd:{cat}:{idx}:{n}")] for n in (1,2,3,5,10)]
        rows.append([InlineKeyboardButton("◀️ Назад", callback_data=f"cat:{cat}")])
        await q.edit_message_text(f"*{name}*\n{price} ₽ / шт. Сколько?",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

async def cb_qadd(update, context):
    q=update.callback_query; await q.answer()
    _,cat,idx,n=q.data.split(":"); idx=int(idx); n=int(n)
    name=MENU[cat][idx][0]; price=MENU[cat][idx][1]
    uid=q.from_user.id
    CARTS.setdefault(uid,[]).append({"name":name,"qty":n,"price":price,"total":n*price})
    await q.edit_message_text(
        f"✅ *{name}* × {n} добавлено\n\nИтого в корзине: *{cart_total(uid)} ₽*",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Корзина", callback_data="cart:show")],
            [InlineKeyboardButton("◀️ В меню", callback_data=f"cat:{cat}")]]),
        parse_mode=ParseMode.MARKDOWN)

async def cb_badd(update, context):
    q=update.callback_query; await q.answer()
    _,idx,size=q.data.split(":"); idx=int(idx)
    name=MENU["beans"][idx][0]; price=MENU["beans"][idx][1][size]
    uid=q.from_user.id
    CARTS.setdefault(uid,[]).append({"name":f"{name} ({size})","qty":1,"price":price,"total":price})
    await q.edit_message_text(
        f"✅ *{name}* ({size}) добавлено\n\nИтого в корзине: *{cart_total(uid)} ₽*",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Корзина", callback_data="cart:show")],
            [InlineKeyboardButton("◀️ К зёрнам", callback_data="cat:beans")]]),
        parse_mode=ParseMode.MARKDOWN)

async def cb_cart_show(update, context):
    q=update.callback_query; await q.answer()
    uid=q.from_user.id
    await q.edit_message_text(cart_text(uid), reply_markup=cart_kb(uid), parse_mode=ParseMode.MARKDOWN)

async def cb_cart_clear(update, context):
    q=update.callback_query; await q.answer()
    CARTS.pop(q.from_user.id, None)
    await q.edit_message_text("🗑 Корзина очищена.")

async def cb_cart_rm(update, context):
    q=update.callback_query; await q.answer()
    uid=q.from_user.id; i=int(q.data.split(":")[1])
    items=CARTS.get(uid,[])
    if 0<=i<len(items): items.pop(i)
    await q.edit_message_text(cart_text(uid), reply_markup=cart_kb(uid), parse_mode=ParseMode.MARKDOWN)

async def cb_checkout(update, context):
    q=update.callback_query; await q.answer()
    uid=q.from_user.id
    if not CARTS.get(uid):
        await q.answer("Корзина пуста", show_alert=True); return
    if is_banned(uid):
        await q.answer("Доступ заблокирован", show_alert=True); return
    STATES[uid]={"state":"name","data":{}}
    await q.edit_message_text("📝 *Шаг 1/4.* Введите ваше имя:", parse_mode=ParseMode.MARKDOWN)

async def submit_order(update, uid, d):
    items=CARTS.pop(uid,[])
    if not items:
        await update.message.reply_text("Корзина пуста."); return
    total=sum(i["total"] for i in items)
    items_str="\n".join(f"• {i['name']} × {i['qty']} = {i['total']} ₽" for i in items)
    u=update.effective_user
    oid=add_order(uid,u.username or "",d["name"],d["phone"],d["address"],d["comment"],items_str,total)

    admin_chat=get_setting("admin_chat_id")
    if admin_chat and admin_bot:
        text=(f"🆕 *Заказ #{oid}*\n\n"
              f"👤 Имя: {d['name']}\n📞 Телефон: {d['phone']}\n"
              f"📍 Адрес: {d['address']}\n💬 Комментарий: {d['comment']}\n\n"
              f"🛒 *Состав:*\n{items_str}\n\n💰 *Итого: {total} ₽*\n\n"
              f"👤 @{u.username or '—'} | ID `{uid}`")
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Принять", callback_data=f"ord:ok:{oid}"),
             InlineKeyboardButton("❌ Отменить", callback_data=f"ord:no:{oid}")],
            [InlineKeyboardButton("🚫 Забанить клиента", callback_data=f"ban:{uid}")]])
        try:
            await admin_bot.send_message(int(admin_chat), text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            log.error("send to admin failed: %s", e)
    else:
        log.warning("admin_chat_id не установлен, заказ #%s не отправлен", oid)

    await update.message.reply_text(
        f"✅ *Заказ #{oid} принят!*\n\n"
        f"Мы позвоним по номеру *{d['phone']}* в течение 10 минут.\n"
        f"Оплата при получении.\n\nСпасибо, что выбрали *CoffeeBro* ☕",
        reply_markup=main_kb(), parse_mode=ParseMode.MARKDOWN)

REPLY_BUTTONS = ("☕ Кофе","🧊 Холодные","🫘 Зерна","🛒 Корзина","📞 Контакты")

async def handle_state(update, st):
    uid=update.effective_user.id
    txt=(update.message.text or "").strip()
    s=st["state"]; d=st["data"]
    if s=="name":
        d["name"]=txt; st["state"]="phone"
        await update.message.reply_text("📞 *Шаг 2/4.* Введите номер телефона:", parse_mode=ParseMode.MARKDOWN)
    elif s=="phone":
        d["phone"]=txt; st["state"]="address"
        await update.message.reply_text("📍 *Шаг 3/4.* Введите адрес доставки (или «самовывоз»):", parse_mode=ParseMode.MARKDOWN)
    elif s=="address":
        d["address"]=txt; st["state"]="comment"
        await update.message.reply_text("💬 *Шаг 4/4.* Комментарий к заказу (или «-»):", parse_mode=ParseMode.MARKDOWN)
    elif s=="comment":
        d["comment"]=txt
        STATES.pop(uid,None)
        await submit_order(update, uid, d)

async def c_cancel(update, context):
    uid=update.effective_user.id
    if uid in STATES:
        STATES.pop(uid,None)
        await update.message.reply_text("❌ Оформление отменено.", reply_markup=main_kb())
    else:
        await update.message.reply_text("Нечего отменять.")

async def c_text(update, context):
    u=update.effective_user
    upsert_user(u.id,u.username or "",u.first_name or "")
    if is_banned(u.id):
        await update.message.reply_text("⛔ Доступ заблокирован."); return
    txt=(update.message.text or "").strip()
    st=STATES.get(u.id)
    if st:
        if txt.lower() in ("отмена","cancel"):
            STATES.pop(u.id,None)
            await update.message.reply_text("❌ Оформление отменено.", reply_markup=main_kb()); return
        if txt in REPLY_BUTTONS:
            await update.message.reply_text("Сначала завершите оформление или напишите «отмена»."); return
        await handle_state(update, st); return
    if txt=="☕ Кофе":     await send_category(update,"coffee"); return
    if txt=="🧊 Холодные": await send_category(update,"cold"); return
    if txt=="🫘 Зерна":    await send_category(update,"beans"); return
    if txt=="🛒 Корзина":
        await update.message.reply_text(cart_text(u.id), reply_markup=cart_kb(u.id), parse_mode=ParseMode.MARKDOWN); return
    if txt=="📞 Контакты":
        await update.message.reply_text(CONTACTS, parse_mode=ParseMode.MARKDOWN); return
    await update.message.reply_text("Выберите раздел из меню ниже 👇", reply_markup=main_kb())

# ---------- АДМИН-БОТ ----------
def admin_panel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Последние заказы", callback_data="adm:orders")],
        [InlineKeyboardButton("👥 Пользователи", callback_data="adm:users")],
        [InlineKeyboardButton("🚫 Бан-лист", callback_data="adm:banned")],
        [InlineKeyboardButton("🔙 Закрыть", callback_data="adm:close")]])

def admin_panel_text():
    s=stats()
    return (f"🔐 *Админ-панель CoffeeBro*\n\n"
            f"📦 Заказов: {s['orders']}\n"
            f"👥 Пользователей: {s['users']}\n"
            f"🚫 Забанено: {s['banned']}\n"
            f"💰 Выручка (принятые): {s['revenue']} ₽")

async def a_text(update, context):
    u=update.effective_user
    saved=get_setting("admin_chat_id")
    if not saved:
        set_setting("admin_chat_id", u.id)
        saved=str(u.id)
        log.info("admin_chat_id = %s", u.id)
    if str(u.id)!=str(saved):
        return  # чужой — игнор
    txt=(update.message.text or "").strip()
    if txt=="snegr":
        try: await update.message.delete()
        except Exception: pass
        await update.message.reply_text(admin_panel_text(), reply_markup=admin_panel_kb(), parse_mode=ParseMode.MARKDOWN)
        return
    try: await update.message.delete()
    except Exception: pass

async def a_panel(update, context):
    q=update.callback_query; await q.answer()
    await q.edit_message_text(admin_panel_text(), reply_markup=admin_panel_kb(), parse_mode=ParseMode.MARKDOWN)

async def a_orders(update, context):
    q=update.callback_query; await q.answer()
    orders=recent_orders(10)
    back=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="adm:panel")]])
    if not orders:
        await q.edit_message_text("Заказов пока нет.", reply_markup=back); return
    lines=["📦 *Последние 10 заказов:*",""]
    for o in orders:
        mark={"new":"🆕","accepted":"✅","cancelled":"❌"}.get(o["status"],"•")
        lines.append(f"{mark} *#{o['id']}* — {o['total']} ₽ — {o['name']}")
    await q.edit_message_text("\n".join(lines), reply_markup=back, parse_mode=ParseMode.MARKDOWN)

async def a_users(update, context):
    q=update.callback_query; await q.answer()
    users=all_users()
    back=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="adm:panel")]])
    if not users:
        await q.edit_message_text("Пользователей нет.", reply_markup=back); return
    rows=[]
    for u in users[:30]:
        tag="🚫" if u["banned"] else "👤"
        name=u["first_name"] or u["username"] or str(u["user_id"])
        rows.append([InlineKeyboardButton(f"{tag} {name} — ID {u['user_id']}", callback_data=f"usr:{u['user_id']}")])
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="adm:panel")])
    await q.edit_message_text("👥 *Пользователи:*", reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

async def a_banned(update, context):
    q=update.callback_query; await q.answer()
    users=[u for u in all_users() if u["banned"]]
    back=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="adm:panel")]])
    if not users:
        await q.edit_message_text("Бан-лист пуст.", reply_markup=back); return
    rows=[]
    for u in users:
        name=u["first_name"] or u["username"] or str(u["user_id"])
        rows.append([InlineKeyboardButton(f"✅ Разбанить {name} ({u['user_id']})", callback_data=f"unban:{u['user_id']}")])
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="adm:panel")])
    await q.edit_message_text("🚫 *Бан-лист:*", reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

async def a_user_detail(update, context):
    q=update.callback_query; await q.answer()
    uid=int(q.data.split(":")[1])
    c=_db(); u=c.execute("SELECT * FROM users WHERE user_id=?",(uid,)).fetchone(); c.close()
    if not u:
        await q.edit_message_text("Не найден."); return
    name=u["first_name"] or "—"
    uname=u["username"] or "—"
    banned="🚫 да" if u["banned"] else "нет"
    text=f"👤 *{name}*\n@{uname}\nID: `{uid}`\nЗабанен: {banned}"
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Разбанить" if u["banned"] else "🚫 Забанить",
            callback_data=(f"unban:{uid}" if u["banned"] else f"ban:{uid}"))],
        [InlineKeyboardButton("🔙 Назад", callback_data="adm:users")]])
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def a_close(update, context):
    q=update.callback_query; await q.answer()
    try: await q.message.delete()
    except Exception: pass

async def a_ord_action(update, context):
    q=update.callback_query; await q.answer()
    _,action,oid=q.data.split(":"); oid=int(oid)
    if action=="ok":
        set_order_status(oid,"accepted")
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text(f"✅ Заказ #{oid} принят.")
    elif action=="no":
        set_order_status(oid,"cancelled")
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text(f"❌ Заказ #{oid} отменён.")

async def a_ban(update, context):
    q=update.callback_query; await q.answer("Забанен")
    uid=int(q.data.split(":")[1])
    set_ban(uid, True)
    await q.edit_message_reply_markup(reply_markup=None)
    await q.message.reply_text(f"🚫 Пользователь `{uid}` забанен.", parse_mode=ParseMode.MARKDOWN)

async def a_unban(update, context):
    q=update.callback_query; await q.answer("Разбанен")
    uid=int(q.data.split(":")[1])
    set_ban(uid, False)
    try: await q.edit_message_reply_markup(reply_markup=None)
    except Exception: pass
    await q.message.reply_text(f"✅ Пользователь `{uid}` разбанен.", parse_mode=ParseMode.MARKDOWN)

# ---------- ЗАПУСК ----------
async def health(request):
    return web.Response(text="CoffeeBro OK")

async def run():
    global admin_bot

    client=ApplicationBuilder().token(CLIENT_TOKEN).build()
    client.add_handler(CommandHandler("start", c_start))
    client.add_handler(CommandHandler("cancel", c_cancel))
    client.add_handler(CallbackQueryHandler(cb_cat,        pattern=r"^cat:"))
    client.add_handler(CallbackQueryHandler(cb_item,       pattern=r"^itm:"))
    client.add_handler(CallbackQueryHandler(cb_qadd,       pattern=r"^qadd:"))
    client.add_handler(CallbackQueryHandler(cb_badd,       pattern=r"^badd:"))
    client.add_handler(CallbackQueryHandler(cb_cart_show,  pattern=r"^cart:show$"))
    client.add_handler(CallbackQueryHandler(cb_cart_clear, pattern=r"^cart:clear$"))
    client.add_handler(CallbackQueryHandler(cb_cart_rm,    pattern=r"^rm:"))
    client.add_handler(CallbackQueryHandler(cb_checkout,   pattern=r"^co:start$"))
    client.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, c_text))

    admin=ApplicationBuilder().token(ADMIN_TOKEN).build()
    admin.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, a_text))
    admin.add_handler(CallbackQueryHandler(a_panel,      pattern=r"^adm:panel$"))
    admin.add_handler(CallbackQueryHandler(a_orders,     pattern=r"^adm:orders$"))
    admin.add_handler(CallbackQueryHandler(a_users,      pattern=r"^adm:users$"))
    admin.add_handler(CallbackQueryHandler(a_banned,     pattern=r"^adm:banned$"))
    admin.add_handler(CallbackQueryHandler(a_close,      pattern=r"^adm:close$"))
    admin.add_handler(CallbackQueryHandler(a_user_detail,pattern=r"^usr:"))
    admin.add_handler(CallbackQueryHandler(a_ord_action, pattern=r"^ord:"))
    admin.add_handler(CallbackQueryHandler(a_ban,        pattern=r"^ban:"))
    admin.add_handler(CallbackQueryHandler(a_unban,      pattern=r"^unban:"))

    await client.initialize(); await client.start(); await client.updater.start_polling()
    await admin.initialize();  await admin.start();  await admin.updater.start_polling()
    admin_bot = admin.bot

    web_app=web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)
    runner=web.AppRunner(web_app); await runner.setup()
    site=web.TCPSite(runner,"0.0.0.0",PORT); await site.start()
    log.info("CoffeeBro запущен на порту %s", PORT)

    await asyncio.Event().wait()

def main():
    init_db()
    asyncio.run(run())

if __name__ == "__main__":
    main()
