# Tryb instrumentu
INSTRUMENT = "options"  # "equity" lub "options"

# Universe
TICKERS = [
    "SPY", "GLD",
    "KO", "XOM",
    "JNJ","LMT", "HON", "LLY", "V"
]

# Wspólne
POSITION_SIZE_PCT = 0.02      # % kapitału na jedną pozycję

# Parametry opcyjne (używane tylko gdy INSTRUMENT="options")
DTE_TARGET    = 45            # docelowe DTE przy otwarciu
CLOSE_DTE     = 14            # zamknij gdy DTE spadnie poniżej tej wartości
SPREAD_WIDTH  = 10            # szerokość spreada w dolarach (strike distance)
STRIKE_OFFSET = 1             # liczba strikeów poniżej ceny sygnału
TP_PCT        = 50             # Take Profit jako % pobranej premii (0 = off)
SL_PCT        = 200            # Stop Loss jako % pobranej premii (0 = off)
MAX_EXPOSURE_PCT = 0.25      # max % kapitału zaangażowanego w otwarte CPS łącznie