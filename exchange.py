# exchange.py
import ccxt
import os
import asyncio
import logging
from typing import Any, Optional

log = logging.getLogger("bybit_bot.exchange")

def create_exchange(api_key: str = "", api_secret: str = "", testnet: bool = True, default_type: str = "swap"):
    params = {
        "apiKey": api_key or "",
        "secret": api_secret or "",
        "enableRateLimit": True,
        "options": {"defaultType": default_type}
    }
    exchange = ccxt.bybit(params)
    if str(testnet).lower() in ("1", "true", "yes"):
        try:
            exchange.set_sandbox_mode(True)
            log.info("Bybit sandbox mode enabled.")
        except Exception:
            log.warning("Cannot set sandbox mode on this ccxt/bybit version.")
    return exchange

async def fetch_ohlcv(exchange: ccxt.Exchange, symbol: str, timeframe: str, limit: int = 500):
    try:
        data = await asyncio.to_thread(exchange.fetch_ohlcv, symbol, timeframe=timeframe, limit=limit)
        import pandas as pd
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("ts", inplace=True)
        return df
    except Exception as e:
        log.exception("fetch_ohlcv error %s: %s", symbol, e)
        import pandas as pd
        return pd.DataFrame()

async def fetch_ticker(exchange: ccxt.Exchange, symbol: str) -> dict:
    try:
        return await asyncio.to_thread(exchange.fetch_ticker, symbol)
    except Exception as e:
        log.exception("fetch_ticker error %s: %s", symbol, e)
        return {}

async def market_price(exchange: ccxt.Exchange, symbol: str) -> float:
    tick = await fetch_ticker(exchange, symbol)
    return float(tick.get("last") or tick.get("close") or 0.0)

def lot_round(exchange: ccxt.Exchange, symbol: str, qty: float) -> float:
    try:
        market = exchange.market(symbol)
        step = market.get("precision", {}).get("amount")
        if step is None:
            step = market.get("limits", {}).get("amount", {}).get("min", 0.000001)
        step = float(step or 0.000001)
        # floor to step
        return (qty // step) * step
    except Exception:
        return float(f"{qty:.6f}")
