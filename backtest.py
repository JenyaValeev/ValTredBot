# backtest.py
import asyncio
import argparse
import yaml
import os
from datetime import datetime
import pandas as pd
import numpy as np

from exchange import create_exchange, fetch_ohlcv
from strategy import Strategy
from db import init_db, create_run, log_trade
from utils import calculate_drawdown, calculate_sharpe, cagr

async def run_backtest(symbol: str, timeframe: str, candles: int, cfg: dict):
    exchange = create_exchange(testnet=False, default_type=cfg.get("default_market_type","swap"))
    df = await fetch_ohlcv(exchange, symbol, timeframe, limit=candles)
    if df.empty:
        print("No data for", symbol)
        return

    strat = Strategy(cfg.get("strategy", {}), param_getter=lambda k, d=None: cfg.get("risk", {}).get(k.lower(), d))
    df = strat.compute_indicators(df)
    # warm up
    warm = max(strat.map["ema_slow"], strat.map["rsi_len"], strat.map["atr_len"]) + 5

    balance = 10000.0
    pos = None
    eq_curve = []
    run_id = create_run(f"backtest {symbol} {timeframe} {datetime.utcnow().isoformat()}")
    commission = 0.00075
    slippage = 0.0005

    for i in range(warm, len(df)):
        window = df.iloc[:i+1]
        sig = strat.generate_signal(window, equity_usdt=balance)
        price = float(df.iloc[i]["close"])
        if pos:
            # manage pos: trailing stop / partial tp (simplified)
            atr = float(df.iloc[i]["atr"])
            # trailing
            if pos["side"] == "long":
                trail_stop = max(pos["stop_px"], price - atr*strat.map["atr_mult_trail"])
                if price <= trail_stop:
                    # exit
                    exit_price = price*(1 - slippage)
                    pnl = (exit_price - pos["entry_px"])*pos["qty"] - exit_price*pos["qty"]*commission
                    balance += pos["qty"]*exit_price
                    log_trade(run_id, symbol, pos["side"], "close", pos["qty"], exit_price, pos["qty"]*exit_price, pnl, "bt_exit")
                    pos = None
                elif price >= pos["tp_px"]:
                    # partial tp
                    close_qty = pos["qty"] * strat.map["partial_tp_ratio"]
                    exit_price = price*(1 - slippage)
                    pnl = (exit_price - pos["entry_px"])*close_qty - exit_price*close_qty*commission
                    balance += close_qty*exit_price
                    pos["qty"] -= close_qty
                    log_trade(run_id, symbol, pos["side"], "partial_close", close_qty, exit_price, close_qty*exit_price, pnl, "bt_partial_tp")
            else:
                trail_stop = min(pos["stop_px"], price + atr*strat.map["atr_mult_trail"])
                if price >= trail_stop:
                    exit_price = price*(1 + slippage)
                    pnl = (pos["entry_px"] - exit_price)*pos["qty"] - exit_price*pos["qty"]*commission
                    balance += pos["qty"]* (2*pos["entry_px"] - exit_price)  # approximate for short
                    log_trade(run_id, symbol, pos["side"], "close", pos["qty"], exit_price, pos["qty"]*exit_price, pnl, "bt_exit")
                    pos = None
                elif price <= pos["tp_px"]:
                    close_qty = pos["qty"] * strat.map["partial_tp_ratio"]
                    exit_price = price*(1 + slippage)
                    pnl = (pos["entry_px"] - exit_price)*close_qty - exit_price*close_qty*commission
                    balance += close_qty * (2*pos["entry_px"] - exit_price)
                    pos["qty"] -= close_qty
                    log_trade(run_id, symbol, pos["side"], "partial_close", close_qty, exit_price, close_qty*exit_price, pnl, "bt_partial_tp")

        if not pos and sig.side != "hold":
            usdt = sig.info["usdt_size"]
            if usdt > balance:
                usdt = balance
            qty = usdt / price
            entry_price = price*(1 + slippage if sig.side=="long" else 1 - slippage)
            entry_cost = qty*entry_price*(1 + commission)
            if entry_cost > balance:
                qty = balance / (entry_price*(1+commission))
                entry_cost = balance
            log_trade(run_id, symbol, sig.side, "open", qty, entry_price, entry_cost, None, "bt_entry")
            balance -= entry_cost
            pos = {"side": sig.side, "qty": qty, "entry_px": entry_price, "stop_px": sig.stop_price, "tp_px": sig.tp_price}

        # equity
        equity = balance
        if pos:
            price_now = price
            if pos["side"] == "long":
                equity += pos["qty"]*price_now
            else:
                equity += pos["qty"]*(2*pos["entry_px"] - price_now)
        eq_curve.append(equity)

    if pos:
        # close at last price
        last_price = float(df.iloc[-1]["close"])
        exit_price = last_price
        if pos["side"] == "long":
            pnl = (exit_price - pos["entry_px"])*pos["qty"] - exit_price*pos["qty"]*commission
            balance += pos["qty"]*exit_price
        else:
            pnl = (pos["entry_px"] - exit_price)*pos["qty"] - exit_price*pos["qty"]*commission
            balance += pos["qty"]*(2*pos["entry_px"] - exit_price)
        log_trade(run_id, symbol, pos["side"], "close", pos["qty"], exit_price, pos["qty"]*exit_price, pnl, "bt_final")

    curve = np.array(eq_curve) if eq_curve else np.array([10000.0, balance])
    ret_pct = (curve[-1]/curve[0]-1)*100 if curve[0]>0 else 0.0
    dd = calculate_drawdown(curve)*100
    sh = calculate_sharpe(curve)
    days = (df.index[-1] - df.index[0]).days or 1
    annual_cagr = cagr(curve[0], curve[-1], days)

    print("Backtest result:")
    print("Initial balance:", curve[0])
    print("Final balance:", curve[-1])
    print("Return %:", ret_pct)
    print("Max drawdown %:", dd)
    print("Sharpe (annualized):", sh)
    print("CAGR:", annual_cagr)
    # save curve
    out = {"ts": df.index[warm:warm+len(curve)], "equity": curve}
    import pandas as pd
    pd.DataFrame(out).to_csv(f"equity_{symbol.replace('/','')}_{timeframe}.csv", index=False)
    print("Equity curve saved.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("timeframe")
    parser.add_argument("--candles", type=int, default=2000)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    cfg = {}
    if os.path.exists(args.config):
        with open(args.config, "r") as f:
            cfg = yaml.safe_load(f)
    asyncio.run(run_backtest(args.symbol.upper(), args.timeframe, args.candles, cfg))
