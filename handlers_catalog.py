"""
Catalogue management for admins: add / edit / delete countries, and the packages
inside each country. Everything is buttons — no command syntax to memorise.
"""

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import db
import permissions as perms
import ui

log = logging.getLogger(__name__)

P = perms.P_CATALOG


# ---------------------------------------------------------------- panel

@ui.require(P)
async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    countries = db.list_countries(active_only=False)
    counts = db.stock_counts()
    text = (
        "🗂️ <b>الدول والباقات</b>\n"
        f"🌍 الدول: {len(countries)} · 📦 الشرايح: {counts['available']} متاحة / {counts['sold']} مباعة\n\n"
        "اختار دولة عشان تشوف باقاتها، أو ضيف دولة جديدة."
    )
    rows = []
    for c in countries:
        st = db.country_stats(c["id"])
        mark = "" if c["active"] else "⏸ "
        rows.append([ui.btn(
            f"{mark}{c['flag']} {c['name']} — {st['plans']} باقة · {st['available']} متاحة",
            f"cat:c:{c['id']}",
        )])
    rows.append([ui.btn("➕ إضافة دولة", "cat:addcountry")])
    rows.append([ui.hub_btn()])
    await ui.reply(update, text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(P)
async def country_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, cid: int):
    c = db.get_country(cid)
    if not c:
        await ui.reply(update, "الدولة دي مش موجودة.")
        return
    plans = db.list_products_with_stock(country_id=cid, active_only=False)
    st = db.country_stats(cid)

    lines = [
        f"{c['flag']} <b>{c['name']}</b> {'' if c['active'] else '(متوقفة)'}",
        f"📦 {st['plans']} باقة · {st['available']} شريحة متاحة · {st['sold']} مباعة\n",
    ]
    rows = []
    for p in plans:
        mark = "" if p["active"] else "⏸ "
        lines.append(
            f"#{p['id']} {mark}{p['data_amount']} / {p['validity_days']}ي — "
            f"{ui.money(p['price_usdt'])} · متاح {p['available']} · مباع {p['sold']}"
        )
        rows.append([ui.btn(
            f"{mark}{p['data_amount']} · {p['validity_days']}ي · {ui.money(p['price_usdt'])} ({p['available']})",
            f"cat:p:{p['id']}",
        )])
    if not plans:
        lines.append("مفيش باقات في الدولة دي لسه.")

    rows.append([ui.btn("➕ إضافة باقة للدولة دي", f"cat:addplan:{cid}")])
    rows.append([
        ui.btn("✏️ تعديل الدولة", f"cat:cedit:{cid}"),
        ui.btn("⏸ إيقاف" if c["active"] else "▶️ تفعيل", f"cat:ctoggle:{cid}"),
    ])
    rows.append([ui.btn("🗑️ حذف الدولة", f"cat:cdel:{cid}")])
    rows.append([ui.btn("⬅️ رجوع", "cat:home"), ui.hub_btn()])
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(P)
async def plan_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int):
    p = db.get_product(pid)
    if not p:
        await ui.reply(update, "الباقة دي مش موجودة.")
        return
    avail = db.count_available_stock(pid)
    tiers = db.list_tiers(pid)
    text = (
        f"📦 <b>باقة #{p['id']}</b>\n"
        f"{p['flag']} {p['country']}\n"
        f"📶 الداتا: {p['data_amount']}\n"
        f"📅 المدة: {p['validity_days']} يوم\n"
        f"💵 السعر: {ui.money(p['price_usdt'])}\n"
        f"🛡️ الضمان الافتراضي: {p.get('warranty_days') or 0} يوم\n"
        f"⚡ الليمت اليومي: {p.get('daily_limit') or '—'}\n"
        f"📝 الوصف: {p.get('description') or '—'}\n"
        f"📊 المتاح: {avail} شريحة\n"
        f"🏷️ أسعار جملة: {len(tiers) if tiers else 'مفيش'}\n"
        f"🔘 الحالة: {'✅ شغالة' if p['active'] else '⏸ متوقفة'}"
    )
    rows = [
        [ui.btn("💵 السعر", f"cat:edit:{pid}:price_usdt"),
         ui.btn("📶 الداتا", f"cat:edit:{pid}:data_amount")],
        [ui.btn("📅 المدة", f"cat:edit:{pid}:validity_days"),
         ui.btn("🛡️ الضمان", f"cat:edit:{pid}:warranty_days")],
        [ui.btn("⚡ الليمت اليومي", f"cat:edit:{pid}:daily_limit"),
         ui.btn("📝 الوصف", f"cat:edit:{pid}:description")],
        [ui.btn("🏷️ أسعار الجملة", f"cat:tiers:{pid}")],
        [ui.btn("⏸ إيقاف" if p["active"] else "▶️ تفعيل", f"cat:ptoggle:{pid}"),
         ui.btn("🗑️ حذف", f"cat:pdel:{pid}")],
        [ui.btn("⬅️ رجوع", f"cat:c:{p['country_id']}"), ui.hub_btn()],
    ]
    await ui.reply(update, text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------- bulk pricing

@ui.require(P)
async def tier_pick_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'🏷️ أسعار الجملة' in the admin panel — pick which package to price."""
    products = db.list_products_with_stock(active_only=False)
    if not products:
        await ui.reply(update, "مفيش باقات لسه. ضيف دولة وباقة الأول.",
                       reply_markup=ui.kb([[ui.btn("🗂️ الدول والباقات", "cat:home")],
                                           [ui.hub_btn()]]))
        return
    lines = ["🏷️ <b>أسعار الجملة</b>\n",
             "اختار الباقة اللي عايز تحط ليها سعر جملة:\n"]
    rows = []
    for p in products:
        tiers = db.list_tiers(p["id"])
        mark = f"🏷️×{len(tiers)}" if tiers else "—"
        lines.append(f"• {db.product_label(p)} — {ui.money(p['price_usdt'])} · {mark}")
        rows.append([ui.btn(
            f"{p['flag']} {p['country']} {p['data_amount']} · {ui.money(p['price_usdt'])} {mark}",
            f"cat:tiers:{p['id']}",
        )])
    rows.append([ui.hub_btn()])
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(P)
async def tiers_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int):
    p = db.get_product(pid)
    if not p:
        await ui.reply(update, "الباقة دي مش موجودة.")
        return
    tiers = db.list_tiers(pid)
    base = p["price_usdt"]

    lines = [
        f"🏷️ <b>أسعار الجملة</b>\n{db.product_label(p)}",
        f"💵 سعر الشريحة الواحدة: <b>{ui.money(base)}</b>\n",
    ]
    rows = []
    if tiers:
        lines.append("<b>الشرائح السعرية الحالية</b>")
        for t in tiers:
            save = base - t["unit_price"]
            lines.append(
                f"• من <b>{t['min_qty']}</b> شرايح فأكتر → {ui.money(t['unit_price'])} للواحدة"
                + (f" (وفر {ui.money(save)} في الواحدة)" if save > 0 else "")
            )
            rows.append([ui.btn(f"🗑️ حذف «من {t['min_qty']}»", f"cat:tierdel:{t['id']}")])
        # a worked example with the biggest tier, so the effect is obvious
        top = tiers[-1]
        lines.append(
            f"\n<i>مثال: {top['min_qty']} شرايح = {ui.money(top['unit_price'] * top['min_qty'])} "
            f"بدل {ui.money(base * top['min_qty'])}</i>"
        )
    else:
        lines.append("مفيش أسعار جملة لسه — كل العملاء بياخدوا السعر العادي.")

    lines.append("\n<i>العميل بياخد أرخص سعر توصله كميته تلقائيًا.</i>")
    rows.append([ui.btn("➕ إضافة سعر جملة", f"cat:tieradd:{pid}")])
    rows.append([ui.btn("⬅️ الباقة", f"cat:p:{pid}"), ui.hub_btn()])
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


ASK_TIER_QTY, ASK_TIER_PRICE = range(630, 632)


@ui.require(P)
async def tier_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split(":")[2])
    p = db.get_product(pid)
    if not p:
        return ConversationHandler.END
    context.user_data["tier_pid"] = pid
    await query.edit_message_text(
        f"🏷️ سعر جملة لـ {db.product_label(p)}\n"
        f"السعر العادي: {ui.money(p['price_usdt'])}\n\n"
        "1️⃣ من كام شريحة يبدأ السعر ده؟ (مثال: 10)\n\n/cancel للإلغاء"
    )
    return ASK_TIER_QTY


async def tier_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("لازم رقم صحيح. جرب تاني:")
        return ASK_TIER_QTY
    if qty < 2:
        await update.message.reply_text("لازم 2 أو أكتر — السعر العادي هو سعر الشريحة الواحدة.")
        return ASK_TIER_QTY

    context.user_data["tier_qty"] = qty
    p = db.get_product(context.user_data["tier_pid"])
    await update.message.reply_text(
        f"2️⃣ سعر <b>الشريحة الواحدة</b> لما يشتري {qty} أو أكتر؟\n"
        f"(السعر العادي {ui.money(p['price_usdt'])} — اكتب رقم أقل منه)",
        parse_mode=ParseMode.HTML,
    )
    return ASK_TIER_PRICE


async def tier_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("لازم رقم. جرب تاني:")
        return ASK_TIER_PRICE
    if price <= 0:
        await update.message.reply_text("السعر لازم يكون أكبر من صفر.")
        return ASK_TIER_PRICE

    pid = context.user_data.pop("tier_pid")
    qty = context.user_data.pop("tier_qty")
    p = db.get_product(pid)
    base = p["price_usdt"]

    db.add_tier(pid, qty, price)
    db.log_action(update.effective_user.id, "add_tier", f"product {pid} qty {qty} @ {price}")

    msg = (
        f"✅ اتسجل: من {qty} شرايح فأكتر، الواحدة بـ {ui.money(price)}\n"
        f"يعني {qty} شرايح = {ui.money(price * qty)} بدل {ui.money(base * qty)}"
    )
    if price >= base:
        msg += "\n\n⚠️ السعر ده مش أقل من السعر العادي — اتأكد إنه اللي انت قاصده."

    # cost check: warn when a tier sells below what the stock cost
    avg = db.list_stock(product_id=pid, status="available", limit=200)
    costs = [s["cost_price"] for s in avg if s.get("cost_price")]
    if costs:
        avg_cost = sum(costs) / len(costs)
        if price < avg_cost:
            msg += (f"\n\n🚨 تنبيه: متوسط تكلفة الشريحة عندك {ui.money(avg_cost)}، "
                    f"يعني السعر ده هيخسرك {ui.money(avg_cost - price)} في الواحدة.")

    await update.message.reply_text(
        msg, reply_markup=ui.kb([[ui.btn("🏷️ أسعار الجملة", f"cat:tiers:{pid}")]])
    )
    return ConversationHandler.END


# ---------------------------------------------------------------- add country

ASK_C_NAME, ASK_C_FLAG = range(600, 602)


@ui.require(P)
async def addcountry_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "🌍 اكتب اسم الدولة (مثال: تركيا)\n\nللإلغاء اكتب /cancel"
    )
    return ASK_C_NAME


async def addcountry_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_name"] = update.message.text.strip()
    await update.message.reply_text("🚩 ابعت علم الدولة (إيموجي)، أو اكتب «تخطي».")
    return ASK_C_FLAG


async def addcountry_flag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flag = update.message.text.strip()
    if flag in ("تخطي", "-", "/skip"):
        flag = "🌍"
    name = context.user_data.pop("c_name", "")
    cid = db.add_country(name, flag)
    if not cid:
        await update.message.reply_text(f"⚠️ الدولة «{name}» موجودة بالفعل.")
        return ConversationHandler.END
    db.log_action(update.effective_user.id, "add_country", f"{name} ({cid})")
    await update.message.reply_text(
        f"✅ اتضافت الدولة {flag} {name}.\n\nدلوقتي ضيفلها باقات:",
        reply_markup=ui.kb([[ui.btn("➕ إضافة باقة", f"cat:addplan:{cid}")],
                            [ui.btn("🗂️ الدول والباقات", "cat:home")]]),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------- edit country

ASK_CE_NAME, ASK_CE_FLAG = range(602, 604)


@ui.require(P)
async def cedit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split(":")[2])
    c = db.get_country(cid)
    if not c:
        return ConversationHandler.END
    context.user_data["edit_cid"] = cid
    await query.edit_message_text(
        f"✏️ الاسم الحالي: {c['name']}\n\nاكتب الاسم الجديد، أو «تخطي» لو مش عايز تغيره.\n/cancel للإلغاء"
    )
    return ASK_CE_NAME


async def cedit_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["ce_name"] = None if text in ("تخطي", "-") else text
    await update.message.reply_text("🚩 ابعت العلم الجديد، أو «تخطي».")
    return ASK_CE_FLAG


async def cedit_flag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    flag = None if text in ("تخطي", "-") else text
    cid = context.user_data.pop("edit_cid", None)
    name = context.user_data.pop("ce_name", None)
    db.update_country(cid, name=name, flag=flag)
    db.log_action(update.effective_user.id, "edit_country", f"{cid}")
    c = db.get_country(cid)
    await update.message.reply_text(
        f"✅ اتحدثت الدولة: {c['flag']} {c['name']}",
        reply_markup=ui.kb([[ui.btn("🗂️ رجوع", f"cat:c:{cid}")]]),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------- add plan

ASK_P_DATA, ASK_P_DAYS, ASK_P_PRICE, ASK_P_WARRANTY, ASK_P_DESC, ASK_P_CONFIRM = range(610, 616)


@ui.require(P)
async def addplan_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split(":")[2])
    c = db.get_country(cid)
    if not c:
        return ConversationHandler.END
    context.user_data["plan"] = {"country_id": cid, "country": c["name"], "flag": c["flag"]}
    await query.edit_message_text(
        f"➕ باقة جديدة في {c['flag']} {c['name']}\n\n"
        "1️⃣ اكتب حجم الداتا (مثال: 10GB أو غير محدود)\n\n/cancel للإلغاء"
    )
    return ASK_P_DATA


async def addplan_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["plan"]["data_amount"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ الباقة بتشتغل كام يوم؟ (رقم فقط، مثال: 30)")
    return ASK_P_DAYS


async def addplan_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["plan"]["validity_days"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("لازم رقم صحيح. جرب تاني:")
        return ASK_P_DAYS
    await update.message.reply_text("3️⃣ سعر البيع للعميل بالدولار (مثال: 6.5)")
    return ASK_P_PRICE


async def addplan_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["plan"]["price_usdt"] = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("لازم رقم. جرب تاني:")
        return ASK_P_PRICE
    await update.message.reply_text(
        f"4️⃣ مدة الضمان بالأيام (الافتراضي {config.DEFAULT_WARRANTY_DAYS}).\n"
        "اكتب رقم، أو «تخطي» للافتراضي، أو 0 لو من غير ضمان."
    )
    return ASK_P_WARRANTY


async def addplan_warranty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text in ("تخطي", "-"):
        days = config.DEFAULT_WARRANTY_DAYS
    else:
        try:
            days = int(text)
        except ValueError:
            await update.message.reply_text("لازم رقم. جرب تاني:")
            return ASK_P_WARRANTY
    context.user_data["plan"]["warranty_days"] = days
    await update.message.reply_text("5️⃣ وصف مختصر للباقة، أو «تخطي».")
    return ASK_P_DESC


async def addplan_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    plan = context.user_data["plan"]
    plan["description"] = "" if text in ("تخطي", "-") else text
    summary = (
        "📋 <b>مراجعة الباقة</b>\n"
        f"{plan['flag']} {plan['country']}\n"
        f"📶 {plan['data_amount']}\n"
        f"📅 {plan['validity_days']} يوم\n"
        f"💵 {ui.money(plan['price_usdt'])}\n"
        f"🛡️ ضمان {plan['warranty_days']} يوم\n"
        f"📝 {plan['description'] or '—'}"
    )
    await update.message.reply_text(
        summary,
        parse_mode=ParseMode.HTML,
        reply_markup=ui.kb([[ui.btn("✅ تأكيد", "cat:planok"), ui.btn("❌ إلغاء", "cat:plancancel")]]),
    )
    return ASK_P_CONFIRM


async def addplan_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cat:plancancel":
        context.user_data.pop("plan", None)
        await query.edit_message_text("اتلغى.")
        return ConversationHandler.END

    plan = context.user_data.pop("plan", None)
    if not plan:
        await query.edit_message_text("حصل خطأ، ابدأ تاني.")
        return ConversationHandler.END

    pid = db.add_product(
        plan["country_id"], plan["data_amount"], plan["validity_days"],
        plan["price_usdt"], plan["description"], plan["warranty_days"],
    )
    db.log_action(query.from_user.id, "add_product", f"{pid} {plan['country']}")
    await query.edit_message_text(
        f"✅ اتضافت الباقة #{pid}.\nدلوقتي ضيفلها ستوك عشان تظهر للعملاء.",
        reply_markup=ui.kb([
            [ui.btn("📦 إضافة ستوك دلوقتي", f"stk:add:{pid}")],
            [ui.btn("🗂️ رجوع للدولة", f"cat:c:{plan['country_id']}")],
        ]),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------- edit plan field

ASK_FIELD_VALUE = 620

FIELD_PROMPTS = {
    "price_usdt": ("💵 السعر الجديد بالدولار", float),
    "data_amount": ("📶 حجم الداتا الجديد", str),
    "validity_days": ("📅 عدد الأيام الجديد", int),
    "warranty_days": ("🛡️ مدة الضمان الجديدة بالأيام", int),
    "description": ("📝 الوصف الجديد", str),
    "daily_limit": ("⚡ الليمت اليومي كما هيظهر للعميل (مثال: 5 جيجا/يوم)، أو - لمسحه", str),
}


@ui.require(P)
async def edit_field_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, _, pid, field = query.data.split(":")
    if field not in FIELD_PROMPTS:
        return ConversationHandler.END
    context.user_data["edit_pid"] = int(pid)
    context.user_data["edit_field"] = field
    prompt, _cast = FIELD_PROMPTS[field]
    await query.edit_message_text(f"{prompt}:\n\n/cancel للإلغاء")
    return ASK_FIELD_VALUE


async def edit_field_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = context.user_data.pop("edit_pid", None)
    field = context.user_data.pop("edit_field", None)
    if not pid or not field:
        return ConversationHandler.END
    _prompt, cast = FIELD_PROMPTS[field]
    raw = update.message.text.strip()
    if cast is str and raw in ("-", "تخطي", "مسح"):
        raw = ""
    try:
        value = cast(raw.replace(",", ".")) if cast is float else cast(raw)
    except ValueError:
        await update.message.reply_text("القيمة مش مظبوطة، ابدأ تاني من صفحة الباقة.")
        return ConversationHandler.END
    db.update_product(pid, **{field: value})
    db.log_action(update.effective_user.id, "edit_product", f"{pid} {field}={value}")
    await update.message.reply_text(
        "✅ اتحدثت الباقة.",
        reply_markup=ui.kb([[ui.btn("📦 عرض الباقة", f"cat:p:{pid}")]]),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in ("plan", "c_name", "edit_cid", "ce_name", "edit_pid", "edit_field",
                "tier_pid", "tier_qty"):
        context.user_data.pop(key, None)
    await update.message.reply_text("اتلغى.")
    return ConversationHandler.END


# ---------------------------------------------------------------- delete / toggle

@ui.require(P)
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]

    if action == "home":
        await panel(update, context)
    elif action == "c":
        await country_detail(update, context, int(parts[2]))
    elif action == "p":
        await plan_detail(update, context, int(parts[2]))
    elif action == "tierpick":
        await tier_pick_product(update, context)
    elif action == "tiers":
        await tiers_panel(update, context, int(parts[2]))
    elif action == "tierdel":
        tier = db.get_tier(int(parts[2]))
        if tier:
            db.delete_tier(tier["id"])
            db.log_action(query.from_user.id, "delete_tier", f"{tier['product_id']} qty {tier['min_qty']}")
            await tiers_panel(update, context, tier["product_id"])

    elif action == "ctoggle":
        cid = int(parts[2])
        c = db.get_country(cid)
        db.update_country(cid, active=not bool(c["active"]))
        await country_detail(update, context, cid)

    elif action == "ptoggle":
        pid = int(parts[2])
        p = db.get_product(pid)
        db.update_product(pid, active=0 if p["active"] else 1)
        await plan_detail(update, context, pid)

    elif action == "cdel":
        cid = int(parts[2])
        c = db.get_country(cid)
        st = db.country_stats(cid)
        await query.edit_message_text(
            f"⚠️ متأكد تحذف {c['flag']} {c['name']}؟\n\n"
            f"هيتحذف معاها {st['plans']} باقة و{st['available']} شريحة متاحة.\n"
            f"الشرايح المباعة ({st['sold']}) والطلبات القديمة هتفضل محفوظة للضمان والتقارير.",
            reply_markup=ui.kb([
                [ui.btn("🗑️ أيوه احذف", f"cat:cdelok:{cid}"), ui.btn("❌ إلغاء", f"cat:c:{cid}")]
            ]),
        )
    elif action == "cdelok":
        cid = int(parts[2])
        c = db.get_country(cid)
        n = db.delete_country(cid)
        db.log_action(query.from_user.id, "delete_country", f"{c['name']} ({n} plans)")
        await query.edit_message_text(
            f"✅ اتحذفت الدولة {c['name']} و{n} باقة.",
            reply_markup=ui.kb([[ui.btn("🗂️ رجوع", "cat:home")]]),
        )

    elif action == "pdel":
        pid = int(parts[2])
        p = db.get_product(pid)
        avail = db.count_available_stock(pid)
        await query.edit_message_text(
            f"⚠️ متأكد تحذف الباقة #{pid}؟\n{db.product_label(p)}\n\n"
            f"هيتحذف معاها {avail} شريحة متاحة. المباع والطلبات القديمة هتفضل محفوظة.",
            reply_markup=ui.kb([
                [ui.btn("🗑️ أيوه احذف", f"cat:pdelok:{pid}"), ui.btn("❌ إلغاء", f"cat:p:{pid}")]
            ]),
        )
    elif action == "pdelok":
        pid = int(parts[2])
        p = db.get_product(pid)
        cid = p["country_id"]
        db.delete_product(pid)
        db.log_action(query.from_user.id, "delete_product", f"{pid}")
        await query.edit_message_text(
            "✅ اتحذفت الباقة.",
            reply_markup=ui.kb([[ui.btn("🗂️ رجوع", f"cat:c:{cid}")]]),
        )


# ---------------------------------------------------------------- conversations

addcountry_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(addcountry_start, pattern=r"^cat:addcountry$")],
    states={
        ASK_C_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcountry_name)],
        ASK_C_FLAG: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcountry_flag)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

editcountry_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(cedit_start, pattern=r"^cat:cedit:\d+$")],
    states={
        ASK_CE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, cedit_name)],
        ASK_CE_FLAG: [MessageHandler(filters.TEXT & ~filters.COMMAND, cedit_flag)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

addplan_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(addplan_start, pattern=r"^cat:addplan:\d+$")],
    states={
        ASK_P_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, addplan_data)],
        ASK_P_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, addplan_days)],
        ASK_P_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addplan_price)],
        ASK_P_WARRANTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, addplan_warranty)],
        ASK_P_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, addplan_desc)],
        ASK_P_CONFIRM: [CallbackQueryHandler(addplan_confirm, pattern=r"^cat:plan(ok|cancel)$")],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

editplan_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(edit_field_start, pattern=r"^cat:edit:\d+:\w+$")],
    states={ASK_FIELD_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field_save)]},
    fallbacks=[CommandHandler("cancel", cancel)],
)

tier_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(tier_add_start, pattern=r"^cat:tieradd:\d+$")],
    states={
        ASK_TIER_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, tier_qty)],
        ASK_TIER_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, tier_price)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)
