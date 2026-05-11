# region imports
from AlgorithmImports import *
# endregion

import sys
import os
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from signal_generator import DedalSignalGenerator


class HermesCPSSignal(QCAlgorithm):

    # ------------------------------------------------------------------ #
    # Initialize                                                           #
    # ------------------------------------------------------------------ #

    def initialize(self):
        self.set_start_date(2020, 1, 1)
        self.set_end_date(2022, 1, 1)
        self.set_cash(100_000)
        self.set_warm_up(300, Resolution.DAILY)
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.MARGIN)

        self.signals = {}
        self.trade_stats = {}
        self.option_symbols = {}          # ticker → canonical option Symbol
        self.open_options = {}            # ticker → open CPS position data
        self.pending_open = {}            # ticker → True when LONG signal awaits execution
        self.pending_signal_price = {}    # ticker → close price at signal time
        self._current_data = None

        # FB subscribed only in equity mode for extended META pre-rename history
        subscribe = list(config.TICKERS)
        if config.INSTRUMENT == "equity" and "META" in config.TICKERS:
            subscribe.append("FB")

        for ticker in subscribe:
            equity = self.add_equity(ticker, Resolution.DAILY)
            equity.set_data_normalization_mode(DataNormalizationMode.ADJUSTED)

            if config.INSTRUMENT == "options" and ticker in config.TICKERS:
                option = self.add_option(ticker, Resolution.DAILY)
                option.set_filter(-20, 0, timedelta(10), timedelta(60))
                self.option_symbols[ticker] = option.symbol
                self.open_options[ticker] = self._empty_options_pos()
                self.pending_open[ticker] = False
                self.pending_signal_price[ticker] = 0.0

            self.consolidate(ticker, timedelta(days=7), self.on_weekly_bar)
            self.signals[ticker] = DedalSignalGenerator(ticker)

            stats_key = "META" if ticker == "FB" else ticker
            if stats_key not in self.trade_stats:
                self.trade_stats[stats_key] = {
                    "trades": [],
                    "open_bar": None,
                    "entry_price": None,
                    "entry_date": None,
                    "bar_count": 0,
                }

    def _empty_options_pos(self):
        return {
            "contract": None, "expiry": None,
            "short_symbol": None, "long_symbol": None,
            "premium_collected": None, "max_profit": None, "max_loss": None,
            "entry_date": None, "entry_dte": None,
            "signal_price": None, "strike_short": None, "strike_long": None,
            "n_spreads": None, "actual_spread_width": None,
        }

    # ------------------------------------------------------------------ #
    # Weekly signal generation (both modes)                               #
    # ------------------------------------------------------------------ #

    def on_weekly_bar(self, bar):
        ticker = bar.symbol.value
        if ticker not in self.signals:
            return

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

            if config.INSTRUMENT == "equity":
                self._handle_equity_signal(ticker, stats_key, close, signal)
            elif config.INSTRUMENT == "options" and ticker in config.TICKERS:
                self._handle_options_signal(ticker, close, signal)

        except Exception as e:
            self.debug(f"[{ticker}] on_weekly_bar: {e}")

    def _handle_equity_signal(self, ticker, stats_key, close, signal):
        stats = self.trade_stats[stats_key]
        invested = self.portfolio[ticker].invested

        if signal == "LONG" and not invested:
            qty = int(
                (float(self.portfolio.total_portfolio_value) * config.POSITION_SIZE_PCT) / close
            )
            if qty > 0:
                self.market_order(ticker, qty)
                stats["entry_price"] = close
                stats["open_bar"] = stats["bar_count"]
                stats["entry_date"] = self.time

        elif signal == "EXIT" and invested:
            self.liquidate(ticker)
            self._record_equity_exit(stats_key, close)

    def _handle_options_signal(self, ticker, close, signal):
        has_open = self.open_options[ticker]["contract"] is not None

        if signal == "LONG" and not has_open and not self.pending_open[ticker]:
            self.pending_open[ticker] = True
            self.pending_signal_price[ticker] = close
        elif signal == "EXIT":
            if self.pending_open[ticker]:
                self.pending_open[ticker] = False

    # ------------------------------------------------------------------ #
    # Daily on_data — options management only                             #
    # ------------------------------------------------------------------ #

    def on_data(self, data: Slice):
        if config.INSTRUMENT != "options" or self.is_warming_up:
            return

        self._current_data = data

        for ticker in config.TICKERS:
            try:
                if self.pending_open.get(ticker, False):
                    if self._open_cps(ticker, self.pending_signal_price[ticker]):
                        self.pending_open[ticker] = False

                if self.open_options[ticker]["contract"] is not None:
                    self._manage_cps(ticker)

            except Exception as e:
                self.debug(f"[options/{ticker}] on_data: {e}")

    # ------------------------------------------------------------------ #
    # Open Credit Put Spread                                              #
    # ------------------------------------------------------------------ #

    def _open_cps(self, ticker, signal_price):
        if self._current_data is None:
            return False

        option_sym = self.option_symbols.get(ticker)
        if option_sym is None or option_sym not in self._current_data.option_chains:
            return False

        chain = self._current_data.option_chains[option_sym]
        puts = [c for c in chain if c.right == OptionRight.Put]
        if not puts:
            return False

        # Expiry closest to DTE_TARGET
        expiries = sorted(set(c.expiry for c in puts))
        best_expiry = min(expiries, key=lambda e: abs((e - self.time).days - config.DTE_TARGET))
        actual_dte = (best_expiry - self.time).days

        expiry_puts = [c for c in puts if c.expiry == best_expiry]
        strikes = sorted(set(c.strike for c in expiry_puts))

        # Short strike: largest <= signal_price, then back by STRIKE_OFFSET
        eligible = [s for s in strikes if s <= signal_price]
        if not eligible:
            self.debug(f"[{ticker}] No eligible short strike <= {signal_price:.2f}")
            return False

        short_idx = len(eligible) - 1 - config.STRIKE_OFFSET
        if short_idx < 0:
            self.debug(f"[{ticker}] STRIKE_OFFSET exceeds available strikes")
            return False

        short_strike = eligible[short_idx]

        # Long strike: closest available below short_strike to (short - SPREAD_WIDTH)
        below_short = [s for s in strikes if s < short_strike]
        if not below_short:
            self.debug(f"[{ticker}] No strikes below {short_strike}")
            return False

        long_strike = min(below_short, key=lambda s: abs(s - (short_strike - config.SPREAD_WIDTH)))

        def get_contract(strike):
            cs = [c for c in expiry_puts if c.strike == strike]
            return cs[0] if cs else None

        short_c = get_contract(short_strike)
        long_c = get_contract(long_strike)
        if short_c is None or long_c is None:
            self.debug(f"[{ticker}] Contracts missing for {short_strike}/{long_strike}")
            return False

        # Actual spread width (may differ from config.SPREAD_WIDTH if exact strike unavailable)
        actual_spread_width = short_strike - long_strike

        # Position sizing based on actual spread width
        spread_dollars = actual_spread_width * 100
        if spread_dollars <= 0:
            return False

        portfolio_value = float(self.portfolio.total_portfolio_value)
        max_exposure = portfolio_value * config.MAX_EXPOSURE_PCT
        current_exposure = self._get_current_exposure()

        if current_exposure >= max_exposure:
            self.debug(
                f"[{ticker}] Skipped: exposure ${current_exposure:.0f} "
                f">= limit ${max_exposure:.0f}"
            )
            return False

        n_spreads = int((portfolio_value * config.POSITION_SIZE_PCT) / spread_dollars)

        # Cap to what fits within remaining exposure budget
        remaining_exposure = max_exposure - current_exposure
        max_spreads_by_exposure = int(remaining_exposure / spread_dollars)
        n_spreads = min(n_spreads, max_spreads_by_exposure)

        if n_spreads < 1:
            self.debug(
                f"[{ticker}] Skipped: no room within exposure limit "
                f"(remaining ${remaining_exposure:.0f}, need ${spread_dollars:.0f}/spread)"
            )
            return False

        # Place via OptionStrategies.bull_put_spread (docs: snake_case in LEAN v2)
        bull_put = OptionStrategies.bull_put_spread(option_sym, short_strike, long_strike, best_expiry)
        self.buy(bull_put, n_spreads)

        def mid(c):
            b, a = float(c.bid_price), float(c.ask_price)
            if b > 0 and a > 0:
                return (b + a) / 2.0
            lp = float(c.last_price)
            return lp if lp > 0 else 0.0

        prem_per_spread = (mid(short_c) - mid(long_c)) * 100.0
        premium_collected = prem_per_spread * n_spreads
        max_loss = (spread_dollars - prem_per_spread) * n_spreads

        self.open_options[ticker] = {
            "contract":           option_sym,
            "expiry":             best_expiry,
            "short_symbol":       short_c.symbol,
            "long_symbol":        long_c.symbol,
            "premium_collected":  premium_collected,
            "max_profit":         premium_collected,
            "max_loss":           max_loss,
            "entry_date":         self.time,
            "entry_dte":          actual_dte,
            "signal_price":       signal_price,
            "strike_short":       short_strike,
            "strike_long":        long_strike,
            "n_spreads":          n_spreads,
            "actual_spread_width": actual_spread_width,
        }

        self.debug(
            f"[{ticker}] CPS opened: {short_strike}/{long_strike} "
            f"spread={actual_spread_width} (target={config.SPREAD_WIDTH}) "
            f"exp={best_expiry.date()} DTE={actual_dte} "
            f"prem=${premium_collected:.2f} x{n_spreads} "
            f"[exposure ${current_exposure + n_spreads * spread_dollars:.0f}/{max_exposure:.0f}]"
        )
        return True

    def _get_current_exposure(self):
        total = 0.0
        for t in config.TICKERS:
            pos = self.open_options.get(t, {})
            if pos.get("contract") is not None:
                n = pos.get("n_spreads") or 0
                sw = pos.get("actual_spread_width") or 0
                total += n * sw * 100
        return total

    # ------------------------------------------------------------------ #
    # Manage / close open CPS (daily)                                     #
    # ------------------------------------------------------------------ #

    def _manage_cps(self, ticker):
        pos = self.open_options[ticker]
        premium_collected = pos["premium_collected"] or 0.0
        days_to_expiry = (pos["expiry"] - self.time).days

        current_pnl = 0.0
        for leg in ("short_symbol", "long_symbol"):
            sym = pos.get(leg)
            if sym is not None:
                try:
                    current_pnl += float(self.portfolio[sym].unrealized_profit)
                except Exception:
                    pass

        pct_of_premium = (
            current_pnl / abs(premium_collected) * 100.0 if premium_collected != 0 else 0.0
        )

        close_reason = None
        if days_to_expiry <= 0:
            close_reason = "EXPIRY"
        elif days_to_expiry < config.CLOSE_DTE:
            close_reason = "DTE"
        elif config.TP_PCT > 0 and pct_of_premium >= config.TP_PCT:
            close_reason = "TP"
        elif config.SL_PCT > 0 and current_pnl <= -(abs(premium_collected) * config.SL_PCT / 100.0):
            close_reason = "SL"

        if close_reason:
            self._close_cps(ticker, close_reason)

    def _close_cps(self, ticker, close_reason):
        pos = self.open_options[ticker]
        premium_collected = pos["premium_collected"] or 0.0

        current_pnl = 0.0
        for leg in ("short_symbol", "long_symbol"):
            sym = pos.get(leg)
            if sym is not None:
                try:
                    current_pnl += float(self.portfolio[sym].unrealized_profit)
                    qty = int(self.portfolio[sym].quantity)
                    if qty != 0:
                        self.market_order(sym, -qty)
                except Exception:
                    pass

        pct_of_premium = (
            current_pnl / abs(premium_collected) * 100.0 if premium_collected != 0 else 0.0
        )
        self._record_cps_trade(ticker, close_reason, pct_of_premium)
        self.open_options[ticker] = self._empty_options_pos()

    # ------------------------------------------------------------------ #
    # Record trades                                                        #
    # ------------------------------------------------------------------ #

    def _record_equity_exit(self, stats_key, close):
        stats = self.trade_stats[stats_key]
        if stats["entry_price"] is None:
            return
        entry_price = stats["entry_price"]
        pnl_pct = (close - entry_price) / entry_price * 100.0
        bars_held = stats["bar_count"] - stats["open_bar"]
        stats["trades"].append({
            "entry_date":  stats["entry_date"],
            "exit_date":   self.time,
            "entry_price": entry_price,
            "exit_price":  close,
            "pnl_pct":     pnl_pct,
            "bars_held":   bars_held,
            "win":         pnl_pct > 0,
        })
        stats["entry_price"] = None
        stats["open_bar"] = None
        stats["entry_date"] = None

    def _record_cps_trade(self, ticker, close_reason, pct_of_premium):
        pos = self.open_options[ticker]
        self.trade_stats[ticker]["trades"].append({
            "entry_date":          pos["entry_date"],
            "exit_date":           self.time,
            "signal_price":        pos["signal_price"],
            "strike_short":        pos["strike_short"],
            "strike_long":         pos["strike_long"],
            "actual_spread_width": pos.get("actual_spread_width"),
            "premium_collected":   pos["premium_collected"],
            "max_profit":          pos["max_profit"],
            "max_loss":            pos["max_loss"],
            "pct_collected":       pct_of_premium,
            "close_reason":        close_reason,
            "win":                 pct_of_premium > 0,
        })

    # ------------------------------------------------------------------ #
    # End of algorithm                                                     #
    # ------------------------------------------------------------------ #

    def on_end_of_algorithm(self):
        if config.INSTRUMENT == "equity":
            all_syms = list(config.TICKERS) + (["FB"] if "META" in config.TICKERS else [])
            for ticker in all_syms:
                try:
                    if self.portfolio[ticker].invested:
                        price = float(self.securities[ticker].price)
                        stats_key = "META" if ticker == "FB" else ticker
                        if price > 0:
                            self._record_equity_exit(stats_key, price)
                        self.liquidate(ticker)
                except Exception:
                    pass

        if config.INSTRUMENT == "options":
            for ticker in config.TICKERS:
                if self.open_options.get(ticker, {}).get("contract") is not None:
                    self._close_cps(ticker, "EOA")

        if config.INSTRUMENT == "equity":
            self._log_equity_stats()
        else:
            self._log_cps_stats()

    # ------------------------------------------------------------------ #
    # Stats logging                                                        #
    # ------------------------------------------------------------------ #

    def _log_equity_stats(self):
        self.log("=== DEDAL SIGNAL STATS ===")
        self.log("TICKER | TRADES | WIN_RATE | AVG_BARS | PF | AVG_WIN% | AVG_LOSS% | TOTAL_PNL%")

        total_trades = 0
        weighted_win_rate = 0.0
        strong_tickers = []
        weak_tickers = []

        for ticker in config.TICKERS:
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
                pf_str, pf_val = "inf", float("inf")
            else:
                pf_val = gross_profit / gross_loss
                pf_str = f"{pf_val:.2f}"

            self.log(
                f"{ticker} | {n_trades} | {win_rate:.1f}% | {avg_bars:.1f} | "
                f"{pf_str} | {avg_win:.1f}% | {avg_loss:.1f}% | {total_pnl:.1f}%"
            )

            total_trades += n_trades
            weighted_win_rate += win_rate * n_trades
            (strong_tickers if pf_val >= 1.5 and n_trades >= 15 else weak_tickers).append(ticker)

        self.log("=== SUMMARY ===")
        self.log(f"Total trades: {total_trades}")
        if total_trades > 0:
            self.log(f"Weighted avg win rate: {weighted_win_rate / total_trades:.1f}%")
        self.log(f"Strong tickers (PF >= 1.5 AND trades >= 15): {', '.join(strong_tickers) or 'none'}")
        self.log(f"Weak tickers (PF < 1.5 OR trades < 15): {', '.join(weak_tickers) or 'none'}")
        self._log_yearly("pnl_pct")

    def _log_cps_stats(self):
        self.log("=== DEDAL CPS STATS ===")
        self.log(
            "TICKER | TRADES | WIN_RATE | AVG_PCT_COLLECTED | AVG_PREMIUM | "
            "PF | AVG_SPREAD | TP_closes | SL_closes | DTE_closes | EXPIRY_closes"
        )

        total_trades = 0
        total_premium = 0.0
        weighted_win_rate = 0.0
        strong_tickers = []
        weak_tickers = []

        for ticker in config.TICKERS:
            trades = self.trade_stats[ticker]["trades"]
            n_trades = len(trades)

            if n_trades == 0:
                self.log(f"{ticker} | 0 | N/A | N/A | N/A | N/A | N/A | 0 | 0 | 0 | 0")
                weak_tickers.append(ticker)
                continue

            wins = [t for t in trades if t["win"]]
            losses = [t for t in trades if not t["win"]]

            win_rate = len(wins) / n_trades * 100.0
            avg_pct = sum(t["pct_collected"] for t in trades) / n_trades
            avg_prem = sum(t.get("premium_collected") or 0 for t in trades) / n_trades
            avg_spread = sum(t.get("actual_spread_width") or 0 for t in trades) / n_trades
            gross_profit = sum(t["pct_collected"] for t in wins)
            gross_loss = abs(sum(t["pct_collected"] for t in losses))

            if gross_loss == 0:
                pf_str, pf_val = "inf", float("inf")
            else:
                pf_val = gross_profit / gross_loss
                pf_str = f"{pf_val:.2f}"

            tp_c  = sum(1 for t in trades if t["close_reason"] == "TP")
            sl_c  = sum(1 for t in trades if t["close_reason"] == "SL")
            dte_c = sum(1 for t in trades if t["close_reason"] == "DTE")
            exp_c = sum(1 for t in trades if t["close_reason"] in ("EXPIRY", "EOA"))

            self.log(
                f"{ticker} | {n_trades} | {win_rate:.1f}% | {avg_pct:.1f}% | "
                f"${avg_prem:.0f} | {pf_str} | {avg_spread:.1f} | "
                f"{tp_c} | {sl_c} | {dte_c} | {exp_c}"
            )

            total_trades += n_trades
            total_premium += sum(t.get("premium_collected") or 0 for t in trades)
            weighted_win_rate += win_rate * n_trades
            (strong_tickers if pf_val >= 1.5 and n_trades >= 10 else weak_tickers).append(ticker)

        self.log("=== CPS SUMMARY ===")
        self.log(f"Total trades: {total_trades}")
        self.log(f"Total premium collected: ${total_premium:.2f}")
        if total_trades > 0:
            self.log(f"Weighted avg win rate: {weighted_win_rate / total_trades:.1f}%")
        self.log(f"Strong tickers (PF >= 1.5 AND trades >= 10): {', '.join(strong_tickers) or 'none'}")
        self.log(f"Weak tickers (PF < 1.5 OR trades < 10): {', '.join(weak_tickers) or 'none'}")
        self._log_yearly("pct_collected")

    def _log_yearly(self, pnl_key):
        self.log("=== YEARLY P&L SUMMARY ===")
        self.log("YEAR | TRADES | TOTAL_PNL%")
        yearly = {}
        for ticker in config.TICKERS:
            for t in self.trade_stats[ticker]["trades"]:
                if t.get("exit_date") is None:
                    continue
                year = t["exit_date"].year
                if year not in yearly:
                    yearly[year] = {"n": 0, "pnl": 0.0}
                yearly[year]["n"] += 1
                yearly[year]["pnl"] += t.get(pnl_key, 0) or 0

        for year in sorted(yearly):
            self.log(f"{year} | {yearly[year]['n']} | {yearly[year]['pnl']:.1f}%")
