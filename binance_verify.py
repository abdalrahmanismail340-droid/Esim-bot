"""
Verifies incoming Binance Pay transactions by transaction ID.

Uses Binance's Pay Transaction History endpoint (GET /sapi/v1/pay/transactions),
which lists Binance Pay transfers received on the account tied to the API key.

Docs: https://developers.binance.com/docs/binance-pay/api-order-transaction-history
Requires an API key/secret with "Enable Reading" permission (no trading needed).
"""

import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.binance.com"
TRANSACTIONS_ENDPOINT = "/sapi/v1/pay/transactions"


class BinanceVerifyError(Exception):
    pass


def _signed_get(api_key: str, api_secret: str, params: dict) -> dict:
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    params.setdefault("recvWindow", 5000)
    query_string = urlencode(params)
    signature = hmac.new(
        api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    params["signature"] = signature
    headers = {"X-MBX-APIKEY": api_key}
    resp = requests.get(BASE_URL + TRANSACTIONS_ENDPOINT, params=params, headers=headers, timeout=10)
    if resp.status_code != 200:
        raise BinanceVerifyError(f"Binance API error {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("code") != "000000":
        raise BinanceVerifyError(f"Binance API returned: {data}")
    return data


def get_recent_pay_transactions(api_key: str, api_secret: str, lookback_hours: int = 48) -> list:
    """Fetch Pay transactions received in the last `lookback_hours`."""
    end_time = int(time.time() * 1000)
    start_time = end_time - lookback_hours * 3600 * 1000
    all_tx = []
    params = {"startTime": start_time, "endTime": end_time, "limit": 100}
    data = _signed_get(api_key, api_secret, params)
    all_tx.extend(data.get("data", []))
    return all_tx


def find_matching_transaction(api_key: str, api_secret: str, tx_id: str, min_amount: float,
                               currency: str = "USDT", lookback_hours: int = 48):
    """
    Looks for a received Pay transaction whose transactionId/orderId matches tx_id
    and whose amount covers min_amount. Returns the transaction dict or None.
    """
    match = get_transaction_by_id(api_key, api_secret, tx_id, lookback_hours)
    if not match:
        return None
    amount = abs(float(match.get("amount", 0)))
    tx_currency = match.get("currency", currency)
    if tx_currency == currency and amount >= min_amount * 0.99:
        return match
    return None


def get_transaction_by_id(api_key: str, api_secret: str, tx_id: str, lookback_hours: int = 48):
    """
    Looks up a received Pay transaction by transaction/order ID, regardless of amount.
    Used for wallet top-ups where the amount isn't known ahead of time.
    Returns the transaction dict or None.
    """
    try:
        transactions = get_recent_pay_transactions(api_key, api_secret, lookback_hours)
    except Exception as e:
        log.warning(f"Binance verify lookup failed: {e}")
        return None

    tx_id = tx_id.strip()
    for tx in transactions:
        candidate_ids = {
            str(tx.get("transactionId", "")),
            str(tx.get("orderId", "")),
            str(tx.get("transactionRefId", "")),
        }
        if tx_id in candidate_ids:
            return tx
    return None
