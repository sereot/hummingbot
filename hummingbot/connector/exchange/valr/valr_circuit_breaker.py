"""
Circuit breaker pattern for VALR connector to handle API errors and protect against cascading failures.
"""
import asyncio
import time
from enum import Enum
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass, field
from collections import deque
import logging


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Blocking requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker"""
    failure_threshold: int = 5  # Number of failures before opening
    success_threshold: int = 2  # Number of successes in half-open before closing
    timeout: float = 30.0  # Time in seconds before attempting recovery
    excluded_errors: list = field(default_factory=lambda: [asyncio.CancelledError])
    
    # Exponential backoff configuration
    initial_timeout: float = 30.0
    max_timeout: float = 300.0  # 5 minutes max
    backoff_multiplier: float = 2.0


class CircuitBreaker:
    """
    Circuit breaker implementation for API calls.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, requests are blocked
    - HALF_OPEN: Testing recovery, limited requests allowed
    """
    
    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        
        # Failure tracking
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.consecutive_failures = 0
        
        # State transition tracking
        self.last_state_change = time.time()
        self.current_timeout = self.config.initial_timeout
        
        # Error history for analysis
        self.error_history: deque = deque(maxlen=100)
        
        # Callbacks
        self.on_state_change: Optional[Callable[[CircuitState, CircuitState], None]] = None
        
        # Logger
        self.logger = logging.getLogger(f"{__name__}.{name}")
        
    def call(self, func: Callable):
        """Decorator for wrapping functions with circuit breaker"""
        async def wrapper(*args, **kwargs):
            return await self.run(func, *args, **kwargs)
        return wrapper
        
    async def run(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        if not self._can_execute():
            raise CircuitOpenError(f"Circuit breaker '{self.name}' is OPEN")
            
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
            
        except Exception as e:
            if not self._should_count_error(e):
                raise
                
            self._on_failure(e)
            raise
            
    def _can_execute(self) -> bool:
        """Check if request can be executed based on current state"""
        if self.state == CircuitState.CLOSED:
            return True
            
        elif self.state == CircuitState.OPEN:
            if self._should_attempt_recovery():
                self._transition_to(CircuitState.HALF_OPEN)
                return True
            return False
            
        else:  # HALF_OPEN
            return True
            
    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to attempt recovery"""
        if self.last_failure_time is None:
            return True
            
        time_since_failure = time.time() - self.last_failure_time
        return time_since_failure >= self.current_timeout
        
    def _should_count_error(self, error: Exception) -> bool:
        """Determine if error should count towards circuit breaker"""
        return type(error) not in self.config.excluded_errors
        
    def _on_success(self):
        """Handle successful execution"""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            self.logger.debug(f"Success in HALF_OPEN state ({self.success_count}/{self.config.success_threshold})")
            
            if self.success_count >= self.config.success_threshold:
                self._transition_to(CircuitState.CLOSED)
                self._reset_timeout()
                
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0
            self.consecutive_failures = 0
            
    def _on_failure(self, error: Exception):
        """Handle failed execution"""
        self.last_failure_time = time.time()
        self.error_history.append({
            "timestamp": self.last_failure_time,
            "error": str(error),
            "type": type(error).__name__
        })
        
        if self.state == CircuitState.CLOSED:
            self.failure_count += 1
            self.consecutive_failures += 1
            self.logger.warning(f"Failure {self.failure_count}/{self.config.failure_threshold}: {error}")
            
            if self.failure_count >= self.config.failure_threshold:
                self._transition_to(CircuitState.OPEN)
                self._increase_timeout()
                
        elif self.state == CircuitState.HALF_OPEN:
            # Single failure in half-open state reopens circuit
            self.logger.warning(f"Failure in HALF_OPEN state, reopening circuit: {error}")
            self._transition_to(CircuitState.OPEN)
            self._increase_timeout()
            
    def _transition_to(self, new_state: CircuitState):
        """Transition to new state"""
        if new_state == self.state:
            return
            
        old_state = self.state
        self.state = new_state
        self.last_state_change = time.time()
        
        # Reset counters based on new state
        if new_state == CircuitState.CLOSED:
            self.failure_count = 0
            self.success_count = 0
            self.consecutive_failures = 0
        elif new_state == CircuitState.HALF_OPEN:
            self.success_count = 0
            
        self.logger.info(f"Circuit breaker state changed: {old_state.value} -> {new_state.value}")
        
        if self.on_state_change:
            self.on_state_change(old_state, new_state)
            
    def _increase_timeout(self):
        """Increase timeout using exponential backoff"""
        self.current_timeout = min(
            self.current_timeout * self.config.backoff_multiplier,
            self.config.max_timeout
        )
        self.logger.info(f"Timeout increased to {self.current_timeout}s")
        
    def _reset_timeout(self):
        """Reset timeout to initial value"""
        self.current_timeout = self.config.initial_timeout
        
    def get_status(self) -> Dict[str, Any]:
        """Get current circuit breaker status"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "consecutive_failures": self.consecutive_failures,
            "current_timeout": self.current_timeout,
            "time_in_state": time.time() - self.last_state_change,
            "recent_errors": list(self.error_history)[-5:]  # Last 5 errors
        }
        
    def reset(self):
        """Manually reset circuit breaker"""
        self._transition_to(CircuitState.CLOSED)
        self._reset_timeout()
        self.failure_count = 0
        self.success_count = 0
        self.consecutive_failures = 0
        self.last_failure_time = None


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open"""
    pass


class CircuitBreakerManager:
    """Manages multiple circuit breakers for different API endpoints"""
    
    def __init__(self):
        self.breakers: Dict[str, CircuitBreaker] = {}
        self.logger = logging.getLogger(__name__)
        
    def get_breaker(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        """Get or create circuit breaker for endpoint"""
        if name not in self.breakers:
            self.breakers[name] = CircuitBreaker(name, config)
            self.logger.info(f"Created circuit breaker for '{name}'")
            
        return self.breakers[name]
        
    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all circuit breakers"""
        return {name: breaker.get_status() for name, breaker in self.breakers.items()}
        
    def reset_all(self):
        """Reset all circuit breakers"""
        for breaker in self.breakers.values():
            breaker.reset()
            
    def get_health_score(self) -> float:
        """
        Calculate overall health score (0-100).
        100 = all circuits closed
        0 = all circuits open
        """
        if not self.breakers:
            return 100.0
            
        closed_count = sum(1 for b in self.breakers.values() if b.state == CircuitState.CLOSED)
        return (closed_count / len(self.breakers)) * 100


# Specialized circuit breakers for different failure types
class RateLimitCircuitBreaker(CircuitBreaker):
    """Circuit breaker specifically for rate limit errors"""
    
    def __init__(self, name: str):
        config = CircuitBreakerConfig(
            failure_threshold=3,  # Open after 3 rate limit errors
            success_threshold=1,  # Close after 1 success
            timeout=60.0,  # Wait 1 minute before retry
            initial_timeout=60.0,
            max_timeout=600.0  # Max 10 minutes
        )
        super().__init__(name, config)
        
        
class ConnectionCircuitBreaker(CircuitBreaker):
    """Circuit breaker for connection/network errors"""
    
    def __init__(self, name: str):
        config = CircuitBreakerConfig(
            failure_threshold=5,  # More tolerant of connection issues
            success_threshold=3,  # Require more successes to close
            timeout=30.0,
            initial_timeout=30.0,
            max_timeout=300.0
        )
        super().__init__(name, config)