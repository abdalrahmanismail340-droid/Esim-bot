# Refund System - Testing & Troubleshooting

## Pre-Deployment Checklist

### Files
- [ ] `handlers_user.py` copied to bot directory
- [ ] `handlers_admin.py` copied to bot directory  
- [ ] `ui.py` copied to bot directory
- [ ] `bot.py` copied to bot directory
- [ ] All imports are correct

### Database
- [ ] `refund_requests` table created
- [ ] Table indexes created
- [ ] db.py has all 4 refund functions implemented:
  - [ ] `create_refund_request()`
  - [ ] `list_refund_requests()`
  - [ ] `get_refund_request()`
  - [ ] `process_refund()`
- [ ] Can connect to database without errors

### Configuration
- [ ] `config.BOT_TOKEN` is valid
- [ ] `config.OWNER_IDS` contains at least one admin
- [ ] `config.ADMIN_CREDIT`, `config.ADMIN_WARRANTY`, `config.ADMIN_BROADCAST` defined
- [ ] `config.TIMEZONE` is correct
- [ ] `config.CURRENCY` is set (e.g., "USD")
- [ ] `config.SUPPORT_CONTACT` is set

### Permissions
- [ ] `permissions.P_WALLET` is defined
- [ ] At least one admin has P_WALLET permission
- [ ] `permissions.can()` function works

### Bot Registration
- [ ] handlers_user.refund_conv registered in bot.py
- [ ] handlers_admin.refund_conv registered in bot.py
- [ ] handlers_admin.refund_reject_conv registered in bot.py
- [ ] Refund callback handler registered (adm:refund*)
- [ ] Text message handler catches "💳 طلبات الاسترجاع" button

---

## Testing - Customer Flow

### Test 1: Start Refund Request
```
Customer action: Send /refund
Expected result:
  ✅ Bot responds: "💰 طلب استرجاع\nابعت المبلغ المطلوب استرجاعه..."
  ✅ Waiting for amount input
  ✅ State: REFUND_AMOUNT
```

**If fails:**
- [ ] Check `/refund` command is handled
- [ ] Check `refund_conv` is registered in bot.py
- [ ] Check message handler filters are correct

---

### Test 2: Enter Amount
```
Customer action: Send "25"
Expected result:
  ✅ Bot responds: "ابعت Binance ID أو عنوان USDT..."
  ✅ Waiting for Binance ID input
  ✅ State: REFUND_ID_OR_ADDR
```

**If fails:**
- [ ] Check `refund_amount()` function in handlers_user.py
- [ ] Check amount validation (must be positive number)
- [ ] Check `context.user_data["refund_amount"]` is being saved

---

### Test 3: Enter Binance ID
```
Customer action: Send "BinanceUserID12345" or "0xUSDTAddress..."
Expected result:
  ✅ Bot responds: "ايش السبب؟ (optional)"
  ✅ Waiting for reason input
  ✅ State: REFUND_REASON
```

**If fails:**
- [ ] Check `refund_id()` function in handlers_user.py
- [ ] Check either binance_id or usdt_address is accepted
- [ ] Check `context.user_data["refund_binance_id"]` is being saved
- [ ] This is where the bug was - check `refund_reason()` has defaults!

---

### Test 4: Enter Reason
```
Customer action: Send "الشريحة ما شتغلت في الدول المطلوبة"
Expected result:
  ✅ Bot shows confirmation screen:
    "📋 مراجعة
     👤 123456789
     💵 استرجاع: 25.00 USD
     🆔 BinanceUserID12345
     📝 السبب: الشريحة ما شتغلت...
     [✅ تأكيد] [❌ إلغاء]"
  ✅ State: REFUND_CONFIRM
```

**If this fails, the bug is still present:**
- [ ] Check `refund_reason()` has default values in `.pop()` calls
- [ ] Check validation block exists before showing confirmation
- [ ] Check keys are re-saved to context.user_data
- [ ] Check error message is user-friendly if validation fails

---

### Test 5: Confirm Refund
```
Customer action: Click [✅ تأكيد]
Expected result:
  ✅ Bot shows: "✅ تم إرسال طلب الاسترجاع #1"
  ✅ Conversation ends
  ✅ Admin receives notification
  ✅ Database shows entry in refund_requests table
```

**If fails:**
- [ ] Check `refund_confirm()` calls `db.create_refund_request()`
- [ ] Check admin notification via `notify_admins()`
- [ ] Check database INSERT was successful
- [ ] Check error logging if database operation fails

---

## Testing - Admin Flow

