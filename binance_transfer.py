"""Binance transfer and Binance Pay Merchant API helpers."""

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import requests


class BinanceTransfer:
    """Legacy Binance Wallet transfer client used by refund operations."""

    def __init__(self):
        self.key = os.environ.get("BINANCE_API_KEY", "").strip()
        self.secret = os.environ.get("BINANCE_API_SECRET", "").strip()
        self.base = "https://api.binance.com"

    def _sign(self, data):
        return hmac.new(
            self.secret.encode(), urlencode(data).encode(), hashlib.sha256
        ).hexdigest()

    def _request(self, method, endpoint, data=None, **kwargs):
        if not self.key or not self.secret:
            return {"error": "API keys not configured"}
        params = {"timestamp": int(time.time() * 1000)}
        if data:
            params.update(data)
        params["signature"] = self._sign(params)
        try:
            response = requests.request(
                method,
                self.base + endpoint,
                params=params,
                headers={"X-MBX-APIKEY": self.key},
                timeout=10,
                **kwargs,
            )
            return response.json()
        except Exception as exc:
            return {"error": str(exc)}

    def withdraw(self, coin, network, address, amount, **kwargs):
        return self._request(
            "POST",
            "/wapi/v3/withdraw.html",
            {
                "coin": coin,
                "network": network,
                "address": address,
                "amount": amount,
                "transactionFeeFlag": True,
                "name": kwargs.get("name", ""),
            },
        )

    def get_deposit_addr(self, coin, network=None):
        return self._request(
            "GET", "/wapi/v3/depositAddress.html", {"coin": coin, "network": network}
        )

    def query_order(self, txid):
        return self._request("GET", "/wapi/v3/withdrawHistory.html", {"txid": txid})


# Binance Pay Merchant API
BINANCE_PAY_BASE_URL = "https://bpay.binanceapi.com"
BINANCE_PAY_QUERY_PATH = "/binancepay/openapi/v2/order/query"


def _binance_pay_signature(timestamp: str, nonce: str, body: str, secret: str) -> str:
    payload = f"{timestamp}\n{nonce}\n{body}\n"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha512).hexdigest().upper()


def query_binance_pay_order(
    api_key: str, secret: str, certificate_sn: str, identifier: str
) -> dict[str, Any]:
    """Query a Binance Pay order by merchantTradeNo or prepayId."""
    identifier = identifier.strip()
    body = json.dumps(
        {"merchantTradeNo": identifier}, separators=(",", ":"), ensure_ascii=False
    )
    timestamp = str(int(time.time() * 1000))
    nonce = secrets.token_hex(16)
    headers = {
        "Content-Type": "application/json",
        "BinancePay-Timestamp": timestamp,
        "BinancePay-Nonce": nonce,
        "BinancePay-Certificate-SN": certificate_sn,
        "BinancePay-Signature": _binance_pay_signature(
            timestamp, nonce, body, secret
        ),
    }
    try:
        response = requests.post(
            BINANCE_PAY_BASE_URL + BINANCE_PAY_QUERY_PATH,
            data=body.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        payload = response.json()
    except Exception as exc:
        return {"ok": False, "error": f"Binance Pay request failed: {exc}"}

    if (
        response.status_code != 200
        or payload.get("status") != "SUCCESS"
        or payload.get("code") != "000000"
    ):
        return {
            "ok": False,
            "error": payload.get("errorMessage")
            or payload.get("code")
            or f"HTTP {response.status_code}",
            "raw": payload,
        }

    data = payload.get("data") or {}
    return {
        "ok": True,
        "status": data.get("status"),
        "amount": float(data.get("totalFee") or 0),
        "currency": data.get("currency"),
        "transaction_id": data.get("transactionId"),
        "merchant_trade_no": data.get("merchantTradeNo"),
        "prepay_id": data.get("prepayId"),
    }


async def execute_refund(refund_req_id, coin="USDT", network="TRX"):
    """Execute a refund from an approved database record."""
    import db

    req = db.get_refund_request(refund_req_id)
    if not req or req["status"] != "approved":
        return {"error": "request not approved"}
    address = req.get("usdt_address") or f"binance:{req['binance_id']}"
    client = BinanceTransfer()
    if not client.key or not client.secret:
        return {"error": "Binance API not configured"}
    result = client.withdraw(
        coin=coin,
        network=network,
        address=address,
        amount=req["amount"],
        name=f"refund-{refund_req_id}",
    )
    if "error" not in result and result.get("id"):
        db.process_refund(refund_req_id, "approved", txid=result["id"])
        db.log_action(None, "auto_transfer", f"refund {refund_req_id} sent: {result['id']}")
    return result


async def check_refund_status(refund_req_id):
    import db

    req = db.get_refund_request(refund_req_id)
    if not req or not req.get("txid"):
        return None
    return BinanceTransfer().query_order(req["txid"])
