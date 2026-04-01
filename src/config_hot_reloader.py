"""
Configuration Hot-Reloader.

Phase 2 - Task 96: Reload trading parameters via SIGHUP or file watch.

Educational purpose only - paper trading simulation.
"""

import asyncio
import json
import logging
import signal
import hashlib
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent

logger = logging.getLogger(__name__)


@dataclass
class ConfigChange:
    """Records a configuration change."""
    key: str
    old_value: Any
    new_value: Any
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = "file"  # "file" or "signal"


class ConfigFileHandler(FileSystemEventHandler):
    """Watchdog handler for config file changes."""
    
    def __init__(self, config_reloader: 'ConfigHotReloader'):
        self.config_reloader = config_reloader
        self._last_hash: str = ""
    
    def on_modified(self, event):
        if event.is_directory:
            return
        
        if Path(event.src_path).name == self.config_reloader.config_file.name:
            # Debounce: check if file actually changed
            try:
                current_hash = self.config_reloader._get_file_hash()
                if current_hash != self._last_hash:
                    self._last_hash = current_hash
                    asyncio.create_task(
                        self.config_reloader._reload_from_file()
                    )
            except Exception as e:
                logger.error(f"Error handling config change: {e}")


class ConfigHotReloader:
    """
    Hot-reloads configuration without restart.
    
    Features:
    - File watch via watchdog
    - SIGHUP signal handling (Unix)
    - Change callbacks
    - Change history tracking
    - Validation before apply
    """
    
    def __init__(
        self,
        config_file: str = "config.json",
        on_reload: Optional[Callable[[Dict], None]] = None,
        validate_config: Optional[Callable[[Dict], bool]] = None,
    ):
        """
        Initialize hot-reloader.
        
        Args:
            config_file: Path to configuration file
            on_reload: Callback when config reloaded
            validate_config: Validation function
        """
        self.config_file = Path(config_file)
        self.on_reload = on_reload
        self.validate_config = validate_config
        
        self._config: Dict[str, Any] = {}
        self._change_history: List[ConfigChange] = []
        self._observers: List[Observer] = []
        self._running = False
        
        # Load initial config
        if self.config_file.exists():
            self._load_config()
    
    def _load_config(self) -> Dict:
        """Load configuration from file."""
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config file: {e}")
            return {}
        except FileNotFoundError:
            logger.warning(f"Config file not found: {self.config_file}")
            return {}
    
    def _get_file_hash(self) -> str:
        """Get MD5 hash of config file."""
        try:
            content = self.config_file.read_bytes()
            return hashlib.md5(content).hexdigest()
        except FileNotFoundError:
            return ""
    
    async def _reload_from_file(self) -> bool:
        """Reload configuration from file."""
        logger.info(f"Reloading config from {self.config_file}")
        
        new_config = self._load_config()
        
        if not new_config:
            logger.warning("Empty or invalid config, keeping current")
            return False
        
        return await self._apply_config(new_config, source="file")
    
    async def _apply_config(
        self,
        new_config: Dict,
        source: str = "file",
    ) -> bool:
        """Apply new configuration."""
        # Validate
        if self.validate_config:
            try:
                if not self.validate_config(new_config):
                    logger.error("Config validation failed")
                    return False
            except Exception as e:
                logger.error(f"Validation error: {e}")
                return False
        
        # Track changes
        for key, new_value in new_config.items():
            old_value = self._config.get(key)
            if old_value != new_value:
                change = ConfigChange(
                    key=key,
                    old_value=old_value,
                    new_value=new_value,
                    source=source,
                )
                self._change_history.append(change)
                logger.info(f"Config changed: {key}: {old_value} -> {new_value}")
        
        # Apply
        self._config = new_config
        
        # Callback
        if self.on_reload:
            try:
                if asyncio.iscoroutinefunction(self.on_reload):
                    await self.on_reload(new_config)
                else:
                    self.on_reload(new_config)
            except Exception as e:
                logger.error(f"Reload callback error: {e}")
        
        logger.info("Config reload complete")
        return True
    
    def _setup_signal_handler(self) -> None:
        """Setup SIGHUP handler for Unix systems."""
        if sys.platform == 'win32':
            logger.info("SIGHUP not available on Windows")
            return
        
        def sighup_handler(signum, frame):
            logger.info("SIGHUP received, reloading config")
            asyncio.create_task(self._reload_from_file())
        
        signal.signal(signal.SIGHUP, sighup_handler)
        logger.info("SIGHUP handler registered")
    
    async def start_watching(self) -> None:
        """Start watching config file for changes."""
        if self._running:
            return
        
        self._running = True
        
        # Setup signal handler
        self._setup_signal_handler()
        
        # Setup file watcher
        if self.config_file.parent.exists():
            handler = ConfigFileHandler(self)
            handler._last_hash = self._get_file_hash()
            
            observer = Observer()
            observer.schedule(
                handler,
                str(self.config_file.parent),
                recursive=False,
            )
            observer.start()
            self._observers.append(observer)
            
            logger.info(f"Watching config file: {self.config_file}")
    
    async def stop_watching(self) -> None:
        """Stop watching config file."""
        self._running = False
        
        for observer in self._observers:
            observer.stop()
            observer.join(timeout=2.0)
        
        self._observers.clear()
        logger.info("Config watcher stopped")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self._config.get(key, default)
    
    def get_all(self) -> Dict:
        """Get all configuration values."""
        return dict(self._config)
    
    def set(self, key: str, value: Any) -> None:
        """Set configuration value (in-memory only)."""
        old_value = self._config.get(key)
        self._config[key] = value
        
        change = ConfigChange(
            key=key,
            old_value=old_value,
            new_value=value,
            source="manual",
        )
        self._change_history.append(change)
    
    async def save(self) -> bool:
        """Save current config to file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self._config, f, indent=2)
            logger.info(f"Config saved to {self.config_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False
    
    def get_change_history(self, limit: int = 100) -> List[ConfigChange]:
        """Get recent configuration changes."""
        return self._change_history[-limit:]
    
    async def force_reload(self) -> bool:
        """Force reload from file."""
        return await self._reload_from_file()


class TradingConfig:
    """
    Trading-specific configuration wrapper.
    
    Provides typed access to common trading parameters.
    """
    
    def __init__(self, reloader: ConfigHotReloader):
        self._reloader = reloader
    
    @property
    def max_position_size(self) -> float:
        return self._reloader.get('max_position_size', 10000.0)
    
    @property
    def max_drawdown_pct(self) -> float:
        return self._reloader.get('max_drawdown_pct', 0.10)
    
    @property
    def obi_threshold(self) -> float:
        return self._reloader.get('obi_threshold', 0.3)
    
    @property
    def latency_ms(self) -> int:
        return self._reloader.get('latency_ms', 50)
    
    @property
    def enabled_symbols(self) -> List[str]:
        return self._reloader.get('enabled_symbols', ['BTC', 'ETH'])
    
    @property
    def risk_multiplier(self) -> float:
        return self._reloader.get('risk_multiplier', 1.0)
    
    @property
    def paper_trading(self) -> bool:
        return self._reloader.get('paper_trading', True)


def create_config_reloader(
    config_file: str = "config.json",
    on_reload: Optional[Callable] = None,
) -> ConfigHotReloader:
    """Create and return a ConfigHotReloader instance."""
    return ConfigHotReloader(
        config_file=config_file,
        on_reload=on_reload,
    )


def create_default_config(config_file: str = "config.json") -> None:
    """Create a default configuration file."""
    default_config = {
        "max_position_size": 10000.0,
        "max_drawdown_pct": 0.10,
        "obi_threshold": 0.3,
        "latency_ms": 50,
        "enabled_symbols": ["BTC", "ETH", "SOL"],
        "risk_multiplier": 1.0,
        "paper_trading": True,
        "log_level": "INFO",
        "redis_url": "redis://localhost:6379",
        "database_url": "postgresql://polym:polym_dev@localhost:5432/polym",
    }
    
    with open(config_file, 'w') as f:
        json.dump(default_config, f, indent=2)
    
    logger.info(f"Default config created: {config_file}")
