import os
import sys
import time
import secrets
import json
import base64
import httpx
from datetime import datetime
from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

load_dotenv()

API_URL = "https://agent-paywall.onrender.com/v1/alpha-confluence"
WATCHLIST = ["BTCUSDT", "PAXGUSDT"]
POLL_INTERVAL_SECONDS = 900  # 15 minutes

BUYER_KEY = os.getenv("TRADER_PRIVATE_KEY")
if not BUYER_KEY:
    print("ERROR: Set TRADER_PRIVATE_KEY in your .env file with your Base wallet private key.")
    sys.exit(1)

if not BUYER_KEY.startswith("0x"):
    BUYER_KEY = "0x" + BUYER_KEY

trader_account = Account.from_key(BUYER_KEY)
print(f"[*] Autonomous Trading Agent Started | Wallet: {trader_account.address}")


def query_institutional_confluence(symbol: str) -> dict:
    payload = {"symbol": symbol}
    with httpx.Client(timeout=45.0) as client:
        probe = client.post(API_URL, json=payload)
        if probe.status_code == 200:
            return probe.json()

        if probe.status_code != 402:
            print(f"[!] Unexpected error: {probe.status_code} - {probe.text}")
            return {}

        challenge = probe.json()
        accepts = challenge.get("accepts", [{}])[0]
        pay_to = accepts.get("payTo")
        amount = int(accepts.get("maxAmountRequired", "50000"))
        token_address = accepts.get("asset", "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913")

        valid_after = int(time.time()) - 60
        valid_before = int(time.time()) + 300
        nonce = "0x" + secrets.token_hex(32)

        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "TransferWithAuthorization": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validBefore", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                ],
            },
            "primaryType": "TransferWithAuthorization",
            "domain": {
                "name": "USD Coin",
                "version": "2",
                "chainId": 8453,
                "verifyingContract": Web3.to_checksum_address(token_address),
            },
            "message": {
                "from": Web3.to_checksum_address(trader_account.address),
                "to": Web3.to_checksum_address(pay_to),
                "value": amount,
                "validAfter": valid_after,
                "validBefore": valid_before,
                "nonce": nonce,
            },
        }

        signable = encode_typed_data(full_message=typed_data)
        signed = trader_account.sign_message(signable)
        sig_hex = signed.signature.hex()
        if not sig_hex.startswith("0x"):
            sig_hex = "0x" + sig_hex

        pay_header = {
            "x402Version": 1,
            "scheme": "exact",
            "network": "base",
            "payload": {
                "signature": sig_hex,
                "authorization": {
                    "from": Web3.to_checksum_address(trader_account.address),
                    "to": Web3.to_checksum_address(pay_to),
                    "value": str(amount),
                    "validAfter": str(valid_after),
                    "validBefore": str(valid_before),
                    "nonce": nonce,
                }
            }
        }

        header_b64 = base64.b64encode(json.dumps(pay_header).encode()).decode()
        res = client.post(API_URL, json=payload, headers={"X-PAYMENT": header_b64})
        return res.json() if res.status_code == 200 else {}


def execute_strategy():
    print(f"\n--- Market Scan Triggered: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')} ---")
    for symbol in WATCHLIST:
        print(f"[*] Analyzing {symbol} via Institutional x402 API ($0.050 Base USDC)...")
        data = query_institutional_confluence(symbol)
        if not data or data.get("status") != "success":
            print(f"    [!] Failed to retrieve signal for {symbol}")
            continue

        price = data.get("current_price")
        bias = data.get("execution_bias")
        regime = data.get("confluence_regime")
        sl_dist = data.get("recommended_stop_loss_distance")
        vol_risk = data.get("volatility_risk")

        print(f"    Price: ${price:,.2f} | Bias: {bias} | Regime: {regime}")
        print(f"    Suggested SL Distance: ${sl_dist} | Volatility Risk: {vol_risk}")

        if bias == "LONG" and vol_risk == "NORMAL":
            print(f"    >>> SIGNAL: PLACE LIMIT BUY at ${price - (sl_dist * 0.5):,.2f} (SL: ${price - sl_dist:,.2f})")
        elif bias == "SHORT" and vol_risk == "NORMAL":
            print(f"    >>> SIGNAL: PLACE LIMIT SELL at ${price + (sl_dist * 0.5):,.2f} (SL: ${price + sl_dist:,.2f})")
        else:
            print("    >>> SIGNAL: NO TRADE (High Volatility or Inconclusive Confluence)")


if __name__ == "__main__":
    print("[*] Bot loop running. Polling market structure every 15 minutes...")
    while True:
        try:
            execute_strategy()
            time.sleep(POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            sys.exit(0)
        except Exception as e:
            print(f"[!] Error in main loop: {e}")
            time.sleep(30)
