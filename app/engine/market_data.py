import time
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any
import httpx
import logzero
from logzero import logger
from .options_engine import calculate_option_price_live


def is_indian_market_open() -> bool:
    """Check if Indian market is currently open (Mon-Fri, 09:15-15:40 IST).
    Uses fixed IST offset (+5:30), not system timezone.
    """
    now_utc = datetime.now(timezone.utc)
    ist_offset = timedelta(hours=5, minutes=30)
    ist_now = now_utc + ist_offset
    
    # Weekday: Mon=0, Fri=4, Sat=5, Sun=6
    if ist_now.weekday() > 4:  # Saturday=5, Sunday=6
        return False
    
    minutes_since_midnight = ist_now.hour * 60 + ist_now.minute
    market_open = 9 * 60 + 15   # 09:15
    market_close = 15 * 60 + 40  # 15:40 (includes Closing Auction Session)
    
    return market_open <= minutes_since_midnight < market_close

# Global in-memory notifications queue for super admin alerts
admin_notifications = []

INSTRUMENTS: Dict[str, Dict[str, Any]] = {
    "NIFTY50": {
        "name": "NIFTY 50 (NSE)",
        "category": "Indices",
        "spread": 1.5,
        "digits": 2,
        "pip_size": 0.05,
        "contract_size": 25,
        "angel_token": ("NSE", "26000"),
        "angel_historical_token": ("NSE", "99926000"),
    },
    "BANKNIFTY": {
        "name": "BANK NIFTY (NSE)",
        "category": "Indices",
        "spread": 2.5,
        "digits": 2,
        "pip_size": 0.05,
        "contract_size": 15,
        "angel_token": ("NSE", "26009"),
        "angel_historical_token": ("NSE", "99926009"),
    },
    "SENSEX": {
        "name": "BSE SENSEX (BSE)",
        "category": "Indices",
        "spread": 5.0,
        "digits": 2,
        "pip_size": 0.05,
        "contract_size": 10,
        "angel_token": ("BSE", "99919000"),
    },
    "FINNIFTY": {
        "name": "NIFTY FINANCIAL (NSE)",
        "category": "Indices",
        "spread": 1.2,
        "digits": 2,
        "pip_size": 0.05,
        "contract_size": 65,
        "angel_token": ("NSE", "26037"),
        "angel_historical_token": ("NSE", "99926037"),
    },
    "MIDCPNIFTY": {
        "name": "NIFTY MIDCAP SELECT (NSE)",
        "category": "Indices",
        "spread": 1.0,
        "digits": 2,
        "pip_size": 0.05,
        "contract_size": 50,
        "angel_token": ("NSE", "26074"),
        "angel_historical_token": ("NSE", "99926074"),
    },
    "RELIANCE": {
        "name": "Reliance Industries (NSE)",
        "category": "Equities",
        "spread": 0.5,
        "digits": 2,
        "pip_size": 0.05,
        "contract_size": 250,
        "angel_token": ("NSE", "2885"),
    },
    "HDFCBANK": {
        "name": "HDFC Bank Ltd (NSE)",
        "category": "Equities",
        "spread": 0.3,
        "digits": 2,
        "pip_size": 0.05,
        "contract_size": 550,
        "angel_token": ("NSE", "1333"),
    }
}

# YAHOO_MAP removed - using Angel One historical API instead

