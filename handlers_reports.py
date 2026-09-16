"""
Financial reporting.

Three things the panel answers:
  * كسبت كام؟   مبيعات − تكلفة الشرايح اللي اتباعت = صافي الربح
  * فلوس مين معايا؟  إجمالي أرصدة العملاء (ده التزام عليك، مش ربح)
  * فلوسي نايمة فين؟ قيمة المخزون المتاح بالتكلفة وبسعر البيع
"""

import csv
import io
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import db
import permissions as perms
import ui

log = logging.getLogger(__name__)

P = perms.P_REPORTS
PERIOD_KEYS = ["today", "yesterday", "7d", "30d", "month", "all"]


def _period_rows(prefix):
    keys = PERIOD_KEYS
    rows = []
    for i in range(0, len(keys), 3):
        rows.append([ui.btn(ui.PERIODS[k], f"{prefix}{k}") for k in keys[i:i + 3]])
    return rows


@ui.require(P)
async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sales, credited = db.lifetime_totals()
    balances = db.total_customer_balance()
    inv = db.inventory_value()
    profit = (sales["revenue"] or 0) - (sales["cost"] or 0)

    text = (
        "📊 <b>التقارير المالية</b>\n\n"
        "<b>من بداية البوت</b>\n"
        f"🛒 المبيعات: {sales['orders']} عملية / {sales['sims']} شريحة — "
        f"{ui.money(sales['revenue'])}\n"
        f"🏭 تكلفة الشرايح المباعة: {ui.money(sales['cost'])}\n"
        f"📈 صافي الربح: <b>{ui.money(profit)}</b>\n"
        f"💳 إجمالي اللي اتشحن لأرصدة العملاء: {ui.money(credited['total'])}\n\n"
        f"💰 أرصدة العملاء دلوقتي: <b>{ui.money(balances['total'])}</b> "
        f"({balances['holders']} عميل)\n"
        f"📦 مخزون متاح: {inv['units']} شريحة بتكلفة {ui.money(inv['cost_value'])}\n\n"
        "اختار فترة عشان تشوف تفاصيلها:"
    )
    rows = _period_rows("rep:p:")
    rows.append([ui.btn("💰 أرصدة العملاء", "rep:balances"),
                 ui.btn("📦 قيمة المخزون", "rep:inv")])
    rows.append([ui.hub_btn()])
    await ui.reply(update, text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(P)
async def period_report(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    start, end, label = ui.period_bounds(key)

    sales = db.sales_summary(start, end)
    topups = db.topups_summary(start, end)
    ledger = db.ledger_summary(start, end)
    new_users = db.new_users_count(start, end)

    revenue = sales["revenue"] or 0
    cost = sales["cost"] or 0
    profit = revenue - cost
    margin = (profit / revenue * 100) if revenue else 0
    sims = sales["sims"]
    avg = (revenue / sales["orders"]) if sales["orders"] else 0

    lines = [
        f"📊 <b>تقرير {label}</b>\n",
        "<b>🛒 المبيعات</b>",
        f"• عمليات الشراء: {sales['orders']}",
        f"• شرايح اتباعت: {sims}",
        f"• عدد المشترين: {sales['buyers']}",
        f"• الإيراد: <b>{ui.money(revenue)}</b>",
        f"• تكلفة الشرايح: {ui.money(cost)}",
        f"• صافي الربح: <b>{ui.money(profit)}</b> ({margin:.0f}%)",
        f"• متوسط الطلب: {ui.money(avg)}",
        "",
        "<b>💳 الشحن</b>",
        f"• عدد عمليات الشحن: {topups['n']}",
        f"• إجمالي المشحون: {ui.money(topups['total'])}",
    ]

    if ledger:
        lines.append("\n<b>📜 حركات الرصيد</b>")
        for r in ledger:
            lines.append(
                f"• {ui.LEDGER_AR.get(r['kind'], r['kind'])}: {r['n']} حركة — {ui.money(r['total'])}"
            )

    lines.append(f"\n👥 عملاء جدد: {new_users}")

    daily = db.daily_sales(start, end)
    if len(daily) > 1:
        peak = max(d["revenue"] for d in daily)
        lines.append("\n<b>📈 المبيعات باليوم</b>")
        for d in daily[-10:]:
            lines.append(f"<code>{d['day'][5:]}</code> {ui.bar(d['revenue'], peak)} {ui.money(d['revenue'])}")

    rows = [
        [ui.btn("🏆 الأكثر مبيعًا", f"rep:top:{key}"), ui.btn("📤 تصدير CSV", f"rep:csv:{key}")],
    ] + _period_rows("rep:p:") + [[ui.btn("⬅️ رجوع", "rep:home"), ui.hub_btn()]]

    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(P)
async def top_report(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    start, end, label = ui.period_bounds(key)
    products = db.top_products(start, end)
    countries = db.top_countries(start, end)
    customers = db.top_customers(start, end)

    lines = [f"🏆 <b>الأكثر مبيعًا — {label}</b>\n"]
    if products:
        lines.append("<b>📦 الباقات</b>")
        for p in products:
            lines.append(
                f"• {p.get('flag') or ''} {p.get('country') or '—'} {p.get('data_amount') or ''} — "
                f"{p['sold']} شريحة · {ui.money(p['revenue'])} · ربح {ui.money(p['profit'])}"
            )
    if countries:
        lines.append("\n<b>🌍 الدول</b>")
        for c in countries:
            lines.append(f"• {c.get('flag') or ''} {c.get('country') or '—'} — "
                         f"{c['sold']} شريحة · {ui.money(c['revenue'])}")
    if customers:
        lines.append("\n<b>👤 العملاء</b>")
        for c in customers:
            name = c.get("username") or c["user_id"]
            lines.append(f"• {name} — {c['orders']} عملية / {c['sims']} شريحة · "
                         f"{ui.money(c['spent'])}")
    if len(lines) == 1:
        lines.append("مفيش مبيعات في الفترة دي.")

    await ui.reply(update, "\n".join(lines),
                   reply_markup=ui.kb([[ui.btn("⬅️ رجوع", f"rep:p:{key}"), ui.hub_btn()]]),
                   parse_mode=ParseMode.HTML)


@ui.require(P)
async def balances_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    totals = db.total_customer_balance()
    top = db.list_users(limit=15, order="balance")
    lines = [
        "💰 <b>أرصدة العملاء</b>\n",
        f"الإجمالي المحتفظ به: <b>{ui.money(totals['total'])}</b>",
        f"عدد العملاء اللي عندهم رصيد: {totals['holders']}",
        f"إجمالي العملاء: {db.count_users()}\n",
        "<b>أعلى أرصدة</b>",
    ]
    for u in top:
        if u["balance"] <= 0:
            continue
        name = u.get("username") or u.get("first_name") or u["telegram_id"]
        lines.append(f"• {name} (<code>{u['telegram_id']}</code>) — {ui.money(u['balance'])}")
    lines.append("\n<i>الأرصدة دي فلوس العملاء عندك، محسوبة التزام مش ربح.</i>")
    await ui.reply(update, "\n".join(lines),
                   reply_markup=ui.kb([[ui.btn("⬅️ رجوع", "rep:home"), ui.hub_btn()]]),
                   parse_mode=ParseMode.HTML)


@ui.require(P)
async def inventory_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    counts = db.stock_counts()
    value = db.inventory_value()
    products = db.list_products_with_stock()
    suppliers = db.list_suppliers()

    lines = [
        "📦 <b>قيمة المخزون</b>\n",
        f"🟢 متاح: {counts['available']} شريحة",
        f"🔴 مباع: {counts['sold']} · ⚫ ملغي: {counts['void']}",
        f"💵 التكلفة المدفوعة في المتاح: <b>{ui.money(value['cost_value'])}</b>",
        f"🏷️ لو اتباع كله: {ui.money(value['sale_value'])}",
        f"📈 ربح متوقع: {ui.money(value['sale_value'] - value['cost_value'])}\n",
        "<b>حسب الباقة</b>",
    ]
    for p in products:
        if not p["available"]:
            continue
        lines.append(f"• {db.product_label(p)} — {p['available']} شريحة")
    if suppliers:
        lines.append("\n<b>حسب المورد</b>")
        for s in suppliers[:8]:
            lines.append(f"• {s['supplier']} — متاح {s['available']} · مصروف {ui.money(s['spent'])}")

    await ui.reply(update, "\n".join(lines),
                   reply_markup=ui.kb([[ui.btn("➕ إضافة ستوك", "stk:addnew")],
                                       [ui.btn("⬅️ رجوع", "rep:home"), ui.hub_btn()]]),
                   parse_mode=ParseMode.HTML)


@ui.require(P)
async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    start, end, label = ui.period_bounds(key)
    rows = db.orders_in_range(start, end)
    if not rows:
        await update.callback_query.answer("مفيش بيانات في الفترة دي.", show_alert=True)
        return

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "order_id", "datetime", "user_id", "username", "country", "plan",
        "sale_price", "cost_price", "profit", "supplier", "lpa_code",
        "warranty_until", "status",
    ])
    for r in rows:
        price = r["price"] or 0
        cost = r["cost_price"] or 0
        writer.writerow([
            r["id"], ui.fmt_dt(r["created_at"]), r["user_id"], r["username"] or "",
            r["country"] or "", r["data_amount"] or "", f"{price:.2f}", f"{cost:.2f}",
            f"{price - cost:.2f}", r["supplier"] or "", r["lpa_code"] or "",
            ui.fmt_dt(r["warranty_until"], False), r["status"],
        ])

    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))  # BOM so Excel reads Arabic
    data.name = f"orders-{key}.csv"
    await context.bot.send_document(
        update.callback_query.from_user.id, document=data,
        caption=f"📤 تصدير الطلبات — {label} ({len(rows)} صف)",
    )
    await update.callback_query.answer("اتبعت ✅")


@ui.require(P)
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]

    if action == "home":
        await panel(update, context)
    elif action == "p":
        await period_report(update, context, parts[2])
    elif action == "top":
        await top_report(update, context, parts[2])
    elif action == "balances":
        await balances_report(update, context)
    elif action == "inv":
        await inventory_report(update, context)
    elif action == "csv":
        await export_csv(update, context, parts[2])
