"""
WebSocket connection pool for VALR connector to handle predictive reconnections.
"""
import asyncio
import time
from collections import deque
from typing import List, Optional, Callable
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS
from hummingbot.logger import HummingbotLogger


class VALRConnectionPool:
    """
    Maintains warm WebSocket connections to eliminate reconnection overhead.
    VALR disconnects every 30 seconds, so we predictively rotate connections.
    """
    
    _logger: Optional[HummingbotLogger] = None
    
    def __init__(self, 
                 ws_factory: Callable[[], WSAssistant],
                 ws_url: str,
                 pool_size: int = 3,
                 rotation_interval: float = 25.0):  # Rotate 5 seconds before expected disconnect
        """
        Initialize connection pool.
        
        Args:
            ws_factory: Factory function to create WebSocket assistants
            ws_url: WebSocket URL to connect to
            pool_size: Number of connections to maintain
            rotation_interval: Time between rotations (should be < 30s for VALR)
        """
        self.ws_factory = ws_factory
        self.ws_url = ws_url
        self.pool_size = pool_size
        self.rotation_interval = rotation_interval
        
        self.connections: deque[WSAssistant] = deque(maxlen=pool_size)
        self.active_connection: Optional[WSAssistant] = None
        self.next_rotation_time: float = 0
        
        self._initialization_task: Optional[asyncio.Task] = None
        self._rotation_task: Optional[asyncio.Task] = None
        self._is_initialized = False
        self._shutdown = False
        
        # Performance tracking
        self.connection_stats = {
            "total_connections": 0,
            "successful_rotations": 0,
            "failed_rotations": 0,
            "average_connection_time": 0,
            "last_rotation_time": 0
        }
    
    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = HummingbotLogger(__name__)
        return cls._logger
    
    async def initialize(self):
        """Pre-establish connections before they're needed."""
        if self._is_initialized:
            return
            
        self.logger().info(f"Initializing VALR connection pool with {self.pool_size} connections")
        
        # Create initial connections
        for i in range(self.pool_size):
            try:
                conn = await self._create_connection()
                self.connections.append(conn)
                self.connection_stats["total_connections"] += 1
                self.logger().debug(f"Created connection {i+1}/{self.pool_size}")
            except Exception as e:
                self.logger().error(f"Failed to create initial connection {i+1}: {e}")
        
        if len(self.connections) == 0:
            raise Exception("Failed to create any connections in pool")
        
        # Set active connection
        self.active_connection = self.connections[0]
        self._schedule_next_rotation()
        
        # Start background rotation task
        self._rotation_task = asyncio.create_task(self._rotation_loop())
        
        self._is_initialized = True
        self.logger().info(f"Connection pool initialized with {len(self.connections)} connections")
    
    async def get_connection(self) -> WSAssistant:
        """
        Returns active connection, handles rotation transparently.
        
        Returns:
            Active WebSocket assistant
        """
        if not self._is_initialized:
            await self.initialize()
        
        if self.active_connection is None or not await self._is_connection_healthy(self.active_connection):
            await self._emergency_rotation()
        
        return self.active_connection
    
    async def _create_connection(self) -> WSAssistant:
        """Create and connect a new WebSocket assistant."""
        ws_assistant = await self.ws_factory()
        await ws_assistant.connect(
            ws_url=self.ws_url,
            ping_timeout=CONSTANTS.PING_TIMEOUT
        )
        return ws_assistant
    
    async def _is_connection_healthy(self, connection: WSAssistant) -> bool:
        """Check if a connection is still healthy."""
        try:
            # Check if WebSocket is still connected
            if not hasattr(connection, '_ws') or connection._ws is None:
                return False
            
            # Check if connection is closed
            if connection._ws.closed:
                return False
            
            # Connection appears healthy
            return True
        except Exception:
            return False
    
    def _schedule_next_rotation(self):
        """Schedule the next connection rotation."""
        self.next_rotation_time = time.time() + self.rotation_interval
    
    async def _rotation_loop(self):
        """Background task that handles predictive connection rotation."""
        while not self._shutdown:
            try:
                # Wait until next rotation time
                sleep_time = max(0, self.next_rotation_time - time.time())
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                
                # Perform rotation
                await self._rotate_connection()
                
            except asyncio.CancelledError:
                self.logger().info("Connection rotation task cancelled")
                break
            except Exception as e:
                self.logger().error(f"Error in rotation loop: {e}")
                self.connection_stats["failed_rotations"] += 1
                # Wait a bit before retrying
                await asyncio.sleep(5)
    
    async def _rotate_connection(self):
        """Seamlessly rotate to next connection before VALR disconnect."""
        start_time = time.time()
        
        try:
            self.logger().debug("Starting predictive connection rotation")
            
            # Get next connection from pool
            old_connection = self.active_connection
            self.connections.rotate(-1)  # Move first connection to end
            self.active_connection = self.connections[0]
            
            # Verify new connection is healthy
            if not await self._is_connection_healthy(self.active_connection):
                self.logger().warning("Next connection in pool is unhealthy, creating new one")
                new_conn = await self._create_connection()
                self.connections[0] = new_conn
                self.active_connection = new_conn
                self.connection_stats["total_connections"] += 1
            
            # Schedule replacement of the old connection
            if old_connection:
                asyncio.create_task(self._replace_connection(old_connection))
            
            # Update stats
            rotation_time = time.time() - start_time
            self.connection_stats["successful_rotations"] += 1
            self.connection_stats["last_rotation_time"] = rotation_time
            
            # Update average connection time
            total_rotations = self.connection_stats["successful_rotations"]
            avg_time = self.connection_stats["average_connection_time"]
            self.connection_stats["average_connection_time"] = (
                (avg_time * (total_rotations - 1) + self.rotation_interval) / total_rotations
            )
            
            self.logger().debug(f"Connection rotation completed in {rotation_time:.3f}s")
            self._schedule_next_rotation()
            
        except Exception as e:
            self.logger().error(f"Failed to rotate connection: {e}")
            self.connection_stats["failed_rotations"] += 1
            # Try again sooner
            self.next_rotation_time = time.time() + 5
    
    async def _emergency_rotation(self):
        """Emergency rotation when active connection fails."""
        self.logger().warning("Performing emergency connection rotation")
        
        # Try each connection in the pool
        for _ in range(len(self.connections)):
            self.connections.rotate(-1)
            candidate = self.connections[0]
            
            if await self._is_connection_healthy(candidate):
                self.active_connection = candidate
                self.logger().info("Found healthy connection in pool")
                self._schedule_next_rotation()
                return
        
        # No healthy connections, create new one
        self.logger().warning("No healthy connections in pool, creating new connection")
        try:
            new_conn = await self._create_connection()
            self.connections[0] = new_conn
            self.active_connection = new_conn
            self.connection_stats["total_connections"] += 1
            self._schedule_next_rotation()
        except Exception as e:
            self.logger().error(f"Failed to create emergency connection: {e}")
            raise
    
    async def _replace_connection(self, old_connection: WSAssistant):
        """Replace a connection that will disconnect soon."""
        try:
            # Wait a bit to avoid connection surge
            await asyncio.sleep(2)
            
            # Disconnect old connection
            try:
                await old_connection.disconnect()
            except Exception:
                pass  # Connection might already be closed
            
            # Create new connection for the pool
            new_conn = await self._create_connection()
            
            # Find and replace the old connection in pool
            for i, conn in enumerate(self.connections):
                if conn == old_connection:
                    self.connections[i] = new_conn
                    break
            
            self.connection_stats["total_connections"] += 1
            self.logger().debug("Replaced old connection in pool")
            
        except Exception as e:
            self.logger().error(f"Failed to replace connection: {e}")
    
    async def shutdown(self):
        """Shutdown the connection pool gracefully."""
        self.logger().info("Shutting down connection pool")
        self._shutdown = True
        
        # Cancel rotation task
        if self._rotation_task and not self._rotation_task.done():
            self._rotation_task.cancel()
            try:
                await self._rotation_task
            except asyncio.CancelledError:
                pass
        
        # Disconnect all connections
        for conn in self.connections:
            try:
                await conn.disconnect()
            except Exception:
                pass
        
        self.connections.clear()
        self.active_connection = None
        self._is_initialized = False
        
        self.logger().info(f"Connection pool shutdown complete. Stats: {self.connection_stats}")
    
    def get_stats(self) -> dict:
        """Get connection pool statistics."""
        return {
            **self.connection_stats,
            "pool_size": len(self.connections),
            "is_initialized": self._is_initialized,
            "next_rotation_in": max(0, self.next_rotation_time - time.time())
        }