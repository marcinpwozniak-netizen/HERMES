# Tryb instrumentu
INSTRUMENT = "options"  # "equity" lub "options"

# Universe
TICKERS = [
    "SPY" #, "GLD",
#    "KO", "XOM",
#    "JNJ","LMT", "HON", "LLY", "V"
]

# Wspólne
POSITION_SIZE_PCT = 0.02      # % kapitału na jedną pozycję

# Parametry opcyjne (używane tylko gdy INSTRUMENT="options")
DTE_TARGET    = 45            # docelowe DTE przy otwarciu
CLOSE_DTE     = 14            # zamknij gdy DTE spadnie poniżej tej wartości
SPREAD_WIDTH       = 10     # szerokość spreada w dolarach (strike distance)
TP_PCT             = 50     # Take Profit jako % pobranej premii (0 = off)
SL_PCT             = 200    # Stop Loss jako % pobranej premii (0 = off)
MAX_EXPOSURE_PCT   = 0.25   # max % kapitału zaangażowanego w otwarte CPS łącznie
TARGET_DELTA       = 0.20   # docelowa delta short puta (wartość absolutna)
DELTA_TOLERANCE    = 0.07   # max odchylenie od TARGET_DELTA
MIN_OPEN_INTEREST  = 100    # minimalny Open Interest kontraktu
MAX_BID_ASK_SPREAD = 0.50   # max spread Bid-Ask na nodze (USD)
DELTA_SL_THRESHOLD = 0.50   # zamknij gdy |delta short puta| >= tej wartości