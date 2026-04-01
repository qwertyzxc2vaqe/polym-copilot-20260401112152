"""
Database Archiver - Parquet Export for Historical Data.

Phase 2 - Task 97: Archive PostgreSQL data to Parquet for long-term storage.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import json

logger = logging.getLogger(__name__)

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    PYARROW_AVAILABLE = True
except ImportError:
    PYARROW_AVAILABLE = False
    logger.warning("pyarrow not available, parquet export disabled")

try:
    import asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False


@dataclass
class ArchiveJob:
    """Represents an archive job."""
    job_id: str
    table_name: str
    start_date: datetime
    end_date: datetime
    output_path: str
    rows_archived: int = 0
    status: str = "pending"  # pending, running, completed, failed
    error_message: str = ""


class DatabaseArchiver:
    """
    Archives database data to Parquet files.
    
    Features:
    - Async PostgreSQL queries via asyncpg
    - Columnar Parquet export via PyArrow
    - Date-partitioned archives
    - Compression support
    - Archive metadata tracking
    """
    
    DEFAULT_BATCH_SIZE = 10000
    
    def __init__(
        self,
        database_url: str = "postgresql://polym:polym_dev@localhost:5432/polym",
        archive_dir: str = "data/archives",
        compression: str = "snappy",
    ):
        """
        Initialize database archiver.
        
        Args:
            database_url: PostgreSQL connection URL
            archive_dir: Directory for archive files
            compression: Parquet compression (snappy, gzip, lz4)
        """
        if not PYARROW_AVAILABLE:
            raise ImportError("pyarrow is required for database archiving")
        
        self.database_url = database_url
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.compression = compression
        
        self._pool: Optional[asyncpg.Pool] = None
        self._archive_history: List[ArchiveJob] = []
    
    async def connect(self) -> bool:
        """Establish database connection pool."""
        if not ASYNCPG_AVAILABLE:
            logger.error("asyncpg not available")
            return False
        
        try:
            self._pool = await asyncpg.create_pool(
                self.database_url,
                min_size=1,
                max_size=5,
            )
            logger.info("Database connection pool established")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Close database connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
    
    async def archive_table(
        self,
        table_name: str,
        start_date: datetime,
        end_date: datetime,
        timestamp_column: str = "timestamp",
        batch_size: int = None,
    ) -> ArchiveJob:
        """
        Archive a table's data to Parquet.
        
        Args:
            table_name: Source table name
            start_date: Start of archive range
            end_date: End of archive range
            timestamp_column: Column to filter by
            batch_size: Rows per batch
        
        Returns:
            ArchiveJob with results
        """
        batch_size = batch_size or self.DEFAULT_BATCH_SIZE
        
        # Create job
        job = ArchiveJob(
            job_id=f"archive_{table_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            table_name=table_name,
            start_date=start_date,
            end_date=end_date,
            output_path="",
            status="running",
        )
        self._archive_history.append(job)
        
        try:
            # Build output path
            date_str = start_date.strftime('%Y%m%d')
            output_file = self.archive_dir / f"{table_name}_{date_str}.parquet"
            job.output_path = str(output_file)
            
            # Query and export
            if self._pool:
                rows = await self._fetch_data(
                    table_name=table_name,
                    start_date=start_date,
                    end_date=end_date,
                    timestamp_column=timestamp_column,
                )
                
                if rows:
                    self._write_parquet(rows, output_file)
                    job.rows_archived = len(rows)
                    job.status = "completed"
                    logger.info(
                        f"Archived {len(rows)} rows from {table_name} "
                        f"to {output_file}"
                    )
                else:
                    job.status = "completed"
                    job.rows_archived = 0
                    logger.info(f"No data to archive for {table_name}")
            else:
                raise RuntimeError("Database not connected")
        
        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            logger.error(f"Archive failed: {e}")
        
        return job
    
    async def _fetch_data(
        self,
        table_name: str,
        start_date: datetime,
        end_date: datetime,
        timestamp_column: str,
    ) -> List[Dict]:
        """Fetch data from database."""
        query = f"""
            SELECT * FROM {table_name}
            WHERE {timestamp_column} >= $1 AND {timestamp_column} < $2
            ORDER BY {timestamp_column}
        """
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, start_date, end_date)
            return [dict(row) for row in rows]
    
    def _write_parquet(
        self,
        rows: List[Dict],
        output_path: Path,
    ) -> None:
        """Write rows to Parquet file."""
        if not rows:
            return
        
        # Convert to columnar format
        columns = {}
        for key in rows[0].keys():
            columns[key] = [row.get(key) for row in rows]
        
        # Create PyArrow table
        table = pa.table(columns)
        
        # Write Parquet
        pq.write_table(
            table,
            str(output_path),
            compression=self.compression,
        )
    
    async def archive_tick_data(
        self,
        days_to_keep: int = 30,
    ) -> List[ArchiveJob]:
        """
        Archive tick data older than specified days.
        
        Args:
            days_to_keep: Days of data to keep in database
        
        Returns:
            List of archive jobs
        """
        jobs = []
        
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        start_date = cutoff_date - timedelta(days=7)  # Archive 7-day chunks
        
        # Archive ticks table
        job = await self.archive_table(
            table_name="ticks",
            start_date=start_date,
            end_date=cutoff_date,
            timestamp_column="timestamp",
        )
        jobs.append(job)
        
        return jobs
    
    async def delete_archived_data(
        self,
        table_name: str,
        before_date: datetime,
        timestamp_column: str = "timestamp",
    ) -> int:
        """
        Delete data that has been archived.
        
        Args:
            table_name: Table to delete from
            before_date: Delete data before this date
            timestamp_column: Timestamp column
        
        Returns:
            Number of rows deleted
        """
        if not self._pool:
            return 0
        
        query = f"""
            DELETE FROM {table_name}
            WHERE {timestamp_column} < $1
        """
        
        async with self._pool.acquire() as conn:
            result = await conn.execute(query, before_date)
            deleted = int(result.split()[-1])
            logger.info(f"Deleted {deleted} rows from {table_name}")
            return deleted
    
    def read_parquet(
        self,
        file_path: str,
    ) -> Optional[List[Dict]]:
        """
        Read data from a Parquet archive.
        
        Args:
            file_path: Path to Parquet file
        
        Returns:
            List of row dictionaries
        """
        try:
            table = pq.read_table(file_path)
            return table.to_pylist()
        except Exception as e:
            logger.error(f"Failed to read parquet: {e}")
            return None
    
    def list_archives(self) -> List[Dict]:
        """List all archive files."""
        archives = []
        
        for file_path in self.archive_dir.glob("*.parquet"):
            stat = file_path.stat()
            archives.append({
                'filename': file_path.name,
                'path': str(file_path),
                'size_bytes': stat.st_size,
                'created': datetime.fromtimestamp(stat.st_ctime),
                'modified': datetime.fromtimestamp(stat.st_mtime),
            })
        
        return sorted(archives, key=lambda x: x['created'], reverse=True)
    
    def get_archive_history(self, limit: int = 100) -> List[ArchiveJob]:
        """Get archive job history."""
        return self._archive_history[-limit:]


class ErrorStateRecovery:
    """
    Error State Recovery (Task 98).
    
    Manages recovery from error states and maintains
    system state for restart recovery.
    """
    
    def __init__(
        self,
        state_file: str = "data/error_state.json",
    ):
        """
        Initialize error state recovery.
        
        Args:
            state_file: Path to state persistence file
        """
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        
        self._error_count: int = 0
        self._last_error: Optional[str] = None
        self._recovery_attempts: int = 0
        self._state: Dict = {}
    
    def save_state(self, state: Dict) -> None:
        """Save current system state."""
        self._state = state
        state_data = {
            'state': state,
            'error_count': self._error_count,
            'last_error': self._last_error,
            'recovery_attempts': self._recovery_attempts,
            'saved_at': datetime.now().isoformat(),
        }
        
        with open(self.state_file, 'w') as f:
            json.dump(state_data, f, indent=2, default=str)
        
        logger.debug(f"State saved to {self.state_file}")
    
    def load_state(self) -> Optional[Dict]:
        """Load saved system state."""
        if not self.state_file.exists():
            return None
        
        try:
            with open(self.state_file, 'r') as f:
                state_data = json.load(f)
            
            self._error_count = state_data.get('error_count', 0)
            self._last_error = state_data.get('last_error')
            self._recovery_attempts = state_data.get('recovery_attempts', 0)
            self._state = state_data.get('state', {})
            
            logger.info(f"State loaded from {self.state_file}")
            return self._state
        
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return None
    
    def record_error(self, error: Exception) -> None:
        """Record an error occurrence."""
        self._error_count += 1
        self._last_error = str(error)
        
        logger.error(f"Error recorded ({self._error_count} total): {error}")
        
        # Auto-save state on error
        self.save_state(self._state)
    
    def attempt_recovery(self) -> bool:
        """
        Attempt to recover from error state.
        
        Returns:
            True if recovery should be attempted
        """
        self._recovery_attempts += 1
        
        # Exponential backoff logic
        max_attempts = 5
        
        if self._recovery_attempts > max_attempts:
            logger.error(f"Max recovery attempts ({max_attempts}) exceeded")
            return False
        
        logger.info(f"Recovery attempt {self._recovery_attempts}/{max_attempts}")
        return True
    
    def clear_error_state(self) -> None:
        """Clear error state after successful recovery."""
        self._error_count = 0
        self._last_error = None
        self._recovery_attempts = 0
        
        logger.info("Error state cleared")
    
    def get_status(self) -> Dict:
        """Get current error state status."""
        return {
            'error_count': self._error_count,
            'last_error': self._last_error,
            'recovery_attempts': self._recovery_attempts,
            'has_saved_state': self.state_file.exists(),
        }


# Factory functions
def create_database_archiver(
    database_url: str = "postgresql://polym:polym_dev@localhost:5432/polym",
    archive_dir: str = "data/archives",
) -> Optional[DatabaseArchiver]:
    """Create and return a DatabaseArchiver instance."""
    if not PYARROW_AVAILABLE:
        logger.warning("Database archiver unavailable - install pyarrow")
        return None
    
    return DatabaseArchiver(
        database_url=database_url,
        archive_dir=archive_dir,
    )


def create_error_recovery(
    state_file: str = "data/error_state.json",
) -> ErrorStateRecovery:
    """Create and return an ErrorStateRecovery instance."""
    return ErrorStateRecovery(state_file=state_file)
