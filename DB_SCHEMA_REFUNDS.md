# Refund System - Database Requirements

## Required Table: `refund_requests`

```sql
CREATE TABLE IF NOT EXISTS refund_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    amount REAL NOT NULL,
    binance_id TEXT,
    usdt_address TEXT,
    reason TEXT,
    status TEXT DEFAULT 'pending',  -- pending, approved, rejected
    txid TEXT,
    admin_note TEXT,
    approved_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id),
    FOREIGN KEY(approved_by) REFERENCES staff(user_id)
);

CREATE INDEX IF NOT EXISTS idx_refund_status ON refund_requests(status);
CREATE INDEX IF NOT EXISTS idx_refund_user ON refund_requests(user_id);
```

## Required db.py Functions

### 1. Create Refund Request
```python
def create_refund_request(user_id: int, amount: float, binance_id: str, reason: str) -> int:
    """
    Create a new refund request.
    Returns the request ID.
    """
```

### 2. List Refund Requests
```python
def list_refund_requests(status: str = "pending", limit: int = 20) -> List[dict]:
    """
    Get refund requests by status.
    Returns list of dicts with fields:
    - id, user_id, username, amount, binance_id, usdt_address, reason, status, 
      txid, admin_note, created_at, updated_at
    """
```

### 3. Get Single Refund Request
```python
def get_refund_request(req_id: int) -> dict:
    """
    Get a single refund request by ID.
    Returns dict with all fields, or None if not found.
    """
```

### 4. Process/Update Refund
```python
def process_refund(req_id: int, status: str, txid: str = None, admin_note: str = None):
    """
    Update refund status. Called by admin to approve or reject.
    - status: 'approved' or 'rejected'
    - txid: Binance transaction ID (when approving)
    - admin_note: Rejection reason (when rejecting)
    """
```

### 5. Mark Blocked Bot (already exists, but verify)
```python
def mark_blocked_bot(user_id: int, blocked: bool):
    """Mark if user has blocked the bot."""
```

### 6. Count Blocked Bot Users (already exists, but verify)
```python
def count_blocked_bot() -> int:
    """Count how many users have blocked the bot."""
```

### 7. List Blocked Bot Users (already exists, but verify)
```python
def list_blocked_bot_users(limit: int = 50) -> List[dict]:
    """Get users who have blocked the bot."""
```

## Integration with Existing Functions

The refund system uses these existing functions (must already exist):

- `db.get_user(user_id)` - Get user info
- `db.get_balance(user_id)` - Get current balance
- `db.add_balance(user_id, amount, kind, note, ...)` - Add balance
- `db.deduct_balance(user_id, amount, kind, note, ...)` - Deduct balance
- `db.log_action(admin_id, action, details)` - Log admin action
- `db.count_users()` - Total user count
- `db.list_users(limit, order)` - List users
- `db.find_users(text, limit)` - Search users by username/name
- `db.is_banned(user_id)` - Check if banned
- `db.set_banned(user_id, banned)` - Set ban status
- `db.list_banned_users()` - Get banned users list
- `db.list_staff()` - Get staff list
- `db.is_maintenance()` - Check maintenance mode
- `db.get_maintenance_message()` - Get maintenance message
- `db.is_staff(user_id)` - Check if staff (from permissions, not db)

## Usage in handlers_user.py

```python
# In refund_confirm():
db.create_refund_request(
    user_id=user_id,
    amount=amount,
    binance_id=binance_id,
    reason=reason
)
```

## Usage in handlers_admin.py

```python
# In refunds_panel():
requests = db.list_refund_requests("pending", 20)

# In refund_detail():
req = db.get_refund_request(req_id)

# In refund_txid_save() - approve:
db.process_refund(req_id, "approved", txid=txid)

# In refund_note_save() - reject:
db.add_balance(user_id, amount, kind="refund_rejected", ...)
db.process_refund(req_id, "rejected", admin_note=note)
```

## Admin Notification

When a refund request is created, notify admins:

```python
# At end of refund_confirm() in handlers_user.py:
await ui.notify_admins(
    context,
    f"💳 <b>طلب استرجاع جديد</b>\n"
    f"👤 {user.first_name or user.id}\n"
    f"💵 المبلغ: {ui.money(amount)}\n"
    f"🆔 {binance_id}\n"
    f"📝 {reason}",
    perm=perms.P_WALLET
)
```