### Test 6: Access Admin Panel
```
Admin action: Send /admin
Expected result:
  ✅ Admin keyboard appears with "💳 طلبات الاسترجاع" button
  ✅ Admin has P_WALLET permission
```

**If button doesn't appear:**
- [ ] Check admin has P_WALLET permission
- [ ] Check `admin_menu_keyboard()` in ui.py includes refund button
- [ ] Check permission check: `can(perms.P_WALLET)`

---

### Test 7: Open Refunds Panel
```
Admin action: Click "💳 طلبات الاسترجاع"
Expected result:
  ✅ Bot shows: "💳 طلبات الاسترجاع (1)\n#1 · @customer · 25.00 USD · BinanceID..."
  ✅ One button for request #1
```

**If fails or shows empty:**
- [ ] Check `refunds_panel()` function in handlers_admin.py
- [ ] Check database has refund_requests entry
- [ ] Check query: `db.list_refund_requests("pending", 20)`
- [ ] Check permission gate: `@ui.require(perms.P_WALLET)`

---

### Test 8: View Request Detail
```
Admin action: Click request button
Expected result:
  ✅ Shows full details:
    "💳 طلب استرجاع #1
     👤 @customer (123456789)
     💵 25.00 USD
     🆔 Binance ID: BinanceID123
     💬 السبب: الشريحة ما شتغلت...
     🕒 التاريخ: 2026-09-20 15:30
     
     [✅ الموافقة] [❌ الرفض]"
```

**If fails:**
- [ ] Check `refund_detail()` calls `db.get_refund_request()`
- [ ] Check database SELECT works
- [ ] Check callback routing: `adm:refund:{id}`

---

### Test 9: Approve Refund
```
Admin action: Click "✅ الموافقة والتحويل"
Expected result:
  ✅ Bot asks: "ابعت Binance Txid (رقم المعاملة)..."
  ✅ State: REFUND_TXID
```

**If fails:**
- [ ] Check `refund_approve_start()` in handlers_admin.py
- [ ] Check callback routing: `adm:refundapp:{id}`
- [ ] Check conversation handler: `refund_conv`

---

### Test 10: Enter Transaction ID
```
Admin action: Send "0x1234567890abcdef"
Expected result:
  ✅ Bot shows: "✅ اتسجل التحويل #1
                💵 25.00 USD لـ BinanceID
                Txid: 0x1234567890abcdef"
  ✅ Conversation ends
  ✅ Customer receives notification
  ✅ Database status changed to "approved"
```

**If fails:**
- [ ] Check `refund_txid_save()` calls `db.process_refund()`
- [ ] Check customer notification is sent
- [ ] Check database UPDATE worked
- [ ] Check audit log has entry

---

### Test 11: Reject Refund
```
Admin action: (go back to list, open another request)
Admin action: Click "❌ الرفض"
Expected result:
  ✅ Bot asks: "سبب الرفض (اختياري)..."
  ✅ State: REFUND_NOTE
```

**If fails:**
- [ ] Check `refund_reject_start()` in handlers_admin.py
- [ ] Check callback routing: `adm:refundrec:{id}`
- [ ] Check conversation handler: `refund_reject_conv`

---

### Test 12: Enter Rejection Reason
```
Admin action: Send "Binance ID خاطي"
Expected result:
  ✅ Bot shows: "❌ اترفض الطلب #2 ورجع الرصيد 25.00 USD"
  ✅ Conversation ends
  ✅ Customer receives notification with reason
  ✅ Customer's balance increased by 25.00 USD
  ✅ Database status changed to "rejected"
```

**If fails:**
- [ ] Check `refund_note_save()` calls:
  - [ ] `db.add_balance()` to restore amount
  - [ ] `db.process_refund()` to update status
