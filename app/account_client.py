"""Clients that communicate with the external account service."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional

import httpx


class AccountServiceError(RuntimeError):
    """Raised when the remote account service returns an unexpected error."""


@dataclass
class Balance:
    user_id: str
    balance: float


class AccountServiceProtocol:
    """Protocol for account service implementations."""

    async def get_balance(self, user_id: str) -> Balance:
        raise NotImplementedError

    async def create_transaction(self, user_id: str, amount: float, description: str) -> Balance:
        raise NotImplementedError


class AccountServiceClient(AccountServiceProtocol):
    """HTTP client that connects to another bot's account system."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._headers = {"Accept": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    async def get_balance(self, user_id: str) -> Balance:
        response = await self._client.get(f"/accounts/{user_id}", headers=self._headers)
        if response.status_code >= 400:
            raise AccountServiceError(
                f"Failed to obtain balance for {user_id}: {response.status_code} {response.text}"
            )
        payload = response.json()
        return Balance(user_id=user_id, balance=float(payload.get("balance", 0.0)))

    async def create_transaction(self, user_id: str, amount: float, description: str) -> Balance:
        payload = {"amount": amount, "description": description}
        response = await self._client.post(
            f"/accounts/{user_id}/transactions", headers=self._headers, json=payload
        )
        if response.status_code >= 400:
            raise AccountServiceError(
                f"Failed to create transaction for {user_id}: {response.status_code} {response.text}"
            )
        data = response.json()
        return Balance(user_id=user_id, balance=float(data.get("balance", 0.0)))

    async def aclose(self) -> None:
        await self._client.aclose()


class InMemoryAccountService(AccountServiceProtocol):
    """Small in-memory account service used for development or testing."""

    def __init__(self, initial_balance: float = 10_000.0) -> None:
        self._balances: Dict[str, float] = {}
        self._initial_balance = initial_balance
        self._lock = asyncio.Lock()

    async def get_balance(self, user_id: str) -> Balance:
        async with self._lock:
            balance = self._balances.setdefault(user_id, self._initial_balance)
            return Balance(user_id=user_id, balance=balance)

    async def create_transaction(self, user_id: str, amount: float, description: str) -> Balance:  # noqa: ARG002
        async with self._lock:
            balance = self._balances.setdefault(user_id, self._initial_balance)
            balance += amount
            self._balances[user_id] = balance
            return Balance(user_id=user_id, balance=balance)


def create_account_service(
    base_url: Optional[str],
    api_key: Optional[str],
    timeout: float,
    *,
    development_initial_balance: float = 10_000.0,
) -> AccountServiceProtocol:
    """Factory that returns the appropriate account service implementation."""

    if base_url:
        return AccountServiceClient(base_url=base_url, api_key=api_key, timeout=timeout)
    return InMemoryAccountService(initial_balance=development_initial_balance)

