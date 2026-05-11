# region imports
from AlgorithmImports import *
# endregion

import sys
import os
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from signal_generator import DedalSignalGenerator

TICKERS = [
    "SPY", "QQQ", "GLD",
    "MSFT", "AAPL", "META", "GOOGL", "NVDA",
    "JPM",
    "MCD", "KO", "PG",
    "JNJ",
    "XOM", "CAT", "AMT", "LMT"
]

# FB is the historical symbol for META — QC maps FB→META automatically,
# but if META ends up with too few trades we track FB separately and merge.
_META_ALIASES = {"META", "FB"}


class HermesCPSSignal(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2015, 1, 1)
        self.set_end_date(2025, 1, 1)
        self.set_cash(100_000)
        # 300 daily bars ≈ 60 weekly bars for indicator warmup
        self.set_warm_up(300, Resolution.DAILY)
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.MARGIN)

        self.signals = {}
        self.trade_stats = {}

        # Symbols to subscribe — add FB alongside META so pre-rename history is captured
        subscribe = list(TICKERS) + ["FB"]

        for ticker in subscribe:
            equity = self.add_equity(ticker, Resolution.DAILY)
            equity.set_data_normalization_mode(DataNormalizationMode.ADJUSTED)
            self.consolidate(ticker, timedelta(days=7), self.on_weekly_bar)

            self.signals[ticker] = DedalSignalGenerator(ticker)
            # FB trades will be stored under "META" key
            stats_key = "META" if ticker == "FB" else ticker
            if stats_key not in self.trade_stats:
                self.trade_stats[stats_key] = {
                    "trades": [],
                    "open_bar": None,
                    "entry_price": None,
                    "entry_date": None,
                    "bar_count": 0,
                }

    def _record_exit(self, stats_key, close, note=""):
        stats = self.trade_stats[stats_key]
        if stats["entry_price"] is None:
            return
        entry_price = stats["entry_price"]
        pnl_pct = (close - entry_price) / entry_price * 100.0
        bars_held = stats["bar_count"] - stats["open_bar"]
        stats["trades"].append({
            "entry_date": stats["entry_date"],
            "exit_date": self.time,
            "entry_price": entry_price,
            "exit_price": close,
            "pnl_pct": pnl_pct,
            "bars_held": bars_held,
            "win": pnl_pct > 0,
        })
        stats["entry_price"] = None
        stats["open_bar"] = None
        stats["entry_date"] = None

    def on_weekly_bar(self, bar):
        ticker = bar.symbol.value
        if ticker not in self.signals:
            return

        # FB and META share trade_stats under "META"
        stats_key = "META" if ticker == "FB" else ticker

        try:
            close = float(bar.close)
            if close <= 0:
                return

            self.signals[ticker].update(bar)

            if self.is_warming_up:
                return

            stats = self.trade_stats[stats_key]
            stats["bar_count"] += 1

            signal = self.signals[ticker].get_signal()
            invested = self.portfolio[ticker].invested

            if signal == "LONG" and not invested:
                portfolio_value = float(self.portfolio.total_portfolio_value)
                quantity = int((portfolio_value * 0.10) / close)
                if quantity > 0:
                    self.market_order(ticker, quantity)
                    stats["entry_price"] = close
                    stats["open_bar"] = stats["bar_count"]
                    stats["entry_date"] = self.time

            elif signal == "EXIT" and invested:
                self.liquidate(ticker)
                self._record_exit(stats_key, close)

        except Exception as e:
            self.debug(f"Error processing {ticker}: {str(e)}")

    def on_data(self, data: Slice):
        pass  # All logic is driven by on_weekly_bar consolidator callback

    def on_end_of_algorithm(self):
        # Close any positions that are still open and record as closed trades
        all_symbols = list(TICKERS) + ["FB"]
        for ticker in all_symbols:
            try:
                if self.portfolio[ticker].invested:
                    exit_price = float(self.securities[ticker].price)
                    stats_key = "META" if ticker == "FB" else ticker
                    if exit_price > 0:
                        self._record_exit(stats_key, exit_price)
                    self.liquidate(ticker)
            except Exception:
                pass

        self.log("=== DEDAL SIGNAL STATS ===")
        self.log("TICKER | TRADES | WIN_RATE | AVG_BARS | PF | AVG_WIN% | AVG_LOSS% | TOTAL_PNL%")

        total_trades = 0
        weighted_win_rate = 0.0
        strong_tickers = []
        weak_tickers = []

        for ticker in TICKERS:
            trades = self.trade_stats[ticker]["trades"]
            n_trades = len(trades)

            if n_trades == 0:
                self.log(f"{ticker} | 0 | N/A | N/A | N/A | N/A | N/A | N/A")
                weak_tickers.append(ticker)
                continue

            wins = [t for t in trades if t["win"]]
            losses = [t for t in trades if not t["win"]]

            win_rate = len(wins) / n_trades * 100.0
            avg_bars = sum(t["bars_held"] for t in trades) / n_trades
            avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0.0
            avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0.0
            total_pnl = sum(t["pnl_pct"] for t in trades)

            gross_profit = sum(t["pnl_pct"] for t in wins)
            gross_loss = abs(sum(t["pnl_pct"] for t in losses))
            if gross_loss == 0:
                pf_str = "inf"
                pf_val = float("inf")
            else:
                pf_val = gross_profit / gross_loss
                pf_str = f"{pf_val:.2f}"

            self.log(
                f"{ticker} | {n_trades} | {win_rate:.1f}% | {avg_bars:.1f} | "
                f"{pf_str} | {avg_win:.1f}% | {avg_loss:.1f}% | {total_pnl:.1f}%"
            )

            total_trades += n_trades
            weighted_win_rate += win_rate * n_trades

            if pf_val >= 1.5 and n_trades >= 15:
                strong_tickers.append(ticker)
            else:
                weak_tickers.append(ticker)

        self.log("=== SUMMARY ===")
        self.log(f"Total trades: {total_trades}")

        if total_trades > 0:
            self.log(f"Weighted avg win rate: {weighted_win_rate / total_trades:.1f}%")
        else:
            self.log("Weighted avg win rate: N/A")

        self.log(
            f"Strong tickers (PF >= 1.5 AND trades >= 15): "
            f"{', '.join(strong_tickers) if strong_tickers else 'none'}"
        )
        self.log(
            f"Weak tickers (PF < 1.5 OR trades < 15): "
            f"{', '.join(weak_tickers) if weak_tickers else 'none'}"
        )

        # Yearly P&L summary
        self.log("=== YEARLY P&L SUMMARY ===")
        self.log("YEAR | TRADES | TOTAL_PNL%")

        yearly = {}
        for ticker in TICKERS:
            for trade in self.trade_stats[ticker]["trades"]:
                year = trade["exit_date"].year
                if year not in yearly:
                    yearly[year] = {"n": 0, "pnl": 0.0}
                yearly[year]["n"] += 1
                yearly[year]["pnl"] += trade["pnl_pct"]

        for year in sorted(yearly):
            self.log(f"{year} | {yearly[year]['n']} | {yearly[year]['pnl']:.1f}%")