- [ ] Check customer notification is sent with reason
- [ ] Check database UPDATE worked
- [ ] Check balance was actually restored (query customer's balance)

---

## Database Verification

### Query 1: Check Table Structure
```sql
SELECT sql FROM sqlite_master 
WHERE type='table' AND name='refund_requests';
```

**Should show:**
- id, user_id, username, amount, binance_id, usdt_address
- reason, status, txid, admin_note, approved_by
- created_at, updated_at

---

### Query 2: Check Data After Customer Submit
```sql
SELECT * FROM refund_requests WHERE status='pending';
```

**Should show:**
- One row per pending request
- status = 'pending'
- txid = NULL
- admin_note = NULL

---

### Query 3: Check Data After Admin Approves
```sql
SELECT * FROM refund_requests WHERE id=1;
```

**Should show:**
- status = 'approved'
- txid = 'entered value'
- updated_at = recent timestamp

---

### Query 4: Check Data After Admin Rejects
```sql
SELECT * FROM refund_requests WHERE id=2;
```

**Should show:**
- status = 'rejected'
- admin_note = 'entered reason'
- updated_at = recent timestamp

---

## Common Errors & Solutions

### Error: "حصل خطأ، حاول ثاني"
**Cause:** Bug in `refund_reason()` - `.pop()` without default values

**Solution:**
```python
# WRONG:
user_id = context.user_data.pop("refund_uid")  # KeyError if missing!

# CORRECT:
user_id = context.user_data.pop("refund_uid", None)
if user_id is None:
    await update.message.reply_text("❌ حصل خطأ في البيانات...")
```

**Verify:** Line ~450 in refund_reason()

---

### Error: "الصلاحية دي مش معاك"
**Cause:** Admin doesn't have P_WALLET permission

**Solution:**
- Add permission to admin in `permissions` table or config
- Check: `perms.can(admin_id, perms.P_WALLET)` returns True

---

### Error: "طلب استرجاع جديد" button doesn't appear
**Cause:** Refund button not in admin_menu_keyboard()

**Solution:**
- Check ui.py has this line:
```python
pair("💳 طلبات الاسترجاع" if can(perms.P_WALLET) else None, ...)
```

---

### Error: Button shows but clicking does nothing
**Cause:** Message handler not routing text button

**Solution:**
- Check bot.py text_message() has:
```python
if text == "💳 طلبات الاسترجاع":
    if perms.is_staff(user_id) and perms.can(user_id, perms.P_WALLET):
        await handlers_admin.refunds_panel(update, context)
```

---

### Error: "KeyError: 'refund_uid'" during refund_reason()
**Cause:** The original bug - still present

**Solution:**
- Replace entire refund_reason() function from delivered handlers_user.py
- Verify default values in all .pop() calls
- Verify re-save before REFUND_CONFIRM

---

### Error: Database queries fail
**Cause:** Missing db functions or wrong column names

**Solution:**
- Run database verification queries above
- Check function signatures match:
  - `def create_refund_request(user_id, amount, binance_id, reason)`
  - `def list_refund_requests(status, limit)`
  - `def get_refund_request(req_id)`
  - `def process_refund(req_id, status, txid, admin_note)`

---

### Error: Admin notification not sent
**Cause:** `notify_admins()` not working or permission not set

**Solution:**
- Check line in refund_confirm():
```python
await ui.notify_admins(context, text, perm=perms.P_WALLET)
```
- Verify notify_admins() in ui.py exists and works
- Check admin IDs in config.OWNER_IDS

---

## Performance Testing

### Test with Multiple Requests
```
1. Submit 10 refund requests as different customers
2. Click "💳 طلبات الاسترجاع"
3. Should show all 10 in list
4. Should load in <2 seconds
```

---

### Test Database Performance
```sql
-- Check index is used
EXPLAIN QUERY PLAN 
SELECT * FROM refund_requests 
WHERE status='pending' 
ORDER BY created_at DESC 
LIMIT 20;
```

Should use `idx_refund_status` index.

---

## Stress Testing

### Test Concurrent Requests
```
1. Have 2 admins open refund panel simultaneously
2. Have 1 admin approve request #1
3. Have other admin try to approve same request #1
4. Second admin should get: "الطلب ده معالج بالفعل"
```

---

## Final Validation

Before going live, verify:

- [ ] All 5 files are in place
- [ ] All db functions implemented
- [ ] Database table exists with correct schema
- [ ] At least one admin has P_WALLET permission
- [ ] Bot starts without import errors
- [ ] Customer can complete full refund flow
- [ ] Admin can view and process refunds
- [ ] Notifications work for both approve and reject
- [ ] Balance is restored on reject
- [ ] Audit log records actions
- [ ] No SQL errors in logs
- [ ] No permission errors in logs
- [ ] Conversation handlers work without timeout
- [ ] Text buttons route correctly
- [ ] Callback queries are answered within 30 seconds

---

## Success Criteria

✅ **System is ready when:**
- Customer can request refund in <5 steps
- Admin can process refund in <1 minute
- Both are notified with complete details
- Balance is correct after rejection
- No errors in bot logs
- Database is clean and indexed
- Performance is smooth (no delays)

---

**Test Status:** [  ] IN PROGRESS  [  ] COMPLETE  [  ] ISSUES FOUND

**Issues Found:**
```
[list any issues here]
```

**Approved by:** ________________  **Date:** ___________

**Ready for Production:** [  ] YES  [  ] NO

---

*Remember: Test thoroughly in a safe environment before deploying to production!*
