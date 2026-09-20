# Admin Refund Management - Quick Guide

## How to Access Refunds Panel

### From Admin Dashboard:
1. Send `/admin` or press **⚙️ لوحة التحكم** button
2. Click **💳 طلبات الاسترجاع** button
3. See list of pending refund requests

### From Main Menu:
- While in admin keyboard, press **💳 طلبات الاسترجاع** button directly

---

## Understanding the Refund Request Details

When you click on a refund request (#123), you'll see:

```
💳 طلب استرجاع #123
👤 @username (ID: 123456789)
💵 المبلغ: 50.00 USD
🆔 Binance ID: 23451234512345123
💬 السبب: الشريحة ما اشتغلت بالدول المطلوبة
🕒 الطلب: 2026-09-20 15:30
```

**Fields Explained:**
- **#ID** - Request ID (reference this in any notes)
- **Username/ID** - How to contact the customer
- **Amount** - How much to refund
- **Binance ID** - Where to send the USDT
- **Reason** - Why customer requested refund
- **Timestamp** - When request was submitted

---

## Approving a Refund

### Steps:

1. **Click the request** from the pending list
2. **Click "✅ الموافقة والتحويل"** button
3. **Enter the Binance transaction ID** (Txid)
   - Example: `0x1234567890abcdef...`
   - If you haven't transferred yet, type: `pending` or `WIP`
   - Or send later with the actual Txid
4. **Press send**

### What Happens Automatically:
- ✅ Status changes to **"Approved"**
- ✅ Txid is saved in the system
- ✅ Customer receives notification: 
  ```
  ✅ اتوافقت على الطلب #123
  💵 50.00 USD اتحولت لـ 23451234512345123
  Txid: 0x1234567890abcdef...
  ```
- ✅ Request logged in audit trail

### If You Forget the Txid:
You can approve with "pending" and update it later by viewing the request again.

---

## Rejecting a Refund

### Steps:

1. **Click the request** from the pending list
2. **Click "❌ الرفض"** button
3. **Enter rejection reason** (optional)
   - Example: "Binance ID غير صحيح - حاول كمان"
   - Or just skip if no reason needed
4. **Press send**

### What Happens Automatically:
- ❌ Status changes to **"Rejected"**
- ❌ Balance is **AUTOMATICALLY RESTORED** to customer
  - Amount = refund amount that was requested
  - Kind = "refund_rejected"
- ✅ Customer receives notification:
  ```
  ❌ للأسف اترفض طلب الاسترجاع #123
  💰 50.00 USD اترجع في رصيدك
  💬 السبب: Binance ID غير صحيح - حاول كمان
  ```
- ✅ Request logged in audit trail

**Important:** When you reject, the balance is **automatically restored**. You don't need to manually add it back.

---

## Common Scenarios

### Scenario 1: Valid Refund Request
```
Customer requests refund for non-working SIM
→ Approve with Txid
→ Customer gets notification
→ Done! ✅
```

### Scenario 2: Invalid Binance ID
```
Customer gives wrong Binance address
→ Reject with reason: "Binance ID غير صحيح"
→ Balance restored to customer
→ Customer receives notification with reason
→ Done! ✅
```

### Scenario 3: Pending Transfer
```
Customer requests refund but you haven't transferred yet
→ Approve with Txid: "pending" or "WIP"
→ Customer is notified
→ Later, when you transfer, you can view and update with real Txid
→ Done! ✅
```

### Scenario 4: Duplicate Request
```
Same customer has 2 requests for same issue
→ Approve one with Txid
→ Reject the other with reason: "Duplicate - request #123 approved"
→ Balance restored to customer for rejected one
→ Done! ✅
```

### Scenario 5: Dispute/Unclear
```
Customer's reason is unclear or you need to investigate
→ Leave it for now (don't approve or reject)
→ Message customer to clarify
→ Once clarified, approve or reject
```

---

## List View - What Each Column Means

```
💳 طلبات الاسترجاع (5)

#15 · @ahmad_2024 · 25.00 USD · 23451234512345123
#14 · @fatima_s · 50.00 USD · 98765432109876543
#13 · 987654321 · 10.00 USD · —
```

**Reading:**
- `#15` = Request ID
- `@ahmad_2024` = Username (or just ID if no username)
- `25.00 USD` = Amount to refund
- `23451234512345123` = Binance address

**Buttons:**
- Click any row to see full details + approve/reject options

---

## Audit Trail

All refund actions are logged. To see them:

1. Go to admin panel: `/admin`
2. Click "⚙️ الإعدادات"
3. Click "📜 سجل التصرفات"
4. Look for entries like:
   - `approve_refund #123 0x123abc...`
   - `reject_refund #123 Invalid address`

---

## Statuses Explained

### 🟠 معلق (Pending)
- Just created by customer
- Waiting for admin action
- Appears in main list

### ✅ موافق (Approved)
- Admin transferred the money
- Txid is recorded
- Customer was notified
- Removed from pending list

### ❌ مرفوض (Rejected)
- Admin rejected the request
- Balance was restored to customer
- Customer was notified
- Removed from pending list

---

## Tips & Best Practices

### ✅ DO:
- ✅ Respond quickly - customers are waiting
- ✅ Use rejection reasons - helps customer understand
- ✅ Check Binance ID validity before approving - USDT addresses are long and specific
- ✅ Keep Txid records - useful for customer support inquiries
- ✅ Log notes in rejection reason - helps future reference
- ✅ Batch process similar rejections (e.g., "Invalid ID") to be consistent

### ❌ DON'T:
- ❌ Don't approve without Txid unless you're sure you'll send it soon
- ❌ Don't reject just because you forget the Txid - approve with "pending" instead
- ❌ Don't process same request twice - check if already approved/rejected
- ❌ Don't forget that rejection **automatically restores balance** - no manual step needed

---

## Permission Requirements

To access refund management, you need:
- **Role:** Manager, Seller, or custom role
- **Permission:** `P_WALLET` (💳 شحن رصيد)

If you see "الصلاحية دي مش معاك" (You don't have permission):
- Contact the bot owner to add this permission to your account
- It's under the 💳 wallet/credit section of permissions

---

## Keyboard Shortcuts

From the refund detail screen, available buttons:
- **✅ الموافقة والتحويل** → Approve (starts conversation to enter Txid)
- **❌ الرفض** → Reject (starts conversation to enter reason)
- **⬅️ الطلبات** → Back to pending list
- **⚙️ لوحة التحكم** → Back to main admin panel

---

## What You Can't Do (Yet)

These features aren't available but could be added:
- ❌ Edit approved/rejected requests
- ❌ Export refund history as CSV
- ❌ Bulk approve multiple requests
- ❌ Schedule future notifications
- ❌ Add custom reason templates

---

## FAQ

**Q: Can I undo an approval?**
A: Currently no. Once approved, status is locked. Contact bot owner for database edit if critical.

**Q: What if customer gives wrong Binance ID?**
A: Reject with reason "Binance ID خاطي" and their balance is restored. They can request again.

**Q: How long does USDT transfer take?**
A: Typically 1-5 minutes on most chains. Blockchain dependent. Check TxHash on blockchain explorer.

**Q: What if customer is unhappy with rejection?**
A: Their balance is already restored. They can try again or contact support via `/help`.

**Q: Can I see approval/rejection history?**
A: Yes! View the request detail again - approved/rejected requests show the Txid or reason, date, and who approved it.

**Q: Is there a daily limit?**
A: No. Refund amount is whatever customer requested. No system limit.

---

## Support

If you encounter issues:
1. Check the "📜 سجل التصرفات" (Audit log) for what happened
2. Look at pending requests list to see current status
3. Contact bot owner with request #ID if something seems wrong

**Common Error:** "الطلب ده معالج بالفعل"
- Means: Request was already approved/rejected
- Solution: Go back and view again - status should show approved/rejected with details

---

**Last Updated:** 2026-09-20
**Version:** 1.0 - Complete Refund System
