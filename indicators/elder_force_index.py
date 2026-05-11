from AlgorithmImports import *


class ElderForceIndex(PythonIndicator):
    """
    Elder Force Index: measures the power behind price moves.
    - raw EFI = change(close) * volume
    - efi = SMA(raw, efi_period)
    - smma = Wilder's Smoothed MA of efi, smma_period
    """

    def __init__(self, indicator_name, efi_period=23, smma_period=10):
        self.name = indicator_name
        self.efi_period = efi_period
        self.smma_period = smma_period

        self._closes = RollingWindow[float](2)
        self._raw_window = RollingWindow[float](efi_period + smma_period + 5)
        self._efi_window = RollingWindow[float](smma_period + 5)

        self._raw_count = 0
        self._efi_count = 0
        self._smma_initialized = False
        self._smma_value = 0.0

        self.efi = 0.0
        self.smma = 0.0
        self.value = 0.0
        self.warm_up_period = efi_period + smma_period

    @property
    def is_ready(self):
        return self._smma_initialized and self._efi_count >= self.smma_period

    def update(self, input_data):
        close = float(input_data.close)
        volume = float(input_data.volume)

        self._closes.Add(close)

        if not self._closes.IsReady:
            return False

        raw = (self._closes[0] - self._closes[1]) * volume
        self._raw_window.Add(raw)
        self._raw_count += 1

        if self._raw_count < self.efi_period:
            return False

        raw_vals = [self._raw_window[i] for i in range(self.efi_period)]
        efi_val = sum(raw_vals) / self.efi_period
        self.efi = efi_val

        self._efi_window.Add(efi_val)
        self._efi_count += 1

        if not self._smma_initialized:
            if self._efi_count >= self.smma_period:
                efi_vals = [self._efi_window[i] for i in range(self.smma_period)]
                self._smma_value = sum(efi_vals) / self.smma_period
                self._smma_initialized = True
        else:
            self._smma_value = (
                (self._smma_value * (self.smma_period - 1) + efi_val) / self.smma_period
            )

        self.smma = self._smma_value
        self.value = self.smma
        return self.is_ready
