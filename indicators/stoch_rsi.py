from AlgorithmImports import *


class StochRSI(PythonIndicator):
    """
    Stochastic RSI — matches TradingView implementation.
    - rsi = RSI(close, rsi_period)
    - stoch = (rsi - lowest(rsi, stoch_period)) / (highest(rsi, stoch_period) - lowest(rsi, stoch_period)) * 100
    - k = SMA(stoch, smooth_k)
    - d = SMA(k, smooth_d)
    """

    def __init__(self, indicator_name, rsi_period=13, stoch_period=8, smooth_k=5, smooth_d=5):
        self.name = indicator_name
        self.rsi_period = rsi_period
        self.stoch_period = stoch_period
        self.smooth_k = smooth_k
        self.smooth_d = smooth_d

        self._closes = RollingWindow[float](rsi_period + 2)
        self._rsi_window = RollingWindow[float](stoch_period + 5)
        self._stoch_window = RollingWindow[float](smooth_k + 5)
        self._k_window = RollingWindow[float](smooth_d + 5)

        self._close_count = 0
        self._rsi_count = 0
        self._stoch_count = 0
        self._k_count = 0

        self._avg_gain = 0.0
        self._avg_loss = 0.0
        self._rsi_initialized = False

        self.k = 0.0
        self.d = 0.0
        self.value = 0.0
        self.warm_up_period = rsi_period + stoch_period + smooth_k + smooth_d

    @property
    def is_ready(self):
        return self._k_count >= self.smooth_d

    def _compute_rsi(self, close):
        self._closes.Add(close)
        self._close_count += 1

        if self._close_count < 2:
            return None

        change = self._closes[0] - self._closes[1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if not self._rsi_initialized:
            if self._close_count <= self.rsi_period:
                self._avg_gain += gain / self.rsi_period
                self._avg_loss += loss / self.rsi_period
                if self._close_count == self.rsi_period:
                    self._rsi_initialized = True
                return None
        else:
            self._avg_gain = (self._avg_gain * (self.rsi_period - 1) + gain) / self.rsi_period
            self._avg_loss = (self._avg_loss * (self.rsi_period - 1) + loss) / self.rsi_period

        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def update(self, input_data):
        close = float(input_data.close)

        rsi_val = self._compute_rsi(close)
        if rsi_val is None:
            return False

        self._rsi_window.Add(rsi_val)
        self._rsi_count += 1

        if self._rsi_count < self.stoch_period:
            return False

        rsi_vals = [self._rsi_window[i] for i in range(min(self._rsi_count, self.stoch_period))]
        lowest = min(rsi_vals)
        highest = max(rsi_vals)

        stoch_val = 0.0 if highest == lowest else (rsi_val - lowest) / (highest - lowest) * 100.0

        self._stoch_window.Add(stoch_val)
        self._stoch_count += 1

        if self._stoch_count < self.smooth_k:
            return False

        k_val = sum(self._stoch_window[i] for i in range(self.smooth_k)) / self.smooth_k
        self.k = k_val

        self._k_window.Add(k_val)
        self._k_count += 1

        if self._k_count < self.smooth_d:
            return False

        self.d = sum(self._k_window[i] for i in range(self.smooth_d)) / self.smooth_d
        self.value = self.k
        return True
