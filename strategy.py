# strategy.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any
import pandas as pd
import numpy as np

from ta.trend import EMAIndicator, ADXIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

@dataclass
class Signal:
    side: str
    entry_price: float
    stop_price: float
    tp_price: float
    stop_dist: float
    info: Dict[str, Any]

class Strategy:
    def __init__(self, params: Dict[str,Any], param_getter=None):
        self.params = params.copy()
        self.get = param_getter or (lambda k, default=None: self.params.get(k, default))

        # internal map with defaults
        def g(k, d):
            return self.get(k.upper(), self.params.get(k, d))
        self.map = {
            "ema_fast": int(g("ema_fast", 20)),
            "ema_slow": int(g("ema_slow", 50)),
            "ema_trend": int(g("ema_trend", 200)),
            "rsi_len": int(g("rsi_len", 14)),
            "rsi_entry_long": float(g("rsi_entry_long", 35)),
            "rsi_entry_short": float(g("rsi_entry_short", 65)),
            "adx_len": int(g("adx_len", 14)),
            "adx_threshold": float(g("adx_threshold", 20)),
            "atr_len": int(g("atr_len", 14)),
            "atr_mult_stop": float(g("atr_mult_stop", 1.5)),
            "atr_mult_trail": float(g("atr_mult_trail", 2.0)),
            "vol_len": int(g("vol_len", 20)),
            "vol_mult": float(g("vol_mult", 1.0)),
            "partial_tp_ratio": float(g("partial_tp_ratio", 0.5)),
            "tp_rr": float(g("tp_rr", 1.0)),
        }

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema_fast"] = EMAIndicator(df["close"], window=self.map["ema_fast"], fillna=True).ema_indicator()
        df["ema_slow"] = EMAIndicator(df["close"], window=self.map["ema_slow"], fillna=True).ema_indicator()
        df["ema_trend"] = EMAIndicator(df["close"], window=self.map["ema_trend"], fillna=True).ema_indicator()
        df["rsi"] = RSIIndicator(df["close"], window=self.map["rsi_len"], fillna=True).rsi()
        adx_obj = ADXIndicator(df["high"], df["low"], df["close"], window=self.map["adx_len"], fillna=True)
        df["adx"] = adx_obj.adx()
        atr_obj = AverageTrueRange(df["high"], df["low"], df["close"], window=self.map["atr_len"], fillna=True)
        df["atr"] = atr_obj.average_true_range()
        df["vol_sma"] = df["volume"].rolling(self.map["vol_len"]).mean().fillna(0)
        return df

    def size_from_risk(self, equity_usdt: float, price: float, stop_dist: float, risk_pct: float, min_usdt: float) -> float:
        if price <= 0 or stop_dist <= 0:
            return min_usdt
        stop_pct = stop_dist / price
        if stop_pct <= 0:
            return min_usdt
        usdt_at_risk = equity_usdt * risk_pct
        position_usdt = usdt_at_risk / stop_pct
        return max(position_usdt, min_usdt)

    def generate_signal(self, df: pd.DataFrame, equity_usdt: float = 10000.0) -> Signal:
        row = df.iloc[-1]
        price = float(row["close"])
        ema_fast = float(row["ema_fast"])
        ema_slow = float(row["ema_slow"])
        ema_trend = float(row["ema_trend"])
        rsi = float(row["rsi"])
        adx = float(row["adx"])
        atr = float(row["atr"])
        vol = float(row["volume"])
        vol_sma = float(row["vol_sma"])

        # ADX filter
        if adx < self.map["adx_threshold"]:
            return Signal("hold", price, 0.0, 0.0, 0.0, {"reason":"low_adx", "adx": adx})

        # volume filter
        if vol_sma > 0 and vol < vol_sma * self.map["vol_mult"]:
            return Signal("hold", price, 0.0, 0.0, 0.0, {"reason":"low_vol", "vol":vol, "vol_sma":vol_sma})

        # trend
        trend_dir = "up" if price > ema_trend else ("down" if price < ema_trend else "flat")

        # long
        if trend_dir == "up" and ema_fast > ema_slow and rsi > self.map["rsi_entry_long"]:
            stop = price - atr * self.map["atr_mult_stop"]
            stop_dist = price - stop
            tp = price + stop_dist * self.map["tp_rr"]
            side = "long"
            info = {"reason": "trend+ema+rsi", "adx": adx, "atr": atr}
        # short
        elif trend_dir == "down" and ema_fast < ema_slow and rsi < self.map["rsi_entry_short"]:
            stop = price + atr * self.map["atr_mult_stop"]
            stop_dist = stop - price
            tp = price - stop_dist * self.map["tp_rr"]
            side = "short"
            info = {"reason": "trend+ema+rsi", "adx": adx, "atr": atr}
        else:
            return Signal("hold", price, 0.0, 0.0, 0.0, {"reason":"no_setup", "rsi": rsi})

        if stop <= 0 or stop_dist <= 0:
            return Signal("hold", price, 0.0, 0.0, 0.0, {"reason":"bad_stop"})

        equity = float(equity_usdt)
        risk_pct = float(self.get("MAX_RISK_PER_TRADE", 0.01))
        min_usdt = float(self.get("MIN_ORDER_USDT", 10.0))
        usdt_size = self.size_from_risk(equity, price, stop_dist, risk_pct, min_usdt)

        info.update({
            "usdt_size": usdt_size,
            "stop_pct": stop_dist / price,
            "atr": atr,
            "vol_sma": vol_sma
        })

        return Signal(side=side, entry_price=price, stop_price=stop, tp_price=tp, stop_dist=stop_dist, info=info)
