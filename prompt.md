/fleet
please fix the following issue
2026-03-30 17:24:31 | INFO     | __main__ | Initializing Polymarket Arbitrage Bot...
2026-03-30 17:24:31 | INFO     | security | Security context initialized
2026-03-30 17:24:31 | INFO     | __main__ | [OK] Security context initialized
2026-03-30 17:24:31 | INFO     | executor | Initializing CLOB client...
2026-03-30 17:24:31 | INFO     | httpx | HTTP Request: POST https://clob.polymarket.com/auth/api-key "HTTP/2 400 Bad Request"
2026-03-30 17:24:32 | INFO     | httpx | HTTP Request: GET https://clob.polymarket.com/auth/derive-api-key "HTTP/2 200 OK"
2026-03-30 17:24:32 | INFO     | executor | CLOB client initialized successfully
2026-03-30 17:24:32 | INFO     | security | SECURITY: EXECUTOR_INITIALIZED - {'chain_id': 137}
2026-03-30 17:24:32 | INFO     | __main__ | [OK] Zero-fee executor initialized
2026-03-30 17:24:32 | INFO     | __main__ | [OK] Binance oracle initialized
2026-03-30 17:24:32 | INFO     | sniper | PolymarketSniper initialized
2026-03-30 17:24:32 | INFO     | __main__ | [OK] Polymarket sniper initialized
2026-03-30 17:24:32 | INFO     | __main__ | [OK] Market scanner initialized
2026-03-30 17:24:32 | INFO     | __main__ | [OK] Arbitrage engine initialized
2026-03-30 17:24:32 | INFO     | ta_fallback | No existing heuristics file, starting fresh
2026-03-30 17:24:32 | INFO     | __main__ | [OK] Technical analyzer initialized
2026-03-30 17:24:32 | INFO     | terminal_velocity | TerminalVelocityController initialized
2026-03-30 17:24:32 | INFO     | terminal_velocity | ⚡ Terminal Velocity Controller STARTED
--- Logging error ---
Traceback (most recent call last):
  File "C:\Python313\Lib\logging\__init__.py", line 1153, in emit
    stream.write(msg + self.terminator)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Python313\Lib\encodings\cp1252.py", line 19, in encode
    return codecs.charmap_encode(input,self.errors,encoding_table)[0]
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
UnicodeEncodeError: 'charmap' codec can't encode character '\u26a1' in position 53: character maps to <undefined>
Call stack:
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 700, in <module>
    asyncio.run(main())
  File "C:\Python313\Lib\asyncio\runners.py", line 194, in run
    return runner.run(main)
  File "C:\Python313\Lib\asyncio\runners.py", line 118, in run
    return self._loop.run_until_complete(task)
  File "C:\Python313\Lib\asyncio\base_events.py", line 707, in run_until_complete
    self.run_forever()
  File "C:\Python313\Lib\asyncio\base_events.py", line 678, in run_forever
    self._run_once()
  File "C:\Python313\Lib\asyncio\base_events.py", line 2033, in _run_once
    handle._run()
  File "C:\Python313\Lib\asyncio\events.py", line 89, in _run
    self._context.run(self._callback, *self._args)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 682, in main
    await orchestrator.initialize()
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 163, in initialize
    await self._terminal_velocity.start()
  File "C:\Users\yeet638\Desktop\utilities\polym\src\terminal_velocity.py", line 156, in start
    logger.info("⚡ Terminal Velocity Controller STARTED")
Message: '⚡ Terminal Velocity Controller STARTED'
Arguments: ()
2026-03-30 17:24:32 | INFO     | __main__ | [OK] ⚡ Terminal Velocity Controller initialized
--- Logging error ---
Traceback (most recent call last):
  File "C:\Python313\Lib\logging\__init__.py", line 1153, in emit
    stream.write(msg + self.terminator)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Python313\Lib\encodings\cp1252.py", line 19, in encode
    return codecs.charmap_encode(input,self.errors,encoding_table)[0]
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
UnicodeEncodeError: 'charmap' codec can't encode character '\u26a1' in position 49: character maps to <undefined>
Call stack:
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 700, in <module>
    asyncio.run(main())
  File "C:\Python313\Lib\asyncio\runners.py", line 194, in run
    return runner.run(main)
  File "C:\Python313\Lib\asyncio\runners.py", line 118, in run
    return self._loop.run_until_complete(task)
  File "C:\Python313\Lib\asyncio\base_events.py", line 707, in run_until_complete
    self.run_forever()
  File "C:\Python313\Lib\asyncio\base_events.py", line 678, in run_forever
    self._run_once()
  File "C:\Python313\Lib\asyncio\base_events.py", line 2033, in _run_once
    handle._run()
  File "C:\Python313\Lib\asyncio\events.py", line 89, in _run
    self._context.run(self._callback, *self._args)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 682, in main
    await orchestrator.initialize()
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 164, in initialize
    logger.info("[OK] ⚡ Terminal Velocity Controller initialized")
