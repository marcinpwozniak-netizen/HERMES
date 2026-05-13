from AlgorithmImports import *


class ElderImpulse(PythonIndicator):
    """
    Elder Impulse System.
    - ema13: EMA(close, 13)
    - macd histogram: EMA(12) - EMA(26), smoothed with EMA(9)
    - green: ema13 rising AND histogram rising
    - red:   ema13 falling AND histogram falling
    - blue:  otherwise
    """

    def __init__(self, indicator_name, ema_trend_period=13):
        self.name = indicator_name

        self._ema13 = self._make_ema(ema_trend_period)
        self._ema12 = self._make_ema(12)
        self._ema26 = self._make_ema(26)
        self._ema_signal = self._make_ema(9)

        self._prev_ema13 = None
        self._prev_histogram = None
        self._bar_count = 0

        self.color = "blue"
        self.is_red = False
        self.value = 0.0
        self.warm_up_period = max(ema_trend_period, 26) + 9 + 5

    def _make_ema(self, period):
        return {"alpha": 2.0 / (period + 1), "value": None, "period": period, "count": 0}

    def _update_ema(self, state, val):
        state["count"] += 1
        if state["value"] is None:
            state["value"] = val
        else:
            state["value"] = state["alpha"] * val + (1 - state["alpha"]) * state["value"]
        return state["value"]

    @property
    def is_ready(self):
        return (
            self._ema13["count"] >= self._ema13["period"]
            and self._ema26["count"] >= self._ema26["period"]
            and self._ema_signal["count"] >= self._ema_signal["period"]
            and self._prev_ema13 is not None
            and self._prev_histogram is not None
        )

    def update(self, input_data):
        close = float(input_data.close)
        self._bar_count += 1

        ema13_val = self._update_ema(self._ema13, close)
        ema12_val = self._update_ema(self._ema12, close)
        ema26_val = self._update_ema(self._ema26, close)

        macd_line = ema12_val - ema26_val
        signal_val = self._update_ema(self._ema_signal, macd_line)
        histogram = macd_line - signal_val

        if self._prev_ema13 is not None and self._prev_histogram is not None:
            if ema13_val > self._prev_ema13 and histogram > self._prev_histogram:
                self.color = "green"
            elif ema13_val < self._prev_ema13 and histogram < self._prev_histogram:
                self.color = "red"
            else:
                self.color = "blue"
            self.is_red = self.color == "red"

        self._prev_ema13 = ema13_val
        self._prev_histogram = histogram
        self.value = 1.0 if self.color == "green" else 0.0
        return self.is_ready
