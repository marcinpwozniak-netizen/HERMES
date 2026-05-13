from AlgorithmImports import *
from indicators import ElderForceIndex, StochRSI, ElderImpulse
import config


class DedalSignalGenerator:
    """
    Generates LONG / EXIT / NONE signals based on the Dedal indicator logic
    combining Elder Force Index, Stochastic RSI, and Elder Impulse.
    """

    def __init__(self, ticker,
                 efi_period=23, efi_smma_period=10,
                 impulse_ema_period=13,
                 stoch_rsi_period=13, stoch_period=8,
                 stoch_smooth_k=5, stoch_smooth_d=5):
        self.efi = ElderForceIndex(f"{ticker}_EFI",
                                   efi_period=efi_period,
                                   smma_period=efi_smma_period)
        self.stoch_rsi = StochRSI(f"{ticker}_SRSI",
                                  rsi_period=stoch_rsi_period,
                                  stoch_period=stoch_period,
                                  smooth_k=stoch_smooth_k,
                                  smooth_d=stoch_smooth_d)
        self.impulse = ElderImpulse(f"{ticker}_IMPULSE",
                                    ema_trend_period=impulse_ema_period)

        self._prev_efi = None
        self._prev_smma = None
        self._prev_k = None
        self._prev_d = None
        self._current_efi = None
        self._current_smma = None
        self._current_k = None
        self._current_d = None

    def _crossover(self, current_a, prev_a, current_b, prev_b):
        """True when a crosses above b."""
        return prev_a <= prev_b and current_a > current_b

    def _crossunder(self, current_a, prev_a, current_b, prev_b):
        """True when a crosses below b."""
        return prev_a >= prev_b and current_a < current_b

    def update(self, bar):
        """Feed a new bar to all three indicators."""
        self.efi.update(bar)
        self.stoch_rsi.update(bar)
        self.impulse.update(bar)

        if self.efi.is_ready:
            self._prev_efi = self._current_efi
            self._prev_smma = self._current_smma
            self._current_efi = self.efi.efi
            self._current_smma = self.efi.smma

        if self.stoch_rsi.is_ready:
            self._prev_k = self._current_k
            self._prev_d = self._current_d
            self._current_k = self.stoch_rsi.k
            self._current_d = self.stoch_rsi.d

    def get_signal(self):
        """Returns 'LONG', 'EXIT', or 'NONE'."""
        if not (self.efi.is_ready and self.stoch_rsi.is_ready and self.impulse.is_ready):
            return "NONE"

        if self._prev_efi is None or self._prev_smma is None:
            return "NONE"
        if self._prev_k is None or self._prev_d is None:
            return "NONE"

        efi = self._current_efi
        smma = self._current_smma
        k = self._current_k
        d = self._current_d

        efi_cross_up = self._crossover(efi, self._prev_efi, smma, self._prev_smma)
        k_cross_up = self._crossover(k, self._prev_k, d, self._prev_d)  # FIX HER-31

        is_long = (efi_cross_up or (efi > smma and k_cross_up)) and not self.impulse.is_red

        d_cross_up = self._crossover(d, self._prev_d, k, self._prev_k)
        efi_cross_down = self._crossunder(efi, self._prev_efi, smma, self._prev_smma)

        is_exit = d_cross_up or efi_cross_down

        if is_long and is_exit:
            return "NONE"
        if is_long:
            return "LONG"
        if is_exit:
            return "EXIT"
        return "NONE"