Message: '[OK] ⚡ Terminal Velocity Controller initialized'
Arguments: ()
2026-03-30 17:24:32 | INFO     | __main__ | All components initialized successfully

------------------------------------------------------------
  CONFIGURATION SUMMARY
------------------------------------------------------------
  Mode:              DRY_RUN
  Starting Capital:  $100.00
  Trade Allocation:  5.0%
  Max Entry Price:   $0.99
  Time Threshold:    1s
  Daily Loss Limit:  $10.00
------------------------------------------------------------

2026-03-30 17:24:32 | INFO     | __main__ | Bot started - press Ctrl+C to stop
2026-03-30 17:24:32 | INFO     | __main__ | Running independent loops for: BTC, ETH

🚀 Bot is running...

2026-03-30 17:24:32 | INFO     | oracle | Connecting to Binance WebSocket: wss://stream.binance.com:9443/ws/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker/xrpusdt@ticker/dogeusdt@ticker/bnbusdt@ticker
2026-03-30 17:24:32 | INFO     | sniper | Starting sniper main loop
2026-03-30 17:24:32 | INFO     | sniper | Connection state: disconnected -> connecting
2026-03-30 17:24:32 | INFO     | ta_fallback | Starting background TA analysis
2026-03-30 17:24:32 | INFO     | ta_fallback | Background TA analysis stopped
2026-03-30 17:24:32 | INFO     | __main__ | [BTC] Starting independent trading loop (scan every 30.0s)
2026-03-30 17:24:32 | INFO     | __main__ | [ETH] Starting independent trading loop (scan every 30.0s)
2026-03-30 17:24:33 | INFO     | sniper | Connection state: connecting -> connected
2026-03-30 17:24:33 | INFO     | sniper | Connected to Polymarket WebSocket
2026-03-30 17:24:33 | INFO     | scanner | Scan complete: found 7 valid 5-min markets (BNB: 1, BTC: 1, DOGE: 1, ETH: 1, HYPE: 1, SOL: 1, XRP: 1)
2026-03-30 17:24:33 | INFO     | sniper | Subscribed to 2 tokens
2026-03-30 17:24:33 | INFO     | scanner | Scan complete: found 7 valid 5-min markets (BNB: 1, BTC: 1, DOGE: 1, ETH: 1, HYPE: 1, SOL: 1, XRP: 1)
2026-03-30 17:24:33 | INFO     | sniper | Subscribed to 2 tokens
2026-03-30 17:24:33 | ERROR    | oracle | WebSocket error: server rejected WebSocket connection: HTTP 404
2026-03-30 17:24:33 | INFO     | oracle | Reconnecting in 1s (attempt 1)
2026-03-30 17:24:33 | WARNING  | sniper | Invalid JSON message: Expecting value: line 1 column 1 (char 0)
2026-03-30 17:24:34 | INFO     | oracle | Connecting to Binance WebSocket: wss://stream.binance.com:9443/ws/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker/xrpusdt@ticker/dogeusdt@ticker/bnbusdt@ticker
2026-03-30 17:24:35 | ERROR    | oracle | WebSocket error: server rejected WebSocket connection: HTTP 404
2026-03-30 17:24:35 | INFO     | oracle | Reconnecting in 2s (attempt 2)
2026-03-30 17:24:37 | INFO     | oracle | Connecting to Binance WebSocket: wss://stream.binance.com:9443/ws/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker/xrpusdt@ticker/dogeusdt@ticker/bnbusdt@ticker
2026-03-30 17:24:38 | ERROR    | oracle | WebSocket error: server rejected WebSocket connection: HTTP 404
2026-03-30 17:24:38 | INFO     | oracle | Reconnecting in 4s (attempt 3)
2026-03-30 17:24:42 | INFO     | oracle | Connecting to Binance WebSocket: wss://stream.binance.com:9443/ws/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker/xrpusdt@ticker/dogeusdt@ticker/bnbusdt@ticker
2026-03-30 17:24:42 | ERROR    | oracle | WebSocket error: server rejected WebSocket connection: HTTP 404
2026-03-30 17:24:42 | INFO     | oracle | Reconnecting in 8s (attempt 4)
2026-03-30 17:24:50 | INFO     | oracle | Connecting to Binance WebSocket: wss://stream.binance.com:9443/ws/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker/xrpusdt@ticker/dogeusdt@ticker/bnbusdt@ticker
2026-03-30 17:24:51 | ERROR    | oracle | WebSocket error: server rejected WebSocket connection: HTTP 404
2026-03-30 17:24:51 | INFO     | oracle | Reconnecting in 16s (attempt 5)
2026-03-30 17:25:04 | INFO     | scanner | 📡 Phase transition: DISCOVERY → CRUISING (closest: BNB @ 296s)
--- Logging error ---
Traceback (most recent call last):
  File "C:\Python313\Lib\logging\__init__.py", line 1153, in emit
    stream.write(msg + self.terminator)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Python313\Lib\encodings\cp1252.py", line 19, in encode
    return codecs.charmap_encode(input,self.errors,encoding_table)[0]
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f4e1' in position 43: character maps to <undefined>
Call stack:
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 700, in <module>
    asyncio.run(main())
  File "C:\Python313\Lib\asyncio\runners.py", line 194, in run
    return runner.run(main)
  File "C:\Python313\Lib\asyncio\runners.py", line 118, in run
    return self._loop.run_until_complete(task)
  File "C:\Python313\Lib\asyncio\base_events.py", line 707, in run_until_complete
    self.run_forever()
  File "C:\Python313\Lib\asyncio\base_events.py", line 678, in run_forever
    self._run_once()
  File "C:\Python313\Lib\asyncio\base_events.py", line 2033, in _run_once
    handle._run()
  File "C:\Python313\Lib\asyncio\events.py", line 89, in _run
    self._context.run(self._callback, *self._args)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 316, in _asset_trading_loop
    markets = await self._scan_markets_for_asset(asset)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 433, in _scan_markets_for_asset
    all_markets = await self._scanner.scan_markets()
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 863, in scan_markets
    self.update_polling_mode(markets)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 336, in update_polling_mode
    self._log_phase_transition(self._previous_mode, new_mode, markets)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 356, in _log_phase_transition
    logger.info(
Message: '📡 Phase transition: DISCOVERY → CRUISING (closest: BNB @ 296s)'
Arguments: ()
2026-03-30 17:25:04 | INFO     | scanner | 🛫 CRUISING ALTITUDE: Enforcing 30s polling intervals (2 scans/min per currency)
--- Logging error ---
Traceback (most recent call last):
  File "C:\Python313\Lib\logging\__init__.py", line 1153, in emit
    stream.write(msg + self.terminator)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Python313\Lib\encodings\cp1252.py", line 19, in encode
    return codecs.charmap_encode(input,self.errors,encoding_table)[0]
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f6eb' in position 43: character maps to <undefined>
Call stack:
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 700, in <module>
    asyncio.run(main())
  File "C:\Python313\Lib\asyncio\runners.py", line 194, in run
    return runner.run(main)
  File "C:\Python313\Lib\asyncio\runners.py", line 118, in run
    return self._loop.run_until_complete(task)
  File "C:\Python313\Lib\asyncio\base_events.py", line 707, in run_until_complete
    self.run_forever()
  File "C:\Python313\Lib\asyncio\base_events.py", line 678, in run_forever
    self._run_once()
  File "C:\Python313\Lib\asyncio\base_events.py", line 2033, in _run_once
    handle._run()
  File "C:\Python313\Lib\asyncio\events.py", line 89, in _run
    self._context.run(self._callback, *self._args)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 316, in _asset_trading_loop
    markets = await self._scan_markets_for_asset(asset)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 433, in _scan_markets_for_asset
    all_markets = await self._scanner.scan_markets()
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 863, in scan_markets
    self.update_polling_mode(markets)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 336, in update_polling_mode
    self._log_phase_transition(self._previous_mode, new_mode, markets)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 361, in _log_phase_transition
    logger.info(
Message: '🛫 CRUISING ALTITUDE: Enforcing 30s polling intervals (2 scans/min per currency)'
Arguments: ()
2026-03-30 17:25:04 | INFO     | scanner | Scan complete: found 7 valid 5-min markets (BNB: 1, BTC: 1, DOGE: 1, ETH: 1, HYPE: 1, SOL: 1, XRP: 1)
2026-03-30 17:25:04 | INFO     | scanner | 📡 Phase transition: DISCOVERY → CRUISING (closest: BNB @ 296s)
--- Logging error ---
Traceback (most recent call last):
  File "C:\Python313\Lib\logging\__init__.py", line 1153, in emit
    stream.write(msg + self.terminator)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Python313\Lib\encodings\cp1252.py", line 19, in encode
    return codecs.charmap_encode(input,self.errors,encoding_table)[0]
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f4e1' in position 43: character maps to <undefined>
Call stack:
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 700, in <module>
    asyncio.run(main())
  File "C:\Python313\Lib\asyncio\runners.py", line 194, in run
    return runner.run(main)
  File "C:\Python313\Lib\asyncio\runners.py", line 118, in run
    return self._loop.run_until_complete(task)
  File "C:\Python313\Lib\asyncio\base_events.py", line 707, in run_until_complete
    self.run_forever()
  File "C:\Python313\Lib\asyncio\base_events.py", line 678, in run_forever
    self._run_once()
  File "C:\Python313\Lib\asyncio\base_events.py", line 2033, in _run_once
    handle._run()
  File "C:\Python313\Lib\asyncio\events.py", line 89, in _run
    self._context.run(self._callback, *self._args)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 316, in _asset_trading_loop
    markets = await self._scan_markets_for_asset(asset)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 433, in _scan_markets_for_asset
    all_markets = await self._scanner.scan_markets()
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 863, in scan_markets
    self.update_polling_mode(markets)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 336, in update_polling_mode
    self._log_phase_transition(self._previous_mode, new_mode, markets)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 356, in _log_phase_transition
    logger.info(
Message: '📡 Phase transition: DISCOVERY → CRUISING (closest: BNB @ 296s)'
Arguments: ()
2026-03-30 17:25:04 | INFO     | scanner | 🛫 CRUISING ALTITUDE: Enforcing 30s polling intervals (2 scans/min per currency)
--- Logging error ---
Traceback (most recent call last):
  File "C:\Python313\Lib\logging\__init__.py", line 1153, in emit
    stream.write(msg + self.terminator)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Python313\Lib\encodings\cp1252.py", line 19, in encode
    return codecs.charmap_encode(input,self.errors,encoding_table)[0]
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f6eb' in position 43: character maps to <undefined>
Call stack:
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 700, in <module>
    asyncio.run(main())
  File "C:\Python313\Lib\asyncio\runners.py", line 194, in run
    return runner.run(main)
  File "C:\Python313\Lib\asyncio\runners.py", line 118, in run
    return self._loop.run_until_complete(task)
  File "C:\Python313\Lib\asyncio\base_events.py", line 707, in run_until_complete
    self.run_forever()
  File "C:\Python313\Lib\asyncio\base_events.py", line 678, in run_forever
    self._run_once()
  File "C:\Python313\Lib\asyncio\base_events.py", line 2033, in _run_once
    handle._run()
  File "C:\Python313\Lib\asyncio\events.py", line 89, in _run
    self._context.run(self._callback, *self._args)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 316, in _asset_trading_loop
    markets = await self._scan_markets_for_asset(asset)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\main.py", line 433, in _scan_markets_for_asset
    all_markets = await self._scanner.scan_markets()
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 863, in scan_markets
    self.update_polling_mode(markets)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 336, in update_polling_mode
    self._log_phase_transition(self._previous_mode, new_mode, markets)
  File "C:\Users\yeet638\Desktop\utilities\polym\src\scanner.py", line 361, in _log_phase_transition
    logger.info(
Message: '🛫 CRUISING ALTITUDE: Enforcing 30s polling intervals (2 scans/min per currency)'
Arguments: ()
2026-03-30 17:25:04 | INFO     | scanner | Scan complete: found 7 valid 5-min markets (BNB: 1, BTC: 1, DOGE: 1, ETH: 1, HYPE: 1, SOL: 1, XRP: 1)
2026-03-30 17:25:07 | INFO     | oracle | Connecting to Binance WebSocket: wss://stream.binance.com:9443/ws/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker/xrpusdt@ticker/dogeusdt@ticker/bnbusdt@ticker
2026-03-30 17:25:08 | ERROR    | oracle | WebSocket error: server rejected WebSocket connection: HTTP 404
2026-03-30 17:25:08 | INFO     | oracle | Reconnecting in 32s (attempt 6)
/fleet scan through whole code base + define possible bugs + fix + refactor code base

/fleet define possible optimization  + implementation

/fleet test & check all current functionalities to ensure it runs without issues

/fleet You should do detailed planning and deep research before starting the action. Please make sure all the code are working properly and bug free, keep codes optimised. Make sure the overall deployment is safe. update readme to indicate current functionalities