"""
Binance API withdrawals and transfers.

يركب على Binance API بتاعتك — محتاج BINANCE_API_KEY و BINANCE_API_SECRET 
في .env, و إذن "Withdraw" في الـ API key settings.
"""

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import requests


class BinanceTransfer:
    def __init__(s):
        s.key = os.environ.get("BINANCE_API_KEY", "").strip()
        s.secret = os.environ.get("BINANCE_API_SECRET", "").strip()
        s.base = "https://api.binance.com"

    def _sign(s, data):
        """HMAC-SHA256 signature."""
        return hmac.new(
            s.secret.encode(), urlencode(data).encode(), hashlib.sha256
        ).hexdigest()

    def _request(s, method, endpoint, data=None, **kw):
        """Raw API call."""
        if not s.key or not s.secret:
            return {"error": "API keys not configured"}

        url = s.base + endpoint
        ts = int(time.time() * 1000)
        params = {"timestamp": ts}

        if data:
            params.update(data)

        params["signature"] = s._sign(params)
        headers = {"X-MBX-APIKEY": s.key}

        try:
            resp = requests.request(
                method, url, params=params, headers=headers, timeout=10, **kw
            )
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def withdraw(s, coin, network, address, amount, **kw):
        """Withdraw to an address."""
        return s._request(
            "POST",
            "/wapi/v3/withdraw.html",
            {
                "coin": coin,
                "network": network,
                "address": address,
                "amount": amount,
                "transactionFeeFlag": True,
                "name": kw.get("name", ""),
            },
        )

    def get_deposit_addr(s, coin, network=None):
        """Fetch your own deposit address."""
        return s._request(
            "GET",
            "/wapi/v3/depositAddress.html",
            {"coin": coin, "network": network},
        )

    def query_order(s, txid):
        """Check withdrawal status."""
        return s._request("GET", "/wapi/v3/withdrawHistory.html", {"txid": txid})


async def execute_refund(refund_req_id, coin="USDT", network="TRX"):
    """
    Execute a refund from db record. Runs async in background.
    Admin message completes immediately, withdrawal status comes later.
    """
    import db

    req = db.get_refund_request(refund_req_id)
    if not req or req["status"] != "approved":
        return {"error": "request not approved"}

    address = req.get("usdt_address") or f"binance:{req['binance_id']}"
    amount = req["amount"]

    client = BinanceTransfer()
    if not client.key or not client.secret:
        return {"error": "Binance API not configured"}

    result = client.withdraw(
        coin=coin,
        network=network,
        address=address,
        amount=amount,
        name=f"refund-{refund_req_id}",
    )

    if "error" in result:
        return result

    txid = result.get("id")
    if txid:
        db.process_refund(refund_req_id, "approved", txid=txid)
        db.log_action(None, "auto_transfer", f"refund {refund_req_id} sent: {txid}")

    return result


async def check_refund_status(refund_req_id):
    """Check if a transfer completed."""
    import db

    req = db.get_refund_request(refund_req_id)
    if not req or not req.get("txid"):
        return None

    client = BinanceTransfer()
    status = client.query_order(req["txid"])
    return status
