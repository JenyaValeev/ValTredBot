# exec_layer.py
import asyncio
import logging
from typing import Tuple, Optional
from db import log_trade

log = logging.getLogger("bybit_bot.exec")

class ExecLayer:
    def __init__(self, exchange, mode: str, run_id: Optional[int], hedge_mode: bool=False):
        self.exchange = exchange
        self.mode = mode  # live | paper | backtest
        self.run_id = run_id
        self.hedge_mode = hedge_mode

    async def open(self, symbol: str, side: str, usdt_value: float) -> Tuple[bool,str,float,float]:
        px = await asyncio.to_thread(self.exchange.fetch_ticker, symbol)
        price = float(px.get("last") or px.get("close") or 0.0)
        if price <= 0:
            return False, "bad price", 0.0, 0.0
        qty = usdt_value / price
        # round qty to market lot
        try:
            market = self.exchange.market(symbol)
            step = market.get("precision", {}).get("amount", 0.000001)
            if step:
                qty = (qty // step) * step
        except Exception:
            qty = float(f"{qty:.6f}")

        if qty <= 0:
            return False, "amount too small", 0.0, price

        info = "paper"
        if self.mode == "live":
            side_api = "buy" if side=="long" else "sell"
            params = {"reduceOnly": False}
            order = await asyncio.to_thread(self.exchange.create_market_order, symbol, side_api, qty, None, params)
            info = str(order)

        log_trade(self.run_id, symbol, side, "open", qty, price, qty*price, None, info)
        return True, "ok", qty, price

    async def close(self, symbol: str, side: str, qty: float) -> Tuple[bool,str,float,float]:
        px = await asyncio.to_thread(self.exchange.fetch_ticker, symbol)
        price = float(px.get("last") or px.get("close") or 0.0)
        info = "paper"
        if self.mode == "live":
            side_api = "sell" if side=="long" else "buy"
            params = {"reduceOnly": True}
            order = await asyncio.to_thread(self.exchange.create_market_order, symbol, side_api, qty, None, params)
            info = str(order)
        log_trade(self.run_id, symbol, side, "close", qty, price, qty*price, None, info)
        return True, "ok", qty, price

    def partial_close(self, symbol: str, side: str, qty: float, px: float):
        log_trade(self.run_id, symbol, side, "partial_close", qty, px, qty*px, None, "partial")
