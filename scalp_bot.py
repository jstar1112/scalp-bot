# scalp_bot.py - BITGET ONLY, NO KEYS - REAL SCALPS ONLY
import ccxt
import time
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import asyncio
import logging
import hashlib
import os
from dotenv import load_dotenv

load_dotenv()  # reads .env

# ------------------- CONFIG -------------------
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# Public Bitget (no keys!)
bitget = ccxt.bitget({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# Scan settings
SCAN_INTERVAL = 180          # 3 min
MIN_CHANGE_PCT = 6.0          # 5-min pump
VOLUME_MULT = 3.0            # volume spike
MIN_24H_VOLUME = 1_000_000   # $1M+
SCORE_THRESHOLD = 70         # 0-100

# ------------------------------------------------
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)

price_cache = {}
volume_cache = {}
alerted = set()


def get_bitget_url(symbol):
    base, quote = symbol.split('/')
    base = base.replace('3S', '').replace('3L', '')
    return f"https://www.bitget.com/spot/{base}{quote}_SPBL"


async def send_alert(symbol, change, vol_now, vol_avg, score, price):
    key_str = f"BITGET{symbol}{time.strftime('%Y%m%d')}"
    key = hashlib.md5(key_str.encode()).hexdigest()
    if key in alerted:
        return
    alerted.add(key)
    if len(alerted) > 500:
        alerted.clear()

    url = get_bitget_url(symbol)
    keyboard = [[InlineKeyboardButton("Open on Bitget", url=url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"*BITGET SCALP CANDIDATE*\n"
        f"`{symbol}`\n"
        f"Change (5m): **+{change:.2f}%**\n"
        f"Vol now: **${vol_now/1e6:.2f}M**\n"
        f"Vol avg (1h): **${vol_avg/1e6:.2f}M** (×{vol_now/vol_avg:.1f})\n"
        f"Price: **${price:.6f}**\n"
        f"Score: **{score}/100**"
    )

    await bot.send_message(
        chat_id=CHAT_ID,
        text=text,
        parse_mode='Markdown',
        reply_markup=reply_markup,
        disable_web_page_preview=True
    )


def update_cache(symbol, price, volume, now):
    # Volume: last 60 min
    if symbol not in volume_cache:
        volume_cache[symbol] = []
    volume_cache[symbol].append((now, volume))
    volume_cache[symbol] = [v for v in volume_cache[symbol] if now - v[0] < 3600]

    # Price: last 5 min
    if symbol not in price_cache:
        price_cache[symbol] = []
    price_cache[symbol].append((now, price))
    price_cache[symbol] = [p for p in price_cache[symbol] if now - p[0] < 300]


def calc_metrics(symbol, now):
    if symbol not in price_cache or len(price_cache[symbol]) < 2:
        return None
    old_price, new_price = price_cache[symbol][0][1], price_cache[symbol][-1][1]

    if old_price == 0:
        return None

    change_pct = (new_price - old_price) / old_price * 100
    if change_pct < MIN_CHANGE_PCT:
        return None

    vols = volume_cache.get(symbol, [])
    if len(vols) < 2:
        return None
    vol_avg = sum(v[1] for v in vols) / len(vols)
    vol_now = vols[-1][1]
    if vol_now < vol_avg * VOLUME_MULT:
        return None

    try:
        ticker = bitget.fetch_ticker(symbol)
    except Exception:
        return None
    if ticker.get('quoteVolume', 0) < MIN_24H_VOLUME:
        return None

    score = 0
    score += min(change_pct / 15 * 40, 40)
    score += min((vol_now / vol_avg - 1) / 5 * 30, 30)
    score += min(ticker['quoteVolume'] / 5e6 * 30, 30)
    score = int(score)

    return {
        'change': change_pct,
        'vol_now': vol_now,
        'vol_avg': vol_avg,
        'price': new_price,
        'score': score
    }


async def scan_bitget():
    try:
        markets = bitget.load_markets()
        symbols = [s for s in markets.keys() if s.endswith('/USDT') and markets[s].get('spot')]
        ohlcv_dict = {}

        for symbol in symbols:
            try:
                ohlcv = bitget.fetch_ohlcv(symbol, '5m', limit=2)
                if len(ohlcv) >= 2:
                    ohlcv_dict[symbol] = ohlcv
            except Exception:
                continue

    except Exception as e:
        logging.error(f"Bitget error: {e}")
        return

    now = time.time()
    for symbol, ohlcv in ohlcv_dict.items():
        if len(ohlcv) < 2:
            continue
        _, _, _, _, volume, close = ohlcv[-1]
        update_cache(symbol, close, volume, now)
        metrics = calc_metrics(symbol, now)
        if metrics and metrics['score'] >= SCORE_THRESHOLD:
            await send_alert(symbol, **metrics)


async def main_loop():
    while True:
        start = time.time()
        await scan_bitget()
        elapsed = time.time() - start
        wait = max(0, SCAN_INTERVAL - elapsed)
        logging.info(f"Scan done in {elapsed:.1f}s – sleeping {wait:.0f}s")
        await asyncio.sleep(wait)


if __name__ == "__main__":
    asyncio.run(main_loop())

# Force rebuild - delete after