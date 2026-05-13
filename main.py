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
        self.set_start_date(2015, 1, 1)
        self.set_end_date(2026, 1, 1)
        self.set_cash(100_000)
        self.set_warm_up(300, Resolution.DAILY)
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.MARGIN)

        self.signals = {}
        self.trade_stats = {}
        self.option_symbols = {}          # ticker → canonical option Symbol
        self.open_options = {}            # ticker → open CPS position data
        self.pending_open = {}            # ticker → True when LONG signal awaits execution
        self.pending_signal_price = {}    # ticker → close price at signal time
        self.pending_open_date = {}       # ticker → datetime when signal was set
        self._current_data = None
        self._bear_sma = None  # created after add_equity below

        # FB subscribed only in equity mode for extended META pre-rename history
        subscribe = list(config.TICKERS)
        if config.INSTRUMENT == "equity" and "META" in config.TICKERS:
            subscribe.append("FB")

        for ticker in subscribe:
            equity = self.add_equity(ticker, Resolution.DAILY)
            equity.set_data_normalization_mode(DataNormalizationMode.ADJUSTED)

            if config.BEAR_FILTER_ENABLED and ticker == config.TICKERS[0]:
                self._bear_sma = self.SMA(
                    config.TICKERS[0],
                    config.BEAR_FILTER_PERIOD,
                    Resolution.DAILY
                )

            if config.INSTRUMENT == "options" and ticker in config.TICKERS:
                option = self.add_option(ticker, Resolution.DAILY)
                option.price_model = OptionPriceModels.binomial_cox_ross_rubinstein()
                option.set_filter(lambda u: u.puts_only()
                    .delta(
                        -(config.TARGET_DELTA + config.DELTA_TOLERANCE),
                        -(config.TARGET_DELTA - config.DELTA_TOLERANCE)
                    )
                    .expiration(config.OPTION_FILTER_MIN_DTE, config.OPTION_FILTER_MAX_DTE)
                )
                self.option_symbols[ticker] = option.symbol
                self.open_options[ticker] = self._empty_options_pos()
                self.pending_open[ticker] = False
                self.pending_signal_price[ticker] = 0.0
                self.pending_open_date[ticker] = None

            if config.SIGNAL_INTERVAL == "daily":
                self.consolidate(ticker, timedelta(days=1), self.on_signal_bar)
            else:
                self.consolidate(ticker, timedelta(days=7), self.on_signal_bar)
            self.signals[ticker] = DedalSignalGenerator(
                ticker,
                efi_period=config.EFI_PERIOD,
                efi_smma_period=config.EFI_SMMA_PERIOD,
                impulse_ema_period=config.IMPULSE_EMA_PERIOD,
                stoch_rsi_period=config.STOCH_RSI_PERIOD,
                stoch_period=config.STOCH_PERIOD,
                stoch_smooth_k=config.STOCH_SMOOTH_K,
                stoch_smooth_d=config.STOCH_SMOOTH_D,
            )

            stats_key = "META" if ticker == "FB" else ticker
            if stats_key not in self.trade_stats:
                self.trade_stats[stats_key] = {
                    "trades": [],
                    "open_bar": None,
                    "entry_price": None,
                    "entry_date": None,
                    "bar_count": 0,
                }

        if config.INSTRUMENT == "options":
            # Pin risk: close ITM spreads before assignment
            self.schedule.on(
                self.date_rules.every_day(config.TICKERS[0]),
                self.time_rules.before_market_close(config.TICKERS[0], 30),
                self._check_pin_risk
            )

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

    def on_signal_bar(self, bar):
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
            self.debug(f"[{ticker}] on_signal_bar: {e}")

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
            if self._is_bear_market():
                self.debug(
                    f"[{ticker}] LONG signal blocked: bear market filter active "
                    f"(spot < SMA{config.BEAR_FILTER_PERIOD} = {self._bear_sma.Current.Value:.2f})"
                )
            else:
                self.pending_open[ticker] = True
                self.pending_signal_price[ticker] = close
                self.pending_open_date[ticker] = self.time

                # Daily mode: attempt same-bar open to eliminate 24h lag.
                # on_signal_bar fires as a consolidator callback inside on_data,
                # so self._current_data already contains today's chain snapshot.
                if config.SIGNAL_INTERVAL == "daily" and self._current_data is not None:
                    if self._open_cps(ticker, close):
                        self.pending_open[ticker] = False

        elif signal == "EXIT":
            if self.pending_open[ticker]:
                self.pending_open[ticker] = False
            # Close open CPS on EXIT signal if enabled
            if has_open and config.CLOSE_ON_EXIT:
                self._close_cps(ticker, "EXIT_SIGNAL")

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
                    days_pending = (self.time - self.pending_open_date[ticker]).days
                    if days_pending > config.PENDING_OPEN_TIMEOUT_DAYS:
                        self.debug(f"[{ticker}] Signal expired after {days_pending}d, cancelling")
                        self.pending_open[ticker] = False
                    elif self._open_cps(ticker, self.pending_signal_price[ticker]):
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

        # Filter by liquidity and Greeks availability
        liquid_puts = [
            c for c in expiry_puts
            if c.greeks is not None
            and c.greeks.delta is not None
            and c.bid_price > 0 and c.ask_price > 0
            and (c.ask_price - c.bid_price) <= config.MAX_BID_ASK_SPREAD
            and c.open_interest >= config.MIN_OPEN_INTEREST
        ]

        if not liquid_puts:
            self.debug(f"[{ticker}] No liquid puts with Greeks for {best_expiry.date()}")
            return False

        # Short strike: contract with delta closest to -TARGET_DELTA
        short_c = min(liquid_puts, key=lambda c: abs(abs(c.greeks.delta) - config.TARGET_DELTA))
        actual_delta = abs(short_c.greeks.delta)

        if abs(actual_delta - config.TARGET_DELTA) > config.DELTA_TOLERANCE:
            self.debug(f"[{ticker}] No contract within delta tolerance (closest: {actual_delta:.3f})")
            return False

        short_strike = short_c.strike

        # Long strike: closest liquid contract below short_strike to (short - SPREAD_WIDTH)
        liquid_below = [c for c in liquid_puts if c.strike < short_strike]
        if not liquid_below:
            self.debug(f"[{ticker}] No liquid strikes below {short_strike}")
            return False

        long_c = min(liquid_below, key=lambda c: abs(c.strike - (short_strike - config.SPREAD_WIDTH)))
        long_strike = long_c.strike

        # Actual spread width (may differ from config.SPREAD_WIDTH if exact strike unavailable)
        actual_spread_width = short_strike - long_strike

        _sw_min = config.SPREAD_WIDTH - config.SPREAD_WIDTH_TOLERANCE
        _sw_max = config.SPREAD_WIDTH + config.SPREAD_WIDTH_TOLERANCE
        if actual_spread_width < _sw_min:
            self.debug(
                f"[{ticker}] Spread too narrow: {actual_spread_width} "
                f"(accepted range {_sw_min}-{_sw_max}), skipping"
            )
            return False
        if actual_spread_width > _sw_max:
            self.debug(
                f"[{ticker}] Spread too wide: {actual_spread_width} "
                f"(accepted range {_sw_min}-{_sw_max}), skipping"
            )
            return False

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
            f"delta={actual_delta:.3f} "
            f"exp={best_expiry.date()} DTE={actual_dte} "
            f"prem=${premium_collected:.2f} risk=${max_loss:.2f} x{n_spreads} "
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

    def _is_bear_market(self):
        """Returns True when bear market filter is active and spot < SMA200."""
        if not config.BEAR_FILTER_ENABLED or self._bear_sma is None:
            return False
        if not self._bear_sma.IsReady:
            return False
        try:
            spot = float(self.securities[config.TICKERS[0]].price)
            return spot < self._bear_sma.Current.Value
        except Exception:
            return False

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

        # Delta-based SL: zamknij gdy short put zbliża się do ATM
        delta_sl_triggered = False
        if self._current_data is not None:
            option_sym = pos.get("contract")
            short_sym = pos.get("short_symbol")
            if option_sym and short_sym and option_sym in self._current_data.option_chains:
                chain = self._current_data.option_chains[option_sym]
                short_contract = next((c for c in chain if c.symbol == short_sym), None)
                if short_contract and short_contract.greeks is not None:
                    if abs(short_contract.greeks.delta) >= config.DELTA_SL_THRESHOLD:
                        delta_sl_triggered = True

        close_reason = None
        if days_to_expiry <= 0:
            close_reason = "EXPIRY"
        elif days_to_expiry < config.CLOSE_DTE:
            close_reason = "DTE"
        elif config.TP_PCT > 0 and pct_of_premium >= config.TP_PCT:
            close_reason = "TP"
        elif config.SL_PCT > 0 and delta_sl_triggered:
            close_reason = "DELTA_SL"  # FIX HER-33: skip delta SL when SL_PCT=0
        elif config.SL_PCT > 0 and current_pnl <= -(abs(premium_collected) * config.SL_PCT / 100.0):
            close_reason = "SL"

        if close_reason:
            self._close_cps(ticker, close_reason)

    def _check_pin_risk(self):
        """
        Fires 30 min before market close every day.
        - DTE == 0 (expiry day):  always close (prevent assignment at bell)
        - DTE == 1 (day before):  close only if short put is ITM (spot < short_strike)
        """
        if config.INSTRUMENT != "options" or self.is_warming_up:
            return

        for ticker in config.TICKERS:
            try:
                pos = self.open_options.get(ticker, {})
                if pos.get("contract") is None:
                    continue

                days_to_expiry = (pos["expiry"].date() - self.time.date()).days

                if days_to_expiry == 0:
                    self.debug(f"[{ticker}] PIN_RISK_0DTE: closing 30min before expiry close")
                    self._close_cps(ticker, "PIN_RISK_0DTE")

                elif days_to_expiry == 1:
                    spot = float(self.securities[ticker].price)
                    strike_short = pos.get("strike_short") or 0.0
                    if spot < strike_short:
                        self.debug(
                            f"[{ticker}] PIN_RISK_1DTE: spot {spot:.2f} < short strike "
                            f"{strike_short:.2f}, closing 30min before close"
                        )
                        self._close_cps(ticker, "PIN_RISK_1DTE")

            except Exception as e:
                self.debug(f"[{ticker}] _check_pin_risk error: {e}")

    def _close_cps(self, ticker, close_reason):
        pos = self.open_options[ticker]
        premium_collected = pos["premium_collected"] or 0.0

        # Capture current P&L before submitting the closing order
        current_pnl = 0.0
        for leg in ("short_symbol", "long_symbol"):
            sym = pos.get(leg)
            if sym is not None:
                try:
                    current_pnl += float(self.portfolio[sym].unrealized_profit)
                except Exception:
                    pass

        # Close as combo order — both legs atomically (FIX HER-33)
        try:
            bull_put = OptionStrategies.bull_put_spread(
                pos["contract"],
                pos["strike_short"],
                pos["strike_long"],
                pos["expiry"]
            )
            self.sell(bull_put, pos["n_spreads"])
        except Exception as e:
            self.debug(f"[{ticker}] Combo close failed, falling back to leg-by-leg: {e}")
            for leg in ("short_symbol", "long_symbol"):
                sym = pos.get(leg)
                if sym is not None:
                    try:
                        qty = int(self.portfolio[sym].quantity)
                        if qty != 0:
                            self.market_order(sym, -qty)
                    except Exception:
                        pass

        pct_of_premium = (
            current_pnl / abs(premium_collected) * 100.0 if premium_collected != 0 else 0.0
        )
        self.debug(f"[{ticker}] CPS closed ({close_reason}): pct={pct_of_premium:.1f}% captured=${current_pnl:.2f}")
        self._record_cps_trade(ticker, close_reason, pct_of_premium, current_pnl)
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

    def _record_cps_trade(self, ticker, close_reason, pct_of_premium, premium_captured=0.0):
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
            "premium_captured":    premium_captured,
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
            "PF | AVG_SPREAD | TP_closes | SL_closes | DTE_closes | EXPIRY_closes | PCR | EXIT_closes | PIN_closes"
        )

        total_trades = 0
        total_premium = 0.0
        weighted_win_rate = 0.0
        global_collected = 0.0
        global_captured = 0.0
        strong_tickers = []
        weak_tickers = []

        for ticker in config.TICKERS:
            trades = self.trade_stats[ticker]["trades"]
            n_trades = len(trades)

            if n_trades == 0:
                self.log(f"{ticker} | 0 | N/A | N/A | N/A | N/A | N/A | 0 | 0 | 0 | 0 | N/A | 0 | 0")
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
            sl_c  = sum(1 for t in trades if t["close_reason"] in ("SL", "DELTA_SL"))
            dte_c = sum(1 for t in trades if t["close_reason"] == "DTE")
            exp_c = sum(1 for t in trades if t["close_reason"] in ("EXPIRY", "EOA"))
            exit_c = sum(1 for t in trades if t["close_reason"] == "EXIT_SIGNAL")
            pin_c  = sum(1 for t in trades if t["close_reason"] in ("PIN_RISK_0DTE", "PIN_RISK_1DTE"))

            total_collected = sum(t.get("premium_collected") or 0 for t in trades)
            total_captured  = sum(t.get("premium_captured")  or 0 for t in trades)
            pcr = (total_captured / total_collected * 100.0) if total_collected != 0 else 0.0

            self.log(
                f"{ticker} | {n_trades} | {win_rate:.1f}% | {avg_pct:.1f}% | "
                f"${avg_prem:.0f} | {pf_str} | {avg_spread:.1f} | "
                f"{tp_c} | {sl_c} | {dte_c} | {exp_c} | {pcr:.1f}% | {exit_c} | {pin_c}"
            )

            total_trades += n_trades
            total_premium += total_collected
            global_collected += total_collected
            global_captured += total_captured
            weighted_win_rate += win_rate * n_trades
            (strong_tickers if pf_val >= 1.5 and n_trades >= 10 else weak_tickers).append(ticker)

        self.log("=== CPS SUMMARY ===")
        self.log(f"Total trades: {total_trades}")
        self.log(f"Total premium collected: ${total_premium:.2f}")
        self.log(f"Total premium captured: ${global_captured:.2f}")
        global_pcr = (global_captured / global_collected * 100.0) if global_collected != 0 else 0.0
        self.log(f"Global PCR: {global_pcr:.1f}%")
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
