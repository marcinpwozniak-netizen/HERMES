# Hermes CPS Signal

QuantConnect/LEAN backtest of the **Dedal** trading system.

Operates in two modes controlled by `config.py`:
- **equity** — buys/sells shares on weekly Dedal signals
- **options** — sells Credit Put Spreads (bull put spreads) using those same signals as entries

---

## Strategy overview

### Signal engine — Dedal

Three indicators computed on weekly bars:

| Indicator | Description |
|-----------|-------------|
| **Elder Force Index** | `EFI = Δclose × volume`, smoothed with 23-period SMA then 10-period SMMA |
| **Stochastic RSI** | RSI(13) → Stoch(8) → K = SMA(5) → D = SMA(5) |
| **Elder Impulse** | EMA(13) + MACD histogram slope → green / red / blue |

**LONG signal** — open a position when:
- EFI crosses its SMMA upward, **or** EFI is above SMMA and K crosses D downward
- AND Elder Impulse is **not** red

**EXIT signal** — close a position when:
- D crosses K upward, **or** EFI crosses its SMMA downward

### Options mode — Credit Put Spread

On a LONG signal the algorithm marks the ticker as *pending*. Each subsequent daily bar it looks for a suitable option chain and, when found, sells a bull put spread:

- **Short leg**: put with |delta| closest to `TARGET_DELTA` (default 0.20), within ±`DELTA_TOLERANCE`
- **Long leg**: put at approximately `short_strike − SPREAD_WIDTH` dollars lower
- Target DTE: ~45 days

Position management per bar:
| Rule | Action |
|------|--------|
| DTE ≤ `CLOSE_DTE` | close — time exit |
| Unrealised P&L ≥ `TP_PCT` % of premium collected | close — take profit |
| Unrealised P&L ≤ −`SL_PCT` % of premium collected | close — stop loss |
| |delta of short put| ≥ `DELTA_SL_THRESHOLD` | close — delta stop |
| Pending signal older than `PENDING_OPEN_TIMEOUT_DAYS` | cancel — expired |

Exposure is capped at `MAX_EXPOSURE_PCT` of portfolio equity.

---

## Configuration (`config.py`)

```python
INSTRUMENT = "options"          # "equity" or "options"
TICKERS    = ["SPY", "QQQ", "XLK"]

POSITION_SIZE_PCT  = 0.03       # % of capital per position
MAX_EXPOSURE_PCT   = 0.40       # max total exposure across all open spreads

# Options parameters
DTE_TARGET         = 45
CLOSE_DTE          = 7
SPREAD_WIDTH       = 10         # strike distance in USD
TP_PCT             = 50         # take-profit as % of premium collected
SL_PCT             = 200        # stop-loss  as % of premium collected
TARGET_DELTA       = 0.20
DELTA_TOLERANCE    = 0.07
DELTA_SL_THRESHOLD = 0.50
MIN_OPEN_INTEREST  = 100
MIN_SPREAD_WIDTH   = 5          # minimum accepted spread width in USD
MAX_BID_ASK_SPREAD = 0.50       # per-leg bid/ask filter
PENDING_OPEN_TIMEOUT_DAYS = 3   # cancel pending signal after this many days
```

---

## Project structure

```
hermes-cps-signal/
├── main.py                  # HermesCPSSignal(QCAlgorithm)
├── config.py                # all parameters
├── signal_generator.py      # DedalSignalGenerator
├── indicators/
│   ├── __init__.py
│   ├── elder_force_index.py
│   ├── stoch_rsi.py
│   └── elder_impulse.py
├── lean.json                # LEAN project config
└── README.md
```

---

## Stack

- Python 3 / QuantConnect LEAN v2
- LEAN CLI (`lean cloud backtest "hermes-cps-signal"`)