class MarketDataEngine:
    def __init__(self):
        self.prices: Dict[str, Dict[str, float]] = {}
        self.candle_cache: Dict[str, List[Dict[str, Any]]] = {}
        self.lock = threading.Lock()
        
        # SQLite persistent candle storage
        self.db_path = os.path.join(os.path.dirname(__file__), "..", "data", "candles.db")
        self._init_candle_db()
        
        # Angel One shared session management
        self.angel_session = None
        self.angel_session_lock = threading.Lock()
        self.angel_credentials = {
            "api_key": os.getenv("ANGEL_API_KEY", "3EWlZO4e"),
            "client_code": os.getenv("ANGEL_CLIENT_CODE", "G140240"),
            "pin": os.getenv("ANGEL_PIN", "5012"),
            "totp_key": os.getenv("ANGEL_TOTP_KEY", "FMKOE2BD2DHDRUPAI4AV3BWNKU"),
        }
        
        # Initialize prices & candle cache as empty
        self._initialize_prices()
        
        # Create and authenticate single shared Angel One session
        self._initialize_angel_session()
        
        # Load today's candles from SQLite BEFORE fetching historical data
        self._load_today_candles_from_db()
        
        # Fetch real historical 1m candles immediately so charts have real data from start
        self._fetch_angel_one_history_sync()
        
        # Clean up old candles (>7 days) from database
        self._cleanup_old_candles()
        
        # Start background live sync workers
        self._sync_live_prices()

    def _initialize_angel_session(self):
        """Create and authenticate the single shared Angel One SmartConnect session."""
        import pyotp
        import os
        from SmartApi import SmartConnect
        
        api_key = self.angel_credentials["api_key"]
        client_code = self.angel_credentials["client_code"]
        pin = self.angel_credentials["pin"]
        totp_key = self.angel_credentials["totp_key"]
        
        self.angel_session = SmartConnect(api_key=api_key)
        try:
            totp = pyotp.TOTP(self.angel_credentials["totp_key"]).now()
            self.angel_session.generateSession(
                self.angel_credentials["client_code"],
                self.angel_credentials["pin"],
                totp
            )
            logger.info("Angel One shared session authenticated successfully!")
        except Exception as e:
            logger.error(f"Failed to authenticate Angel One shared session: {e}")
            # Session remains None - workers will handle gracefully

    def _reauthenticate_angel_session(self) -> bool:
        """Re-authenticate the shared Angel One session. Thread-safe.
        
        Returns True if re-authentication succeeded, False otherwise.
        """
        with self.angel_session_lock:
            # Double-check pattern: another thread might have already re-authed
            if self._is_session_valid():
                logger.debug("Angel One session already valid, skipping re-auth")
                return True
            
            import pyotp
            from SmartApi import SmartConnect
            
            logger.warning("Attempting to re-authenticate Angel One shared session...")
            try:
                # Create fresh SmartConnect instance
                api_key = self.angel_credentials["api_key"]
                self.angel_session = SmartConnect(api_key=api_key)
                
                totp = pyotp.TOTP(self.angel_credentials["totp_key"]).now()
                self.angel_session.generateSession(
                    self.angel_credentials["client_code"],
                    self.angel_credentials["pin"],
                    totp
                )
                logger.info("Angel One shared session re-authenticated successfully!")
                return True
            except Exception as e:
                logger.error(f"Failed to re-authenticate Angel One shared session: {e}")
                self.angel_session = None
                return False

    def _is_session_valid(self) -> bool:
        """Check if the current session is likely still valid.
        Note: This is a best-effort check; actual validity is determined by API responses.
        """
        return self.angel_session is not None

    def _get_angel_session(self):
        """Get the shared Angel One session, re-authenticating if needed."""
        if self.angel_session is None:
            if not self._reauthenticate_angel_session():
                raise RuntimeError("Angel One session unavailable and re-authentication failed")
        return self.angel_session

    def _initialize_prices(self):
        """Initialize prices to empty - real data will come from Angel One on startup."""
        for symbol, cfg in INSTRUMENTS.items():
            self.prices[symbol] = {
                "bid": 0.0,
                "ask": 0.0,
                "mid": 0.0,
                "change_24h": 0.0,
                "high_24h": 0.0,
                "low_24h": 0.0,
            }
            self.candle_cache[symbol] = []

    def _init_candle_db(self):
        """Initialize SQLite database for persistent candle storage."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL,
                    time INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    PRIMARY KEY (symbol, time)
                )
            """)
            # Create index for faster time-based queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_candles_symbol_time 
                ON candles(symbol, time)
            """)
            conn.commit()

    def _save_candle_to_db(self, symbol: str, candle: Dict[str, Any]):
        """Persist a single candle to SQLite (upsert)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO candles (symbol, time, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (symbol, candle["time"], candle["open"], candle["high"], 
                      candle["low"], candle["close"], candle["volume"]))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to save candle to DB for {symbol}: {e}")

    def _load_today_candles_from_db(self):
        """Load today's candles from SQLite into candle_cache."""
        today_utc = datetime.now(timezone.utc)
        ist_offset = timedelta(hours=5, minutes=30)
        ist_now = today_utc + timedelta(hours=5, minutes=30)
        today_start = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start - timedelta(hours=5, minutes=30)
        start_ts = int(today_start_utc.timestamp())
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT symbol, time, open, high, low, close, volume
                FROM candles
                WHERE time >= ?
                ORDER BY symbol, time
            """, (start_ts,))
            
            loaded = 0
            for row in cursor:
                symbol = row["symbol"]
                if symbol not in self.candle_cache:
                    self.candle_cache[symbol] = []
                self.candle_cache[symbol].append({
                    "time": row["time"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"]
                })
                loaded += 1
            
            if loaded:
                logger.info(f"Loaded {loaded} candles from SQLite for today")

    def _cleanup_old_candles(self):
        """Delete candles older than 7 days from database."""
        cutoff_utc = datetime.now(timezone.utc) - timedelta(days=7)
        cutoff_ts = int(cutoff_utc.timestamp())
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("DELETE FROM candles WHERE time < ?", (cutoff_ts,))
                conn.commit()
                if cursor.rowcount > 0:
                    logger.info(f"Cleaned up {cursor.rowcount} old candles from database (older than 7 days)")
        except Exception as e:
            logger.error(f"Failed to cleanup old candles: {e}")

    def _save_candle_to_db(self, symbol: str, candle: Dict[str, Any]):
        """Persist a single candle to SQLite (upsert)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO candles (symbol, time, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (symbol, candle["time"], candle["open"], candle["high"], 
                      candle["low"], candle["close"], candle["volume"]))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to save candle to DB for {symbol}: {e}")
        except RuntimeError as e:
            logger.error(f"Cannot fetch history: {e}")
            return
        
        logger.info("Fetching historical candles from Angel One using shared session...")
        
        # Calculate date range: last 2 days of market hours
        from datetime import datetime, timedelta, timezone
        to_date = datetime.now(timezone.utc)
        from_date = to_date - timedelta(days=2)
        
        # Format for Angel One API: "YYYY-MM-DD HH:MM"
        from_str = from_date.strftime("%Y-%m-%d 09:15")
        to_str = to_date.strftime("%Y-%m-%d 15:40")
        
        candles_fetched = 0
        for symbol, cfg in INSTRUMENTS.items():
            # Use historical-specific token for indices (NIFTY50, BANKNIFTY, FINNIFTY, MIDCPNIFTY)
            # Falls back to regular angel_token for symbols without separate historical token (SENSEX, RELIANCE, HDFCBANK)
            angel_token = cfg.get("angel_historical_token") or cfg.get("angel_token")
            if not angel_token:
                logger.warning(f"No angel_token for {symbol}, skipping historical fetch")
                continue
            
            exchange, token = angel_token
            try:
                # Angel One getCandleData parameters:
                # exchange, symboltoken, interval, fromdate, todate
                # interval: "ONE_MINUTE", "FIVE_MINUTE", etc.
                candle_params = {
                    "exchange": exchange,
                    "symboltoken": token,
                    "interval": "ONE_MINUTE",
                    "fromdate": from_str,
                    "todate": to_str
                }
                # DEBUG: Log the exact parameters for NIFTY50
                if symbol == "NIFTY50":
                    logger.info(f"DEBUG NIFTY50 candle_params: {candle_params}")
                res = obj.getCandleData(candle_params)
                
                if res and res.get("status") and res.get("data"):
                    candles_data = res["data"]
                    cfg = INSTRUMENTS[symbol]
                    
                    real_candles = []
                    for c in candles_data:
                        # Angel One returns: [timestamp, open, high, low, close, volume]
                        # Timestamp is ISO 8601 string like '2026-09-02T09:15:00+05:30', NOT epoch int
                        if len(c) >= 6:
                            try:
                                # Parse ISO 8601 timestamp string to epoch
                                parsed_time = datetime.fromisoformat(c[0].replace('Z', '+00:00'))
                                ts = int(parsed_time.timestamp())
                            except Exception:
                                # Fallback: if already an int, use as-is
                                ts = int(c[0]) if isinstance(c[0], (int, float)) else 0
                            
                            if ts == 0:
                                continue
                            
                            # DEBUG: Print first candle timestamp details for each symbol
                            if not real_candles:
                                from datetime import timezone, timedelta
                                ist_tz = timezone(timedelta(hours=5, minutes=30))
                                utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                                ist_dt = datetime.fromtimestamp(ts, tz=ist_tz)
                                logger.info(
                                    f"DEBUG {symbol} FIRST CANDLE: "
                                    f"raw={c[0]} | parsed={parsed_time} | epoch={ts} | "
                                    f"UTC={utc_dt.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
                                    f"IST={ist_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                                )
                            
                            if ts == 0:
                                continue
                                
                            o = float(c[1])
                            h = float(c[2])
                            l = float(c[3])
                            c_val = float(c[4])
                            vol = int(c[5]) if c[5] else 100
                            
                            real_candles.append({
                                "time": ts,
                                "open": round(o, cfg["digits"]),
                                "high": round(h, cfg["digits"]),
                                "low": round(l, cfg["digits"]),
                                "close": round(c_val, cfg["digits"]),
                                "volume": max(vol, 10)
                            })
                    
                    if real_candles:
                        # DEBUG: Log last 3 RAW candles from Angel One for NIFTY50
                        if symbol == "NIFTY50" and len(real_candles) >= 1:
                            last_raw = candles_data[-3:] if len(candles_data) >= 3 else candles_data
                            for idx, c in enumerate(last_raw):
                                if len(c) >= 6:
                                    try:
                                        dt = datetime.fromisoformat(c[0].replace('Z', '+00:00'))
                                        ist_dt = datetime.fromtimestamp(int(dt.timestamp()), tz=timezone(timedelta(hours=5, minutes=30)))
                                        logger.info(f"DEBUG NIFTY50 RAW CANDLE[-{len(last_raw)-idx}]: raw_time={c[0]} | IST={ist_dt.strftime('%Y-%m-%d %H:%M:%S %Z')} | close={c[4]}")
                                    except Exception:
                                        logger.info(f"DEBUG NIFTY50 RAW CANDLE[-{len(last_raw)-idx}]: raw={c} | parse_failed=True")
                        
                        # MERGE: Preserve any live-captured candles that are newer than historical data
                        # AND fall within today's market hours (09:15-15:40 IST)
                        # This prevents phantom candles from closed-market hours being preserved
                        existing_candles = self.candle_cache.get(symbol, [])
                        if existing_candles:
                            # Find the latest timestamp in newly fetched historical data
                            hist_max_ts = max(c["time"] for c in real_candles)
                            # Find live-captured candles that are newer than historical data
                            newer_live_candles = [c for c in existing_candles if c["time"] > hist_max_ts]
                            # Filter to only keep candles within market hours (09:15-15:40 IST)
                            ist_tz = timezone(timedelta(hours=5, minutes=30))
                            market_open_ts = None
                            market_close_ts = None
                            # Calculate today's market open/close in UTC
                            now_utc = datetime.now(timezone.utc)
                            ist_now = now_utc + timedelta(hours=5, minutes=30)
                            if ist_now.weekday() <= 4:  # Mon-Fri
                                today_open = ist_now.replace(hour=9, minute=15, second=0, microsecond=0) - timedelta(hours=5, minutes=30)
                                today_close = ist_now.replace(hour=15, minute=40, second=0, microsecond=0) - timedelta(hours=5, minutes=30)
                                market_open_ts = int(today_open.timestamp())
                                market_close_ts = int(today_close.timestamp())
                            
                            if market_open_ts is not None and market_close_ts is not None:
                                newer_live_candles = [
                                    c for c in newer_live_candles 
                                    if market_open_ts <= c["time"] <= market_close_ts
                                ]
                            
                            if newer_live_candles:
                                logger.info(f"MERGE {symbol}: Preserving {len(newer_live_candles)} live-captured candles newer than historical data within market hours (newest historical={datetime.fromtimestamp(hist_max_ts, tz=timezone(timedelta(hours=5, minutes=30))).strftime('%H:%M:%S')}, newest live={datetime.fromtimestamp(max(c['time'] for c in newer_live_candles), tz=timezone(timedelta(hours=5, minutes=30))).strftime('%H:%M:%S')})")
                                # Merge: historical + newer live candles (already sorted by time)
                                merged = real_candles + newer_live_candles
                                # Ensure sorted by time
                                merged.sort(key=lambda x: x["time"])
                                real_candles = merged
                        
                        with self.lock:
                            self.candle_cache[symbol] = real_candles
                            last_c = real_candles[-1]
                            # DEBUG: Log the FINAL stored candle in cache for NIFTY50
                            if symbol == "NIFTY50":
                                ts = last_c["time"]
                                ist_dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=5, minutes=30)))
                                logger.info(f"DEBUG NIFTY50 STORED LAST CANDLE: epoch={ts} | IST={ist_dt.strftime('%Y-%m-%d %H:%M:%S %Z')} | close={last_c['close']}")
                            spread = cfg["spread"]
                            self.prices[symbol]["mid"] = last_c["close"]
                            self.prices[symbol]["bid"] = round(last_c["close"] - spread / 2, cfg["digits"])
                            self.prices[symbol]["ask"] = round(last_c["close"] + spread / 2, cfg["digits"])
                            self.prices[symbol]["high_24h"] = max(c["high"] for c in real_candles)
                            self.prices[symbol]["low_24h"] = min(c["low"] for c in real_candles)
                            self.prices[symbol]["change_24h"] = round(
                                ((real_candles[-1]["close"] - real_candles[0]["open"]) / real_candles[0]["open"]) * 100, 2
                            )
                            candles_fetched += len(real_candles)
                            logger.info(f"Fetched {len(real_candles)} real candles for {symbol} from Angel One")
                else:
                    # Log full response for debugging empty data (esp. indices NIFTY50, BANKNIFTY, FINNIFTY, MIDCPNIFTY)
                    logger.warning(
                        f"No historical data returned for {symbol} (token={token}, exchange={exchange}): "
                        f"status={res.get('status')}, message={res.get('message')}, "
                        f"data={res.get('data')}, raw={res}"
                    )
                    
            except Exception as e:
                logger.error(f"Error fetching historical data for {symbol}: {e}")
            
            # Small delay between API calls to avoid rate limiting (esp. for MIDCPNIFTY)
            time.sleep(0.3)
        
        if candles_fetched > 0:
            logger.info(f"Successfully loaded {candles_fetched} real historical candles from Angel One")
        else:
            logger.warning("No historical candles fetched from Angel One - charts will be empty until live ticks arrive")

    def _sync_live_prices(self):
        """Live price feed: Angel One 1s REST polling using shared session."""
        def angel_one_worker():
            import pyotp
            import os
            from SmartApi import SmartConnect
            
            ANGEL_MAP = {
                "NIFTY50":    ("NSE", "26000"),
                "BANKNIFTY":  ("NSE", "26009"),
                "FINNIFTY":   ("NSE", "26037"),
                "MIDCPNIFTY": ("NSE", "26074"),
                "RELIANCE":   ("NSE", "2885"),
                "HDFCBANK":   ("NSE", "1333"),
                "SENSEX":     ("BSE", "99919000"),
            }
            
            last_tick_time = time.time()
            
            # Build exchangeTokens and token_to_sym once
            exchangeTokens = {"NSE": [], "BSE": []}
            token_to_sym = {}
            for sym, (exch, token) in ANGEL_MAP.items():
                exchangeTokens[exch].append(token)
                token_to_sym[token] = sym
            
            while True:
                try:
                    # Watchdog: check if we haven't received ticks in 30 seconds
                    # If so, the session may have expired - trigger re-auth on next API call
                    if time.time() - last_tick_time > 30:
                        logger.warning("No tick received from Angel One in 30 seconds. Will re-authenticate on next API call.")
                        # Mark session as potentially stale - _get_angel_session will re-auth on next use
                        with self.angel_session_lock:
                            self.angel_session = None
                        last_tick_time = time.time()  # Reset to avoid repeated warnings
                    
                    obj = self._get_angel_session()
                    res = obj.getMarketData("FULL", exchangeTokens)
                    
                    if res and res.get("status") and res.get("data"):
                        fetched = res["data"].get("fetched", [])
                        with self.lock:
                            for item in fetched:
                                try:
                                    token = item.get("symbolToken")
                                    if token not in token_to_sym:
                                        continue
                                    sym = token_to_sym[token]
                                    if sym not in INSTRUMENTS:
                                        continue
                                    cfg = INSTRUMENTS[sym]
                                    
                                    last_p = item.get("ltp")
                                    if not last_p or last_p <= 0:
                                        continue
                                    
                                    spread = cfg["spread"]
                                    self.prices[sym]["mid"] = round(last_p, cfg["digits"])
                                    self.prices[sym]["bid"] = round(last_p - spread / 2, cfg["digits"])
                                    self.prices[sym]["ask"] = round(last_p + spread / 2, cfg["digits"])
                                    
                                    now_ts = int(time.time())
                                    # Update last tick timestamp for watchdog
                                    last_tick_time = now_ts
                                    
                                    # Update candle cache ONLY during market hours (including CAS)
                                    if is_indian_market_open():
                                        # Update candle cache
                                        if sym in self.candle_cache and len(self.candle_cache[sym]) > 0:
                                            last_c = self.candle_cache[sym][-1]
                                            if now_ts - last_c["time"] < 60: 
                                                last_c["close"] = round(last_p, cfg["digits"])
                                                last_c["high"] = round(max(last_c["high"], last_p), cfg["digits"])
                                                last_c["low"] = round(min(last_c["low"], last_p), cfg["digits"])
                                                # Persist updated candle to SQLite
                                                self._save_candle_to_db(sym, last_c)
                                            else:
                                                new_candle = {
                                                    "time": now_ts - (now_ts % 60),
                                                    "open": last_c["close"],
                                                    "high": max(last_c["close"], last_p),
                                                    "low": min(last_c["close"], last_p),
                                                    "close": last_p,
                                                    "volume": item.get("tradeVolume", 100)
                                                }
                                                self.candle_cache[sym].append(new_candle)
                                                # Persist new candle to SQLite
                                                self._save_candle_to_db(sym, new_candle)
                                                if len(self.candle_cache[sym]) > 1000:
                                                    self.candle_cache[sym].pop(0)
                                except Exception as item_e:
                                    logger.warning(f"Error processing individual Angel One item: {item_e}")
                                    continue  # Process next item, don't kill the whole feed
                    else:
                        logger.warning(f"Angel One API returned no data or error: {res}")
                except Exception as e:
                    logger.error(f"Angel One getMarketData exception: {e}")
                    # Session may be invalid - mark for re-auth on next iteration
                    with self.angel_session_lock:
                        self.angel_session = None
                time.sleep(1)

        def fetch_worker():
            """Periodic history refresher - re-fetch Angel One historical data using shared session."""
            while True:
                time.sleep(300)  # 5 minutes
                try:
                    self._fetch_angel_one_history_sync()
                except Exception as e:
                    logger.error(f"Periodic Angel One history refresh failed: {e}")

        threading.Thread(target=fetch_worker, daemon=True).start()
        threading.Thread(target=angel_one_worker, daemon=True).start()

    def tick(self, symbol: str = None) -> Dict[str, Any]:
        """Advance price ticks or return current live snapshot."""
        with self.lock:
            return {k: v.copy() for k, v in self.prices.items()}

    def get_candles(self, symbol: str, count: int = 500) -> List[Dict[str, Any]]:
        with self.lock:
            # Handle Options mathematically derived from underlying index
            if symbol.endswith("CE") or symbol.endswith("PE"):
                if "BANKNIFTY" in symbol:
                    underlying = "BANKNIFTY"
                elif "FINNIFTY" in symbol:
                    underlying = "FINNIFTY"
                elif "MIDCPNIFTY" in symbol:
                    underlying = "MIDCPNIFTY"
                elif "SENSEX" in symbol:
                    underlying = "SENSEX"
                else:
                    underlying = "NIFTY50"

                underlying_candles = self.candle_cache.get(underlying, [])[-count:]
                is_call = symbol.endswith("CE")
                
                option_candles = []
                for uc in underlying_candles:
                    base_close = calculate_option_price_live(symbol, uc["close"]) or 150.0
                    base_open = calculate_option_price_live(symbol, uc["open"]) or base_close
                    base_high = calculate_option_price_live(symbol, uc["high"] if is_call else uc["low"]) or max(base_open, base_close)
                    base_low = calculate_option_price_live(symbol, uc["low"] if is_call else uc["high"]) or min(base_open, base_close)
                    
                    option_candles.append({
                        "time": uc["time"],
                        "open": round(base_open, 2),
                        "high": round(max(base_high, base_open, base_close), 2),
                        "low": round(max(0.05, min(base_low, base_open, base_close)), 2),
                        "close": round(base_close, 2),
                        "volume": uc.get("volume", 200)
                    })
                return option_candles

            return self.candle_cache.get(symbol, [])[-count:]

    def get_all_prices(self) -> List[Dict[str, Any]]:
        self.tick()
        with self.lock:
            result = []
            for sym, cfg in INSTRUMENTS.items():
                p = self.prices[sym]
                result.append({
                    "symbol": sym,
                    "name": cfg["name"],
                    "category": cfg["category"],
                    "bid": p["bid"],
                    "ask": p["ask"],
                    "mid": p["mid"],
                    "spread_pips": round(cfg["spread"] / cfg["pip_size"], 1),
                    "digits": cfg["digits"],
                    "change_24h": p["change_24h"],
                    "high_24h": p["high_24h"],
                    "low_24h": p["low_24h"]
                })
            return result

    def get_prices(self) -> Dict[str, Dict[str, float]]:
        with self.lock:
            res = {k: v.copy() for k, v in self.prices.items()}
            return res

    def get_price(self, symbol: str) -> Dict[str, float]:
        with self.lock:
            if symbol in self.prices:
                return self.prices[symbol].copy()
            
            # If Option - derive from underlying if available
            if symbol.endswith("CE") or symbol.endswith("PE"):
                if "BANKNIFTY" in symbol:
                    underlying = "BANKNIFTY"
                elif "FINNIFTY" in symbol:
                    underlying = "FINNIFTY"
                elif "MIDCPNIFTY" in symbol:
                    underlying = "MIDCPNIFTY"
                elif "SENSEX" in symbol:
                    underlying = "SENSEX"
                else:
                    underlying = "NIFTY50"

                u_price = self.prices.get(underlying, {}).get("mid", 0.0)
                if u_price <= 0:
                    # No real data available for underlying
                    return {
                        "bid": 0.0,
                        "ask": 0.0,
                        "mid": 0.0,
                        "change_24h": 0.0,
                        "high_24h": 0.0,
                        "low_24h": 0.0
                    }
                
                opt_price = calculate_option_price_live(symbol, u_price) or 0.0
                if opt_price <= 0:
                    return {
                        "bid": 0.0,
                        "ask": 0.0,
                        "mid": 0.0,
                        "change_24h": 0.0,
                        "high_24h": 0.0,
                        "low_24h": 0.0
                    }
                return {
                    "bid": round(opt_price - 0.25, 2),
                    "ask": round(opt_price + 0.25, 2),
                    "mid": round(opt_price, 2),
                    "change_24h": 0.0,
                    "high_24h": 0.0,
                    "low_24h": 0.0
                }

            # Unknown symbol - return empty
            return {
                "bid": 0.0,
                "ask": 0.0,
                "mid": 0.0,
                "change_24h": 0.0,
                "high_24h": 0.0,
                "low_24h": 0.0
            }

    def calculate_pnl(self, symbol: str, order_type: str, lots: float, open_price: float) -> tuple[float, float, float]:
        is_option = symbol.endswith("CE") or symbol.endswith("PE")
        
        if not is_option and symbol not in INSTRUMENTS:
            return 0.0, open_price, 0.0
            
        if is_option:
            if "BANKNIFTY" in symbol:
                underlying = "BANKNIFTY"
            elif "FINNIFTY" in symbol:
                underlying = "FINNIFTY"
            elif "MIDCPNIFTY" in symbol:
                underlying = "MIDCPNIFTY"
            elif "SENSEX" in symbol:
                underlying = "SENSEX"
            elif "NIFTY" in symbol:
                underlying = "NIFTY50"
            else:
                underlying = "NIFTY50"
            underlying_spot = self.prices.get(underlying, {}).get("mid", 0.0)
            opt_price = calculate_option_price_live(symbol, underlying_spot)
            if opt_price is None: opt_price = open_price
            
            # Options spread logic
            bid = opt_price - 0.25
            ask = opt_price + 0.25
            
            current_exit_price = bid if order_type == "BUY" else ask
            diff = current_exit_price - open_price if order_type == "BUY" else open_price - current_exit_price
            
            pips = diff
            pnl = diff * lots
            
            turnover = (open_price + current_exit_price) * lots
            stt_and_charges = turnover * 0.000125
            total_fees = stt_and_charges + 40.0
            pnl -= total_fees
            return round(pnl, 2), round(current_exit_price, 2), round(pips, 1)

        # Standard instruments
        cfg = INSTRUMENTS[symbol]
        cur_p = self.prices[symbol]
        
        if order_type == "BUY":
            current_exit_price = cur_p["bid"]
            diff = current_exit_price - open_price
        else:
            current_exit_price = cur_p["ask"]
            diff = open_price - current_exit_price

        pips = diff / cfg["pip_size"]
        pnl = diff * lots
        
        turnover = (open_price + current_exit_price) * lots
        stt_and_charges = turnover * 0.000125
        total_fees = stt_and_charges + 40.0
        pnl -= total_fees

        return round(pnl, 2), round(current_exit_price, cfg["digits"]), round(pips, 1)

market_engine = MarketDataEngine()
