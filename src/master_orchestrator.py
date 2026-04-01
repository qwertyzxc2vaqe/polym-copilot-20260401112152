"""
Master Orchestrator - Central Process Manager.

Phase 2 - Task 89: Central Python script capable of deploying, restarting,
and health-checking all local sub-processes and Docker containers.

Educational purpose only - paper trading simulation.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Callable, Any
from enum import Enum
import traceback

logger = logging.getLogger(__name__)


class ProcessStatus(Enum):
    """Status of a managed process."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"
    CRASHED = "crashed"
    RESTARTING = "restarting"


class ContainerStatus(Enum):
    """Status of a Docker container."""
    NOT_FOUND = "not_found"
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    EXITED = "exited"
    DEAD = "dead"


@dataclass
class ProcessConfig:
    """Configuration for a managed process."""
    name: str
    command: List[str]
    working_dir: str = "."
    env: Dict[str, str] = field(default_factory=dict)
    auto_restart: bool = True
    max_restarts: int = 3
    restart_delay_seconds: float = 5.0
    health_check_interval: float = 30.0
    health_check_command: Optional[List[str]] = None
    depends_on: List[str] = field(default_factory=list)
    cpu_affinity: Optional[List[int]] = None


@dataclass
class ProcessState:
    """Runtime state of a managed process."""
    config: ProcessConfig
    status: ProcessStatus = ProcessStatus.STOPPED
    process: Optional[subprocess.Popen] = None
    pid: Optional[int] = None
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    restart_count: int = 0
    last_health_check: Optional[float] = None
    last_error: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            'name': self.config.name,
            'status': self.status.value,
            'pid': self.pid,
            'started_at': self.started_at,
            'restart_count': self.restart_count,
            'last_health_check': self.last_health_check,
            'last_error': self.last_error,
        }


@dataclass
class ContainerConfig:
    """Configuration for a Docker container."""
    name: str
    image: str
    ports: Dict[str, str] = field(default_factory=dict)  # host:container
    volumes: Dict[str, str] = field(default_factory=dict)
    env: Dict[str, str] = field(default_factory=dict)
    network: str = "bridge"
    depends_on: List[str] = field(default_factory=list)
    health_check_port: Optional[int] = None


@dataclass
class ContainerState:
    """Runtime state of a Docker container."""
    config: ContainerConfig
    status: ContainerStatus = ContainerStatus.NOT_FOUND
    container_id: Optional[str] = None
    started_at: Optional[float] = None
    last_health_check: Optional[float] = None
    
    def to_dict(self) -> dict:
        return {
            'name': self.config.name,
            'image': self.config.image,
            'status': self.status.value,
            'container_id': self.container_id,
            'started_at': self.started_at,
        }


