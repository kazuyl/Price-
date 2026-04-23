import os
import time
import requests
import yfinance as yf

SERVER_URL = os.environ.get(
    "SERVER_URL",
    "https://bot-v2-production.up.railway.app/price_update"
)
SYMBOL = os.environ.get("SYMBOL", "NQ=F")
INTERVAL = int(os.environ.get("INTERVAL", "15"))
TIMEOUT = int(os.environ.get("TIMEOUT", "15"))

def get_latest_price(symbol: str):
    try:
        df = yf.download(
            symbol,
            period="1d",
            interval="1m",
            progress=False,
            auto_adjust=False,
        )

        if df is None or df.empty:
            return None

        return float(df["Close"].dropna().iloc[-1])

    except Exception as e:
        print(f"[PRICE ERROR] {e}")
        return None

def send_price(price: float):
    try:
        r = requests.post(SERVER_URL, json={"price": price}, timeout=TIMEOUT)
        print(f"[POST] price={price} status={r.status_code}")
    except Exception as e:
        print(f"[POST ERROR] {e}")

def main():
    print("=== AUTO PRICE FEED STARTED ===")

    last_price = None

    while True:
        price = get_latest_price(SYMBOL)

        if price is not None:
            send_price(price)
            last_price = price

        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
