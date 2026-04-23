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

        last_close = float(df["Close"].dropna().iloc[-1])
        return last_close

    except Exception as e:
        print(f"[PRICE ERROR] {e}")
        return None


def send_price(price: float):
    payload = {"price": price}

    try:
        r = requests.post(SERVER_URL, json=payload, timeout=TIMEOUT)
        print(f"[POST] price={price} status={r.status_code} response={r.text}")
    except Exception as e:
        print(f"[POST ERROR] {e}")


def main():
    print("=== AUTO PRICE FEED STARTED ===")
    print(f"SERVER_URL: {SERVER_URL}")
    print(f"SYMBOL: {SYMBOL}")
    print(f"INTERVAL: {INTERVAL}s")

    last_sent_price = None

    while True:
        price = get_latest_price(SYMBOL)

        if price is not None:
            if last_sent_price is None or price != last_sent_price:
                send_price(price)
                last_sent_price = price
            else:
                print(f"[SKIP] unchanged price={price}")
        else:
            print("[WARN] no price fetched")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
