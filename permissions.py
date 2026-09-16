"""
Who is allowed to do what.

Two sources of authority:
  * OWNER_IDS from the .env — full power, can't be edited or removed from inside
    the bot, so you can never lock yourself out.
  * the staff table — admins you add from the panel, each with a chosen set of
    permissions.

A permission check is always `permissions.can(user_id, P_SOMETHING)`.
"""

import config
import db

P_CATALOG = "catalog"       # الدول والباقات: إضافة/تعديل/حذف
P_STOCK = "stock"           # إضافة ستوك وحذفه
P_SEARCH = "search"         # البحث في الشرايح والعملاء
P_REPORTS = "reports"       # التقارير المالية
P_WALLET = "wallet"         # شحن/خصم رصيد يدوي
P_USERS = "users"           # عرض العملاء، حظر وفك حظر
P_BROADCAST = "broadcast"   # الرسائل الجماعية
P_WARRANTY = "warranty"     # طلبات الضمان
P_SETTINGS = "settings"     # الصيانة والإعدادات
P_STAFF = "staff"           # إدارة الأدمن والصلاحيات (للمالك فقط)

ALL_PERMS = [
    P_CATALOG, P_STOCK, P_SEARCH, P_REPORTS, P_WALLET,
    P_USERS, P_BROADCAST, P_WARRANTY, P_SETTINGS, P_STAFF,
]

PERM_LABELS = {
    P_CATALOG: "🗂️ الدول والباقات",
    P_STOCK: "📦 المخزون",
    P_SEARCH: "🔍 البحث",
    P_REPORTS: "📊 التقارير المالية",
    P_WALLET: "💰 شحن الرصيد",
    P_USERS: "👥 العملاء والحظر",
    P_BROADCAST: "📢 الرسائل الجماعية",
    P_WARRANTY: "🛡️ طلبات الضمان",
    P_SETTINGS: "⚙️ الإعدادات",
    P_STAFF: "🔑 إدارة الأدمن",
}

# Ready-made roles so you don't have to tick boxes every time.
ROLE_PRESETS = {
    "owner": ALL_PERMS,
    "manager": [P_CATALOG, P_STOCK, P_SEARCH, P_REPORTS, P_WALLET, P_USERS,
                P_BROADCAST, P_WARRANTY, P_SETTINGS],
    "seller": [P_STOCK, P_SEARCH, P_WALLET, P_WARRANTY],
    "support": [P_SEARCH, P_WARRANTY, P_USERS],
    "viewer": [P_SEARCH, P_REPORTS],
}

ROLE_LABELS = {
    "owner": "👑 مالك",
    "manager": "🧑‍💼 مدير",
    "seller": "🛒 بائع",
    "support": "🧑‍💻 دعم",
    "viewer": "👀 مشاهدة فقط",
    "custom": "🎛️ مخصص",
}


def is_owner(user_id: int) -> bool:
    return user_id in config.OWNER_IDS


def perms_of(user_id: int):
    """The full permission list for a user. Empty list means not staff at all."""
    if is_owner(user_id):
        return list(ALL_PERMS)
    row = db.get_staff(user_id)
    if not row:
        return []
    raw = (row.get("perms") or "").strip()
    if raw == "*":
        return [p for p in ALL_PERMS if p != P_STAFF]
    return [p for p in raw.split(",") if p in ALL_PERMS]


def is_staff(user_id: int) -> bool:
    return is_owner(user_id) or bool(db.get_staff(user_id))


def can(user_id: int, perm: str) -> bool:
    if is_owner(user_id):
        return True
    if perm == P_STAFF:
        return False  # only owners manage staff
    return perm in perms_of(user_id)


def role_of(user_id: int) -> str:
    if is_owner(user_id):
        return "owner"
    row = db.get_staff(user_id)
    return row["role"] if row else ""


def describe(user_id: int) -> str:
    role = role_of(user_id)
    if not role:
        return "مش أدمن"
    perms = perms_of(user_id)
    label = ROLE_LABELS.get(role, role)
    if role == "owner":
        return f"{label} — كل الصلاحيات"
    names = "، ".join(PERM_LABELS[p] for p in perms) or "مفيش صلاحيات"
    return f"{label}\n{names}"