class MasterOrchestrator:
    """
    Central orchestrator for managing all simulation components.
    
    Manages:
    - Python sub-processes (WebSocket listeners, ML training, etc.)
    - Docker containers (Redis, PostgreSQL, Prometheus, Grafana)
    - Health checks and automatic restarts
    - Graceful shutdown
    """
    
    def __init__(
        self,
        state_file: str = "orchestrator_state.json",
        log_dir: str = "logs",
    ):
        """
        Initialize master orchestrator.
        
        Args:
            state_file: File to persist state
            log_dir: Directory for process logs
        """
        self.state_file = state_file
        self.log_dir = log_dir
        
        # Process management
        self._processes: Dict[str, ProcessState] = {}
        self._containers: Dict[str, ContainerState] = {}
        
        # Control
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._health_check_task: Optional[asyncio.Task] = None
        
        # Callbacks
        self._on_process_crash: Optional[Callable] = None
        self._on_container_crash: Optional[Callable] = None
        
        # Ensure log directory exists
        os.makedirs(log_dir, exist_ok=True)
    
    def register_process(self, config: ProcessConfig) -> None:
        """Register a process to be managed."""
        self._processes[config.name] = ProcessState(config=config)
        logger.info(f"Registered process: {config.name}")
    
    def register_container(self, config: ContainerConfig) -> None:
        """Register a Docker container to be managed."""
        self._containers[config.name] = ContainerState(config=config)
        logger.info(f"Registered container: {config.name}")
    
    async def start_all(self) -> bool:
        """
        Start all registered processes and containers.
        
        Respects dependency ordering.
        """
        self._running = True
        logger.info("Starting all components...")
        
        # Start containers first (infrastructure)
        container_order = self._get_dependency_order(
            {n: c.config.depends_on for n, c in self._containers.items()}
        )
        
        for name in container_order:
            if name in self._containers:
                await self._start_container(name)
                await asyncio.sleep(2)  # Allow container to initialize
        
        # Start processes
        process_order = self._get_dependency_order(
            {n: p.config.depends_on for n, p in self._processes.items()}
        )
        
        for name in process_order:
            if name in self._processes:
                await self._start_process(name)
                await asyncio.sleep(1)
        
        # Start health check loop
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        
        logger.info("All components started")
        return True
    
    async def stop_all(self) -> None:
        """Stop all processes and containers gracefully."""
        logger.info("Stopping all components...")
        self._running = False
        self._shutdown_event.set()
        
        # Stop health check
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        # Stop processes first (reverse order)
        process_order = self._get_dependency_order(
            {n: p.config.depends_on for n, p in self._processes.items()}
        )
        
        for name in reversed(process_order):
            if name in self._processes:
                await self._stop_process(name)
        
        # Stop containers
        container_order = self._get_dependency_order(
            {n: c.config.depends_on for n, c in self._containers.items()}
        )
        
        for name in reversed(container_order):
            if name in self._containers:
                await self._stop_container(name)
        
        # Save state
        self._save_state()
        logger.info("All components stopped")
    
    async def _start_process(self, name: str) -> bool:
        """Start a single process."""
        if name not in self._processes:
            return False
        
        state = self._processes[name]
        config = state.config
        
        if state.status == ProcessStatus.RUNNING:
            return True
        
        state.status = ProcessStatus.STARTING
        logger.info(f"Starting process: {name}")
        
        try:
            # Prepare environment
            env = os.environ.copy()
            env.update(config.env)
            
            # Open log file
            log_file = open(os.path.join(self.log_dir, f"{name}.log"), 'a')
            
            # Start process
            process = subprocess.Popen(
                config.command,
                cwd=config.working_dir,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            
            state.process = process
            state.pid = process.pid
            state.started_at = time.time() * 1000
            state.status = ProcessStatus.RUNNING
            state.last_error = None
            
            # Set CPU affinity if specified (Linux only)
            if config.cpu_affinity and sys.platform == 'linux':
                try:
                    os.sched_setaffinity(process.pid, set(config.cpu_affinity))
                except Exception as e:
                    logger.warning(f"Failed to set CPU affinity for {name}: {e}")
            
            logger.info(f"Process {name} started with PID {state.pid}")
            return True
            
        except Exception as e:
            state.status = ProcessStatus.CRASHED
            state.last_error = str(e)
            logger.error(f"Failed to start process {name}: {e}")
            return False
    
    async def _stop_process(self, name: str) -> bool:
        """Stop a single process."""
        if name not in self._processes:
            return False
        
        state = self._processes[name]
        
        if state.status == ProcessStatus.STOPPED:
            return True
        
        state.status = ProcessStatus.STOPPING
        logger.info(f"Stopping process: {name}")
        
        if state.process:
            try:
                # Try graceful termination
                state.process.terminate()
                
                # Wait up to 10 seconds
                try:
                    state.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Force kill
                    state.process.kill()
                    state.process.wait()
                
            except Exception as e:
                logger.error(f"Error stopping process {name}: {e}")
        
        state.status = ProcessStatus.STOPPED
        state.stopped_at = time.time() * 1000
        state.process = None
        state.pid = None
        
        logger.info(f"Process {name} stopped")
        return True
    
    async def _restart_process(self, name: str) -> bool:
        """Restart a process."""
        if name not in self._processes:
            return False
        
        state = self._processes[name]
        config = state.config
        
        if state.restart_count >= config.max_restarts:
            logger.error(f"Process {name} exceeded max restarts ({config.max_restarts})")
            state.status = ProcessStatus.CRASHED
            return False
        
        state.status = ProcessStatus.RESTARTING
        state.restart_count += 1
        
        logger.info(f"Restarting process {name} (attempt {state.restart_count})")
        
        await self._stop_process(name)
        await asyncio.sleep(config.restart_delay_seconds)
        return await self._start_process(name)
    
    async def _start_container(self, name: str) -> bool:
        """Start a Docker container."""
        if name not in self._containers:
            return False
        
        state = self._containers[name]
        config = state.config
        
        logger.info(f"Starting container: {name}")
        
        try:
            # Check if container exists
            result = subprocess.run(
                ['docker', 'ps', '-a', '-q', '-f', f'name={name}'],
                capture_output=True,
                text=True,
            )
            
            if result.stdout.strip():
                # Container exists, start it
                subprocess.run(['docker', 'start', name], check=True)
            else:
                # Create and start container
                cmd = ['docker', 'run', '-d', '--name', name]
                
                # Add ports
                for host, container in config.ports.items():
                    cmd.extend(['-p', f'{host}:{container}'])
                
                # Add volumes
                for host, container in config.volumes.items():
                    cmd.extend(['-v', f'{host}:{container}'])
                
                # Add environment
                for key, value in config.env.items():
                    cmd.extend(['-e', f'{key}={value}'])
                
                # Add network
                cmd.extend(['--network', config.network])
                
                # Add image
                cmd.append(config.image)
                
                subprocess.run(cmd, check=True)
            
            state.status = ContainerStatus.RUNNING
            state.started_at = time.time() * 1000
            
            # Get container ID
            result = subprocess.run(
                ['docker', 'ps', '-q', '-f', f'name={name}'],
                capture_output=True,
                text=True,
            )
            state.container_id = result.stdout.strip()
            
            logger.info(f"Container {name} started")
            return True
            
        except Exception as e:
            state.status = ContainerStatus.EXITED
            logger.error(f"Failed to start container {name}: {e}")
            return False
    
    async def _stop_container(self, name: str) -> bool:
        """Stop a Docker container."""
        if name not in self._containers:
            return False
        
        state = self._containers[name]
        
        logger.info(f"Stopping container: {name}")
        
        try:
            subprocess.run(['docker', 'stop', name], check=True, timeout=30)
            state.status = ContainerStatus.EXITED
            logger.info(f"Container {name} stopped")
            return True
        except Exception as e:
            logger.error(f"Error stopping container {name}: {e}")
            return False
    
    async def _health_check_loop(self) -> None:
        """Background loop for health checks."""
        while self._running:
            try:
                # Check processes
                for name, state in self._processes.items():
                    if state.status == ProcessStatus.RUNNING:
                        is_healthy = await self._check_process_health(name)
                        
                        if not is_healthy:
                            logger.warning(f"Process {name} unhealthy")
                            
                            if state.config.auto_restart:
                                await self._restart_process(name)
                            else:
                                state.status = ProcessStatus.UNHEALTHY
                                
                                if self._on_process_crash:
                                    await self._safe_callback(
                                        self._on_process_crash, name, state
                                    )
                
                # Check containers
                for name, state in self._containers.items():
                    if state.status == ContainerStatus.RUNNING:
                        is_healthy = await self._check_container_health(name)
                        
                        if not is_healthy:
                            logger.warning(f"Container {name} unhealthy")
                            state.status = ContainerStatus.EXITED
                            
                            if self._on_container_crash:
                                await self._safe_callback(
                                    self._on_container_crash, name, state
                                )
                
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
                await asyncio.sleep(10)
    
    async def _check_process_health(self, name: str) -> bool:
        """Check if a process is healthy."""
        state = self._processes[name]
        
        if not state.process:
            return False
        
        # Check if process is still running
        poll = state.process.poll()
        if poll is not None:
            state.last_error = f"Process exited with code {poll}"
            return False
        
        state.last_health_check = time.time() * 1000
        
        # Run custom health check if configured
        if state.config.health_check_command:
            try:
                result = subprocess.run(
                    state.config.health_check_command,
                    capture_output=True,
                    timeout=10,
                )
                return result.returncode == 0
            except Exception as e:
                state.last_error = str(e)
                return False
        
        return True
    
    async def _check_container_health(self, name: str) -> bool:
        """Check if a Docker container is healthy."""
        state = self._containers[name]
        
        try:
            result = subprocess.run(
                ['docker', 'inspect', '-f', '{{.State.Running}}', name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            
            is_running = result.stdout.strip() == 'true'
            state.last_health_check = time.time() * 1000
            
            if is_running:
                state.status = ContainerStatus.RUNNING
            else:
                state.status = ContainerStatus.EXITED
            
            return is_running
            
        except Exception as e:
            logger.error(f"Container health check failed for {name}: {e}")
            return False
    
    def _get_dependency_order(self, deps: Dict[str, List[str]]) -> List[str]:
        """Get topological order respecting dependencies."""
        order = []
        visited = set()
        
        def visit(name):
            if name in visited:
                return
            visited.add(name)
            for dep in deps.get(name, []):
                visit(dep)
            order.append(name)
        
        for name in deps:
            visit(name)
        
        return order
    
    async def _safe_callback(self, callback: Callable, *args) -> None:
        """Execute callback safely."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                callback(*args)
        except Exception as e:
            logger.error(f"Callback error: {e}")
    
    def _save_state(self) -> None:
        """Save orchestrator state to file."""
        state = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'processes': {n: s.to_dict() for n, s in self._processes.items()},
            'containers': {n: s.to_dict() for n, s in self._containers.items()},
        }
        
        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get overall orchestrator status."""
        return {
            'running': self._running,
            'processes': {n: s.to_dict() for n, s in self._processes.items()},
            'containers': {n: s.to_dict() for n, s in self._containers.items()},
        }
    
    def set_on_process_crash(self, callback: Callable) -> None:
        """Set callback for process crashes."""
        self._on_process_crash = callback
    
    def set_on_container_crash(self, callback: Callable) -> None:
        """Set callback for container crashes."""
        self._on_container_crash = callback


# Factory function
def create_orchestrator(
    state_file: str = "orchestrator_state.json",
    log_dir: str = "logs",
) -> MasterOrchestrator:
    """Create and return a MasterOrchestrator instance."""
    return MasterOrchestrator(state_file=state_file, log_dir=log_dir)
