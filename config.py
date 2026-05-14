SIGNAL_INTERVAL = "daily"       # "weekly" | "daily"

# Universe & instrument
INSTRUMENT  = "options"
TICKERS     = ["SPY"]

# Position sizing
POSITION_SIZE_PCT = 0.10        # 10% — spread is narrow, can go slightly larger

# Options — short-term setup (4-7 DTE)
DTE_TARGET    = 4               # target DTE at open; also test 7
CLOSE_DTE     = 0               # hold to expiry; no early exit by DTE
SPREAD_WIDTH  = 10               # $5 wide — hard cap on max loss per contract
SPREAD_WIDTH_TOLERANCE = 7      # Accept spreads within ±1 of SPREAD_WIDTH (e.g. 4/5/6 for target=5)
TP_PCT        = 70              # take profit at 80% premium collected
SL_PCT        = 0               # disabled — $5 spread IS the stop loss
CLOSE_ON_EXIT = True            # Close open CPS immediately when Dedal EXIT signal fires
MAX_EXPOSURE_PCT = 0.45

# Greeks
TARGET_DELTA       = 0.30       # more aggressive than 45-DTE setup
DELTA_TOLERANCE    = 0.10
DELTA_SL_THRESHOLD = 1.0        # effectively disabled (delta never reaches 1.0)

# Liquidity filters
MIN_OPEN_INTEREST  = 100
MIN_SPREAD_WIDTH   = 3          # narrower floor for 5-wide spreads
MAX_BID_ASK_SPREAD = 0.30       # tighter — short DTE options are liquid on SPY

# Signal expiry (daily mode: don't wait more than 1 day for a fill)
PENDING_OPEN_TIMEOUT_DAYS = 1

# Option chain filter DTE range
OPTION_FILTER_MIN_DTE = 2       # was 10 — need to see 4 DTE expiries
OPTION_FILTER_MAX_DTE = 16      # was 60 — no need for long-dated chains

# ── Fast Dedal indicator periods (daily mode) ──────────────────────────
# Weekly defaults shown in comments for reference
EFI_PERIOD        = 2           # Elder Force Index SMA period  (weekly: 23)
EFI_SMMA_PERIOD   = 3           # EFI Wilder smoothing period   (weekly: 10)
IMPULSE_EMA_PERIOD = 5          # Elder Impulse trend EMA       (weekly: 13)
STOCH_RSI_PERIOD  = 14          # StochRSI — RSI period         (weekly: 13)
STOCH_PERIOD      = 14          # StochRSI — stoch period       (weekly: 8)
STOCH_SMOOTH_K    = 3           # StochRSI — K smooth           (weekly: 5)
STOCH_SMOOTH_D    = 3           # StochRSI — D smooth           (weekly: 5)

# ── Bear market filter ────────────────────────────────────────────────
BEAR_FILTER_ENABLED = False      # Block new CPS entries when SPY < SMA(BEAR_FILTER_PERIOD)
BEAR_FILTER_PERIOD  = 33       # SMA period in daily bars
