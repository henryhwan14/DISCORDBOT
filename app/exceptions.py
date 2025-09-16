"""Domain specific exceptions for trading operations."""


class TradingError(RuntimeError):
    """Base class for domain errors raised by the trading engine."""


class InsufficientFundsError(TradingError):
    """Raised when the user does not have enough balance to buy securities."""


class PositionNotFoundError(TradingError):
    """Raised when a user attempts to sell a position that does not exist."""


class QuantityTooLargeError(TradingError):
    """Raised when the requested sell quantity exceeds the position size."""

