# Tryb instrumentu
INSTRUMENT = "options"  # "equity" lub "options"

# Universe
TICKERS = ["SPY"]

# Wspólne
POSITION_SIZE_PCT = 0.15      # % kapitału na jedną pozycję

# Parametry opcyjne (używane tylko gdy INSTRUMENT="options")
DTE_TARGET    = 45            # docelowe DTE przy otwarciu
CLOSE_DTE     = 7             # zamknij gdy DTE spadnie poniżej tej wartości
SPREAD_WIDTH       = 25     # szerokość spreada w dolarach (strike distance)
TP_PCT             = 50     # Take Profit jako % pobranej premii (0 = off)
SL_PCT             = 200    # Stop Loss jako % pobranej premii (0 = off)
MAX_EXPOSURE_PCT   = 0.45   # max % kapitału zaangażowanego w otwarte CPS łącznie
TARGET_DELTA       = 0.15    # docelowa delta short puta (wartość absolutna)
DELTA_TOLERANCE    = 0.07   # max odchylenie od TARGET_DELTA
MIN_OPEN_INTEREST  = 100    # minimalny Open Interest kontraktu
MIN_SPREAD_WIDTH   = 5      # minimalna akceptowana szerokość spreadu w USD
MAX_BID_ASK_SPREAD = 0.50   # max spread Bid-Ask na nodze (USD)
DELTA_SL_THRESHOLD = 0.50   # zamknij gdy |delta short puta| >= tej wartości
PENDING_OPEN_TIMEOUT_DAYS = 3  # anuluj pending sygnał po tylu dniach bez wykonania