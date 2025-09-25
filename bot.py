"""Discord economy bot without Roblox integration.

This module contains the main bot implementation, data management helpers,
and command definitions for a virtual economy Discord bot. The bot provides
account management, transfers, public accounts, administrator utilities, and
scheduled tasks such as tax collection and salary payments.
"""
from __future__ import annotations

import json
import os
import random
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import discord
import pandas as pd
from discord.ext import commands, tasks
from dotenv import load_dotenv

###############################################################################
# Configuration & Constants
###############################################################################

DATA_FILE = Path("users.json")
SETTINGS_FILE = Path("admin_settings.json")
PUBLIC_ACCOUNTS_FILE = Path("public_accounts.json")
TRANSACTIONS_FILE = Path("transactions.json")
ACCOUNT_MAPPING_FILE = Path("account_mapping.json")

ADMIN_USER_IDS = {496921375768838154, 559307598848065537}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "transaction_fee": {"enabled": False, "min_amount": 0, "fee_rate": 0.0},
    "tax_system": {
        "enabled": False,
        "rate": 0.0,
        "period_days": 30,
        "last_collected": None,
        "tax_name": "세금",
    },
    "salary_system": {
        "enabled": False,
        "salaries": {},
        "source_account": {},
        "last_paid": None,
    },
    "frozen_accounts": {},
}

###############################################################################
# Utility classes & helpers
###############################################################################


class JsonFile:
    """Simple helper for managing JSON persistence."""

    def __init__(self, path: Path, default: Any) -> None:
        self.path = path
        self.default = default
        self._ensure_exists()

    def _ensure_exists(self) -> None:
        if not self.path.exists():
            self.save(self.default)

    def load(self) -> Any:
        with self.path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def save(self, data: Any) -> None:
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=4)


users_store = JsonFile(DATA_FILE, {})
settings_store = JsonFile(SETTINGS_FILE, DEFAULT_SETTINGS)
public_accounts_store = JsonFile(PUBLIC_ACCOUNTS_FILE, {})
transactions_store = JsonFile(TRANSACTIONS_FILE, [])
account_mapping_store = JsonFile(ACCOUNT_MAPPING_FILE, {})


###############################################################################
# Data models
###############################################################################


@dataclass
class AccountRecord:
    account_number: str
    owner_name: str
    balance: int
    is_public: bool = False

    @classmethod
    def from_dict(cls, account_number: str, data: Dict[str, Any]) -> "AccountRecord":
        balance = data.get("잔액")
        if balance is None:
            cash = data.pop("현금", 0)
            bank = data.pop("은행", 0)
            balance = cash + bank
        return cls(
            account_number=account_number,
            owner_name=data.get("이름", "알 수 없음"),
            balance=balance,
            is_public=data.get("공용계좌", False),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "이름": self.owner_name,
            "계좌번호": self.account_number,
            "잔액": self.balance,
        }
        if self.is_public:
            data["공용계좌"] = True
        return data


###############################################################################
# Storage helper functions
###############################################################################


def load_users() -> Dict[str, Dict[str, Any]]:
    return users_store.load()


def save_users(users: Dict[str, Dict[str, Any]]) -> None:
    users_store.save(users)


def load_settings() -> Dict[str, Any]:
    return settings_store.load()


def save_settings(settings: Dict[str, Any]) -> None:
    settings_store.save(settings)


def load_public_accounts() -> Dict[str, Dict[str, Any]]:
    return public_accounts_store.load()


def save_public_accounts(data: Dict[str, Dict[str, Any]]) -> None:
    public_accounts_store.save(data)


def load_transactions() -> List[Dict[str, Any]]:
    return transactions_store.load()


def save_transactions(data: List[Dict[str, Any]]) -> None:
    transactions_store.save(data)


def load_account_mapping() -> Dict[str, Dict[str, Any]]:
    return account_mapping_store.load()


def save_account_mapping(mapping: Dict[str, Dict[str, Any]]) -> None:
    account_mapping_store.save(mapping)


def add_transaction(
    transaction_type: str,
    from_user: str,
    to_user: str,
    amount: int,
    fee: int = 0,
    memo: str = "",
) -> None:
    transactions = load_transactions()
    transactions.append(
        {
            "timestamp": datetime.now().isoformat(),
            "type": transaction_type,
            "from_user": from_user,
            "to_user": to_user,
            "amount": amount,
            "fee": fee,
            "memo": memo,
        }
    )
    transactions = transactions[-1000:]
    save_transactions(transactions)


###############################################################################
# Account helpers
###############################################################################


def format_number_4digit(value: int) -> str:
    return f"{value:,}"


def generate_account_number() -> str:
    users = load_users()
    mapping = load_account_mapping()
    public_accounts = load_public_accounts()

    existing_numbers = set(users.keys()) | set(mapping.keys())
    existing_numbers.update(account["account_number"] for account in public_accounts.values())

    while True:
        account_number = f"{random.randint(1000, 9999)}"
        if account_number not in existing_numbers:
            return account_number


def get_account_number_by_user(user_id: int) -> Optional[str]:
    mapping = load_account_mapping()
    for account_number, data in mapping.items():
        if data.get("user_id") == user_id:
            return account_number
    return None


def ensure_account_record(account_number: str) -> AccountRecord:
    users = load_users()
    if account_number not in users:
        raise ValueError("Account not found")
    record = AccountRecord.from_dict(account_number, users[account_number])
    users[account_number] = record.to_dict()
    save_users(users)
    return record


def update_account_record(record: AccountRecord) -> None:
    users = load_users()
    users[record.account_number] = record.to_dict()
    save_users(users)


def is_account_frozen(account_number: str) -> bool:
    settings = load_settings()
    frozen_accounts = settings.get("frozen_accounts", {})
    return account_number in frozen_accounts


def set_account_frozen(account_number: str, frozen: bool, reason: str = "") -> None:
    settings = load_settings()
    frozen_accounts = settings.setdefault("frozen_accounts", {})
    if frozen:
        frozen_accounts[account_number] = {
            "frozen_at": datetime.now().isoformat(),
            "reason": reason,
        }
    else:
        frozen_accounts.pop(account_number, None)
    save_settings(settings)


def verify_public_account(account_number: str, password: str) -> Optional[str]:
    public_accounts = load_public_accounts()
    for name, data in public_accounts.items():
        if data["account_number"] == account_number and data["password"] == password:
            return name
    return None


def calculate_transaction_fee(amount: int) -> int:
    settings = load_settings()
    fee_config = settings.get("transaction_fee", {})
    if not fee_config.get("enabled"):
        return 0
    if amount < fee_config.get("min_amount", 0):
        return 0
    return int(amount * fee_config.get("fee_rate", 0.0))


def perform_transfer(
    sender_account: str,
    recipient_account: str,
    amount: int,
    *,
    memo: str = "",
    apply_fee: bool = True,
    transaction_type: str = "송금",
) -> Tuple[int, AccountRecord, AccountRecord]:
    if amount <= 0:
        raise ValueError("Transfer amount must be positive")

    sender = ensure_account_record(sender_account)
    recipient = ensure_account_record(recipient_account)

    if is_account_frozen(sender.account_number):
        raise PermissionError("Sender account is frozen")
    if is_account_frozen(recipient.account_number):
        raise PermissionError("Recipient account is frozen")

    fee = calculate_transaction_fee(amount) if apply_fee else 0
    total_cost = amount + fee

    if sender.balance < total_cost:
        raise RuntimeError("Insufficient funds")

    sender.balance -= total_cost
    recipient.balance += amount

    update_account_record(sender)
    update_account_record(recipient)

    add_transaction(transaction_type, sender.account_number, recipient.account_number, amount, fee, memo)
    return fee, sender, recipient


def require_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


###############################################################################
# Discord setup
###############################################################################

load_dotenv()
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set in the environment")

intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)


###############################################################################
# Slash command helpers
###############################################################################


def build_account_embed(record: AccountRecord, *, title: str, include_status: bool = True) -> discord.Embed:
    embed = discord.Embed(title=title, color=0x0099FF)
    embed.add_field(name="계좌번호", value=f"`{record.account_number}`", inline=False)
    embed.add_field(name="예금주", value=record.owner_name, inline=False)
    embed.add_field(name="잔액", value=f"{format_number_4digit(record.balance)}원", inline=False)
    if include_status:
        status = "🔒 동결됨" if is_account_frozen(record.account_number) else "✅ 정상"
        embed.add_field(name="계좌 상태", value=status, inline=False)
    return embed


###############################################################################
# Slash commands - general user utilities
###############################################################################


@bot.tree.command(name="정보", description="자신 또는 다른 사용자의 계좌 정보를 조회합니다")
async def cmd_account_info(interaction: discord.Interaction, 멤버: Optional[discord.Member] = None) -> None:
    member = 멤버 or interaction.user
    account_number = get_account_number_by_user(member.id)
    if not account_number:
        await interaction.response.send_message("계좌가 없습니다. `/계좌생성` 명령어로 먼저 계좌를 만드세요.", ephemeral=True)
        return

    record = ensure_account_record(account_number)
    embed = build_account_embed(record, title="🏦 계좌 정보")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="계좌생성", description="새로운 계좌를 생성합니다")
async def cmd_create_account(interaction: discord.Interaction) -> None:
    if get_account_number_by_user(interaction.user.id):
        await interaction.response.send_message("이미 계좌가 존재합니다. `/잔액` 명령어로 확인하세요.", ephemeral=True)
        return

    account_number = generate_account_number()
    record = AccountRecord(account_number=account_number, owner_name=interaction.user.display_name, balance=1_000_000)

    users = load_users()
    users[account_number] = record.to_dict()
    save_users(users)

    mapping = load_account_mapping()
    mapping[account_number] = {
        "user_id": interaction.user.id,
        "discord_name": interaction.user.display_name,
        "created_at": datetime.now().isoformat(),
    }
    save_account_mapping(mapping)

    add_transaction("계좌생성", "SYSTEM", account_number, 1_000_000, 0, "신규 계좌 생성")

    embed = build_account_embed(record, title="계좌 생성 완료! 🎉", include_status=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="잔액", description="내 계좌번호와 현재 잔액을 확인합니다")
async def cmd_balance(interaction: discord.Interaction) -> None:
    account_number = get_account_number_by_user(interaction.user.id)
    if not account_number:
        await interaction.response.send_message("계좌가 없습니다. `/계좌생성` 명령어로 먼저 계좌를 만드세요.", ephemeral=True)
        return

    record = ensure_account_record(account_number)
    embed = build_account_embed(record, title="📊 잔액 확인")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="송금", description="입력한 계좌번호로 원하는 금액을 송금합니다")
async def cmd_transfer(interaction: discord.Interaction, 계좌번호: str, 금액: int) -> None:
    sender_account = get_account_number_by_user(interaction.user.id)
    if not sender_account:
        await interaction.response.send_message("계좌가 없습니다. `/계좌생성` 명령어로 먼저 계좌를 만드세요.", ephemeral=True)
        return

    if sender_account == 계좌번호:
        await interaction.response.send_message("송금 실패: 자기 자신에게는 송금할 수 없습니다.", ephemeral=True)
        return

    try:
        fee, _, _ = perform_transfer(
            sender_account,
            계좌번호,
            금액,
            memo=f"계좌번호 송금: {계좌번호}",
            transaction_type="송금",
        )
    except ValueError:
        await interaction.response.send_message("송금 실패: 송금액은 1원 이상이어야 합니다.", ephemeral=True)
        return
    except PermissionError as exc:
        await interaction.response.send_message(f"송금 실패: {exc}", ephemeral=True)
        return
    except RuntimeError:
        fee_amount = calculate_transaction_fee(금액)
        total_cost = 금액 + fee_amount
        if fee_amount:
            message = (
                f"송금 실패: 잔액 부족 (송금액: {format_number_4digit(금액)}원 + 수수료: {format_number_4digit(fee_amount)}원 = "
                f"총 {format_number_4digit(total_cost)}원이 필요합니다.)"
            )
        else:
            message = "송금 실패: 잔액이 부족합니다."
        await interaction.response.send_message(message, ephemeral=True)
        return
    except Exception:
        await interaction.response.send_message("송금 처리 중 오류가 발생했습니다. 관리자에게 문의하세요.", ephemeral=True)
        return

    description = [f"💸 **{format_number_4digit(금액)}원 송금 완료!**", f"대상 계좌: `{계좌번호}`"]
    if fee:
        description.append(f"수수료: {format_number_4digit(fee)}원")
    await interaction.response.send_message("\n".join(description))


@bot.tree.command(name="거래내역", description="최근 거래내역을 조회합니다")
async def cmd_transactions(interaction: discord.Interaction) -> None:
    account_number = get_account_number_by_user(interaction.user.id)
    if not account_number:
        await interaction.response.send_message("계좌가 없습니다. `/계좌생성` 명령어로 먼저 계좌를 만드세요.", ephemeral=True)
        return

    transactions = load_transactions()
    user_transactions = [t for t in transactions if t["from_user"] == account_number or t["to_user"] == account_number][-10:]

    if not user_transactions:
        await interaction.response.send_message("거래내역이 없습니다.", ephemeral=True)
        return

    embed = discord.Embed(title="최근 거래내역 (10건)", color=0x0099FF)
    for transaction in reversed(user_transactions):
        timestamp = datetime.fromisoformat(transaction["timestamp"]).strftime("%m/%d %H:%M")
        if transaction["from_user"] == account_number:
            desc = f"↗️ {transaction['type']} -{format_number_4digit(transaction['amount'])}원"
            if transaction["fee"]:
                desc += f" (수수료: {format_number_4digit(transaction['fee'])}원)"
        else:
            desc = f"↘️ {transaction['type']} +{format_number_4digit(transaction['amount'])}원"
        if transaction.get("memo"):
            desc += f"\n메모: {transaction['memo']}"
        embed.add_field(name=timestamp, value=desc, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="타계좌조회", description="다른 사용자의 계좌 번호를 확인합니다")
async def cmd_lookup_other_account(interaction: discord.Interaction, 대상자: discord.Member) -> None:
    account_number = get_account_number_by_user(대상자.id)
    if not account_number:
        await interaction.response.send_message("해당 사용자는 계좌가 없습니다.", ephemeral=True)
        return

    record = ensure_account_record(account_number)
    embed = build_account_embed(record, title=f"{대상자.display_name}님의 계좌 정보")
    await interaction.response.send_message(embed=embed, ephemeral=True)


###############################################################################
# Slash commands - administrator utilities
###############################################################################


def ensure_admin(interaction: discord.Interaction) -> bool:
    return require_admin(interaction.user.id)


@bot.tree.command(name="계좌동결", description="[관리자 전용] 계좌를 동결합니다")
async def cmd_freeze_account(interaction: discord.Interaction, 대상자: discord.Member, 사유: str = "계좌 동결") -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    account_number = get_account_number_by_user(대상자.id)
    if not account_number:
        await interaction.response.send_message("계좌를 찾을 수 없습니다.", ephemeral=True)
        return

    if is_account_frozen(account_number):
        await interaction.response.send_message("이미 동결된 계좌입니다.", ephemeral=True)
        return

    set_account_frozen(account_number, True, 사유)
    embed = discord.Embed(title="계좌 동결 완료", color=0xFF0000)
    embed.add_field(name="대상 계좌", value=f"`{account_number}`", inline=False)
    embed.add_field(name="동결 사유", value=사유, inline=False)
    embed.add_field(name="동결 시간", value=datetime.now().strftime("%Y-%m-%d %H:%M"), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="계좌해제", description="[관리자 전용] 계좌 동결을 해제합니다")
async def cmd_unfreeze_account(interaction: discord.Interaction, 대상자: discord.Member) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    account_number = get_account_number_by_user(대상자.id)
    if not account_number:
        await interaction.response.send_message("계좌를 찾을 수 없습니다.", ephemeral=True)
        return

    if not is_account_frozen(account_number):
        await interaction.response.send_message("해당 계좌는 동결되어 있지 않습니다.", ephemeral=True)
        return

    set_account_frozen(account_number, False)
    embed = discord.Embed(title="계좌 동결 해제", color=0x00FF00)
    embed.add_field(name="대상 계좌", value=f"`{account_number}`", inline=False)
    embed.add_field(name="해제 시간", value=datetime.now().strftime("%Y-%m-%d %H:%M"), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="관리자화폐발행", description="[관리자 전용] 특정 계좌에 화폐를 발행합니다")
async def cmd_mint_currency(
    interaction: discord.Interaction,
    대상자: discord.Member,
    금액: int,
    사유: str = "관리자 화폐 발행",
) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    if 금액 <= 0:
        await interaction.response.send_message("발행 금액은 1원 이상이어야 합니다.", ephemeral=True)
        return

    account_number = get_account_number_by_user(대상자.id)
    if not account_number:
        await interaction.response.send_message("계좌를 찾을 수 없습니다.", ephemeral=True)
        return

    record = ensure_account_record(account_number)
    if is_account_frozen(account_number):
        await interaction.response.send_message("계좌가 동결되어 있습니다.", ephemeral=True)
        return

    previous_balance = record.balance
    record.balance += 금액
    update_account_record(record)

    add_transaction(
        "관리자화폐발행",
        "SYSTEM",
        account_number,
        금액,
        0,
        f"화폐 발행: {사유} (관리자: {interaction.user.display_name})",
    )

    embed = discord.Embed(title="💰 화폐 발행 완료", color=0x00FF00)
    embed.add_field(name="대상 계좌", value=f"`{account_number}`", inline=False)
    embed.add_field(name="대상자", value=대상자.display_name, inline=False)
    embed.add_field(name="발행 금액", value=f"{format_number_4digit(금액)}원", inline=True)
    embed.add_field(name="이전 잔액", value=f"{format_number_4digit(previous_balance)}원", inline=True)
    embed.add_field(name="현재 잔액", value=f"{format_number_4digit(record.balance)}원", inline=True)
    embed.add_field(name="발행 사유", value=사유, inline=False)
    embed.add_field(name="처리 관리자", value=interaction.user.display_name, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="관리자거래세설정", description="[관리자 전용] 거래 수수료를 설정합니다")
async def cmd_set_transaction_fee(
    interaction: discord.Interaction, 최소금액: int, 수수료율: float
) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    if not 0 <= 수수료율 <= 100:
        await interaction.response.send_message("수수료율은 0%에서 100% 사이여야 합니다.", ephemeral=True)
        return

    settings = load_settings()
    settings["transaction_fee"] = {
        "enabled": True,
        "min_amount": 최소금액,
        "fee_rate": 수수료율 / 100,
    }
    save_settings(settings)

    embed = discord.Embed(title="거래세 설정 완료!", color=0x0099FF)
    embed.add_field(name="최소 거래 금액", value=f"{format_number_4digit(최소금액)}원", inline=False)
    embed.add_field(name="수수료율", value=f"{수수료율}%", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="공용계좌", description="[관리자 전용] 공용계좌를 생성합니다")
async def cmd_create_public_account(
    interaction: discord.Interaction, 계좌이름: str, 계좌번호: str, 계좌비밀번호: str
) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    users = load_users()
    public_accounts = load_public_accounts()
    mapping = load_account_mapping()

    existing_numbers = set(users.keys()) | set(mapping.keys())
    existing_numbers.update(account["account_number"] for account in public_accounts.values())

    if 계좌번호 in existing_numbers:
        await interaction.response.send_message("이미 사용 중인 계좌번호입니다.", ephemeral=True)
        return

    if 계좌이름 in public_accounts:
        await interaction.response.send_message("이미 존재하는 공용계좌 이름입니다.", ephemeral=True)
        return

    public_accounts[계좌이름] = {
        "account_number": 계좌번호,
        "password": 계좌비밀번호,
        "created_by": interaction.user.id,
        "created_at": datetime.now().isoformat(),
    }
    save_public_accounts(public_accounts)

    record = AccountRecord(account_number=계좌번호, owner_name=f"공용계좌({계좌이름})", balance=0, is_public=True)
    users[계좌번호] = record.to_dict()
    save_users(users)

    embed = discord.Embed(title="🏛️ 공용계좌 생성 완료", color=0x0099FF)
    embed.add_field(name="계좌 이름", value=계좌이름, inline=True)
    embed.add_field(name="계좌번호", value=계좌번호, inline=True)
    embed.add_field(name="초기 잔액", value="0원", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="공용계좌접근", description="[관리자 전용] 공용계좌 정보를 DM으로 받습니다")
async def cmd_access_public_account(interaction: discord.Interaction, 계좌이름: str) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    public_accounts = load_public_accounts()
    if 계좌이름 not in public_accounts:
        await interaction.response.send_message("공용계좌를 찾을 수 없습니다.", ephemeral=True)
        return

    account = public_accounts[계좌이름]
    embed = discord.Embed(title=f"🏛️ 공용계좌: {계좌이름}", color=0x0099FF)
    embed.add_field(name="계좌번호", value=f"`{account['account_number']}`", inline=False)
    embed.add_field(name="비밀번호", value=f"`{account['password']}`", inline=False)
    embed.set_footer(text="이 정보로 공용계좌 거래를 할 수 있습니다.")

    try:
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("공용계좌 정보를 DM으로 발송했습니다.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("DM을 보낼 수 없습니다. DM 설정을 확인해주세요.", ephemeral=True)


@bot.tree.command(name="공용계좌잔액", description="[관리자 전용] 공용계좌의 잔액을 조회합니다")
async def cmd_public_account_balance(
    interaction: discord.Interaction, 계좌번호: str, 비밀번호: str
) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    account_name = verify_public_account(계좌번호, 비밀번호)
    if not account_name:
        await interaction.response.send_message("공용계좌 인증에 실패했습니다.", ephemeral=True)
        return

    record = ensure_account_record(계좌번호)
    embed = build_account_embed(record, title="🏛️ 공용계좌 잔액", include_status=True)
    embed.add_field(name="계좌 이름", value=account_name, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="공용계좌송금", description="공용계좌에서 다른 계좌로 송금합니다")
async def cmd_public_transfer(
    interaction: discord.Interaction,
    공용계좌번호: str,
    공용계좌비밀번호: str,
    받는계좌번호: str,
    금액: int,
) -> None:
    account_name = verify_public_account(공용계좌번호, 공용계좌비밀번호)
    if not account_name:
        await interaction.response.send_message("공용계좌 인증에 실패했습니다.", ephemeral=True)
        return

    if 공용계좌번호 == 받는계좌번호:
        await interaction.response.send_message("같은 계좌로는 송금할 수 없습니다.", ephemeral=True)
        return

    try:
        fee, _, _ = perform_transfer(
            공용계좌번호,
            받는계좌번호,
            금액,
            memo=f"공용계좌({account_name}) → {받는계좌번호}",
            transaction_type="공용계좌송금",
        )
    except ValueError:
        await interaction.response.send_message("송금 실패: 금액은 1원 이상이어야 합니다.", ephemeral=True)
        return
    except PermissionError as exc:
        await interaction.response.send_message(f"송금 실패: {exc}", ephemeral=True)
        return
    except RuntimeError:
        fee_amount = calculate_transaction_fee(금액)
        total_cost = 금액 + fee_amount
        if fee_amount:
            message = (
                f"송금 실패: 잔액 부족 (송금액: {format_number_4digit(금액)}원 + 수수료: {format_number_4digit(fee_amount)}원 = "
                f"총 {format_number_4digit(total_cost)}원이 필요합니다.)"
            )
        else:
            message = "송금 실패: 잔액이 부족합니다."
        await interaction.response.send_message(message, ephemeral=True)
        return

    embed = discord.Embed(title="💸 공용계좌 송금 완료", color=0x00FF00)
    embed.add_field(name="보내는 계좌", value=f"{account_name} (`{공용계좌번호}`)", inline=False)
    embed.add_field(name="받는 계좌", value=f"`{받는계좌번호}`", inline=False)
    embed.add_field(name="송금액", value=f"{format_number_4digit(금액)}원", inline=True)
    if fee:
        embed.add_field(name="수수료", value=f"{format_number_4digit(fee)}원", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="공용계좌입금", description="내 계좌에서 공용계좌로 입금합니다")
async def cmd_public_deposit(
    interaction: discord.Interaction,
    공용계좌번호: str,
    공용계좌비밀번호: str,
    금액: int,
) -> None:
    account_name = verify_public_account(공용계좌번호, 공용계좌비밀번호)
    if not account_name:
        await interaction.response.send_message("공용계좌 인증에 실패했습니다.", ephemeral=True)
        return

    sender_account = get_account_number_by_user(interaction.user.id)
    if not sender_account:
        await interaction.response.send_message("계좌가 없습니다. `/계좌생성` 명령어로 먼저 계좌를 만드세요.", ephemeral=True)
        return

    if sender_account == 공용계좌번호:
        await interaction.response.send_message("같은 계좌로는 입금할 수 없습니다.", ephemeral=True)
        return

    try:
        fee, _, _ = perform_transfer(
            sender_account,
            공용계좌번호,
            금액,
            memo=f"{sender_account} → 공용계좌({account_name})",
            transaction_type="공용계좌입금",
        )
    except ValueError:
        await interaction.response.send_message("입금 실패: 금액은 1원 이상이어야 합니다.", ephemeral=True)
        return
    except PermissionError as exc:
        await interaction.response.send_message(f"입금 실패: {exc}", ephemeral=True)
        return
    except RuntimeError:
        fee_amount = calculate_transaction_fee(금액)
        total_cost = 금액 + fee_amount
        if fee_amount:
            message = (
                f"입금 실패: 잔액 부족 (입금액: {format_number_4digit(금액)}원 + 수수료: {format_number_4digit(fee_amount)}원 = "
                f"총 {format_number_4digit(total_cost)}원이 필요합니다.)"
            )
        else:
            message = "입금 실패: 잔액이 부족합니다."
        await interaction.response.send_message(message, ephemeral=True)
        return

    embed = discord.Embed(title="💰 공용계좌 입금 완료", color=0x00FF00)
    embed.add_field(name="보내는 계좌", value=f"`{sender_account}`", inline=False)
    embed.add_field(name="받는 계좌", value=f"{account_name} (`{공용계좌번호}`)", inline=False)
    embed.add_field(name="입금액", value=f"{format_number_4digit(금액)}원", inline=True)
    if fee:
        embed.add_field(name="수수료", value=f"{format_number_4digit(fee)}원", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="관리자세금설정", description="[관리자 전용] 세금을 설정합니다")
async def cmd_set_tax(
    interaction: discord.Interaction, 세율: float, 징수주기: int
) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    if not 0 <= 세율 <= 100:
        await interaction.response.send_message("세율은 0%에서 100% 사이여야 합니다.", ephemeral=True)
        return

    if 징수주기 < 1:
        await interaction.response.send_message("징수 주기는 1일 이상이어야 합니다.", ephemeral=True)
        return

    settings = load_settings()
    settings["tax_system"] = {
        "enabled": True,
        "rate": 세율 / 100,
        "period_days": 징수주기,
        "last_collected": datetime.now().isoformat(),
        "tax_name": settings.get("tax_system", {}).get("tax_name", "세금"),
    }
    save_settings(settings)

    embed = discord.Embed(title="세금 설정 완료", color=0x00AAFF)
    embed.add_field(name="세율", value=f"{세율}%", inline=True)
    embed.add_field(name="징수 주기", value=f"{징수주기}일", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="관리자월급설정", description="[관리자 전용] 역할별 월급을 설정합니다")
async def cmd_set_salary(
    interaction: discord.Interaction,
    역할_id: str,
    월급: int,
    공용계좌번호: str,
    공용계좌비밀번호: str,
) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    if 월급 < 0:
        await interaction.response.send_message("월급은 0원 이상이어야 합니다.", ephemeral=True)
        return

    try:
        role_id = int(역할_id)
    except ValueError:
        await interaction.response.send_message("올바른 역할 ID를 입력해주세요.", ephemeral=True)
        return

    if not interaction.guild:
        await interaction.response.send_message("서버 정보를 가져올 수 없습니다.", ephemeral=True)
        return

    role = interaction.guild.get_role(role_id)
    if not role:
        await interaction.response.send_message("해당 역할을 찾을 수 없습니다.", ephemeral=True)
        return

    account_name = verify_public_account(공용계좌번호, 공용계좌비밀번호)
    if not account_name:
        await interaction.response.send_message("공용계좌 인증에 실패했습니다.", ephemeral=True)
        return

    if 공용계좌번호 not in load_users():
        await interaction.response.send_message("공용계좌를 찾을 수 없습니다.", ephemeral=True)
        return

    settings = load_settings()
    salary_config = settings.setdefault("salary_system", {})
    salary_config["enabled"] = True
    salary_config.setdefault("salaries", {})[str(role_id)] = 월급
    salary_config["source_account"] = {
        "account_number": 공용계좌번호,
        "password": 공용계좌비밀번호,
        "account_name": account_name,
    }
    save_settings(settings)

    embed = discord.Embed(title="💰 월급 설정 완료", color=0x00FF00)
    embed.add_field(name="역할", value=role.name, inline=True)
    embed.add_field(name="월급", value=f"{format_number_4digit(월급)}원", inline=True)
    embed.add_field(name="월급 지급원", value=f"{account_name} (`{공용계좌번호}`)", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="관리자거래내역", description="[관리자 전용] 다른 사용자의 거래내역을 조회합니다")
async def cmd_admin_transactions(interaction: discord.Interaction, 대상자: discord.Member) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    account_number = get_account_number_by_user(대상자.id)
    if not account_number:
        await interaction.response.send_message("계좌를 찾을 수 없습니다.", ephemeral=True)
        return

    transactions = load_transactions()
    user_transactions = [t for t in transactions if t["from_user"] == account_number or t["to_user"] == account_number][-15:]

    if not user_transactions:
        await interaction.response.send_message("거래내역이 없습니다.", ephemeral=True)
        return

    embed = discord.Embed(title=f"📊 {account_number}의 거래내역 (15건)", color=0xFF9900)
    for transaction in reversed(user_transactions):
        timestamp = datetime.fromisoformat(transaction["timestamp"]).strftime("%m/%d %H:%M")
        if transaction["from_user"] == account_number:
            desc = f"↗️ {transaction['type']} -{format_number_4digit(transaction['amount'])}원"
            if transaction["fee"]:
                desc += f" (수수료: {format_number_4digit(transaction['fee'])}원)"
        else:
            desc = f"↘️ {transaction['type']} +{format_number_4digit(transaction['amount'])}원"
        if transaction.get("memo"):
            desc += f"\n메모: {transaction['memo']}"
        embed.add_field(name=timestamp, value=desc, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="관리자계좌초기화", description="[관리자 전용] 특정 계좌를 초기화합니다")
async def cmd_reset_account(interaction: discord.Interaction, 대상자: discord.Member) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    account_number = get_account_number_by_user(대상자.id)
    if not account_number:
        await interaction.response.send_message("계좌를 찾을 수 없습니다.", ephemeral=True)
        return

    record = ensure_account_record(account_number)
    previous_balance = record.balance
    record.balance = 1_000_000
    update_account_record(record)

    add_transaction("관리자초기화", "SYSTEM", account_number, 1_000_000, 0, f"관리자 계좌 초기화: {interaction.user.display_name}")

    embed = discord.Embed(title="🔄 계좌 초기화 완료", color=0x00FF00)
    embed.add_field(name="대상 계좌", value=f"`{account_number}`", inline=False)
    embed.add_field(name="이전 잔액", value=f"{format_number_4digit(previous_balance)}원", inline=True)
    embed.add_field(name="현재 잔액", value="1,000,000원", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="관리자계좌현황", description="[관리자 전용] 개설된 모든 계좌 현황을 확인합니다")
async def cmd_account_overview(interaction: discord.Interaction) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    users = load_users()
    if not users:
        await interaction.response.send_message("개설된 계좌가 없습니다.", ephemeral=True)
        return

    records = [AccountRecord.from_dict(acc, data) for acc, data in users.items()]
    total_money = sum(record.balance for record in records)
    records.sort(key=lambda record: record.balance, reverse=True)

    embed = discord.Embed(title="🏦 전체 계좌 현황", color=0x0099FF)
    embed.add_field(name="총 계좌 수", value=f"{len(records)}개", inline=True)
    embed.add_field(name="총 유통 자금", value=f"{format_number_4digit(total_money)}원", inline=True)
    embed.add_field(name="평균 잔액", value=f"{format_number_4digit(total_money // len(records))}원", inline=True)

    details = []
    for idx, record in enumerate(records[:10], start=1):
        status = "🔒" if is_account_frozen(record.account_number) else "✅"
        details.append(f"{idx}. {status} `{record.account_number}` - {format_number_4digit(record.balance)}원")

    if len(records) > 10:
        details.append(f"... 외 {len(records) - 10}개 계좌")

    embed.add_field(name="상위 계좌 (잔액순)", value="\n".join(details), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


def create_excel_transactions(target_account: Optional[str] = None) -> Optional[str]:
    transactions = load_transactions()
    if target_account:
        filtered = [t for t in transactions if t["from_user"] == target_account or t["to_user"] == target_account]
    else:
        filtered = transactions

    if not filtered:
        return None

    rows = []
    for transaction in filtered:
        timestamp = datetime.fromisoformat(transaction["timestamp"])
        rows.append(
            {
                "거래일시": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "거래유형": transaction["type"],
                "보내는계좌": transaction["from_user"],
                "받는계좌": transaction["to_user"],
                "금액": transaction["amount"],
                "수수료": transaction["fee"],
                "메모": transaction["memo"],
            }
        )

    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp_file:
        df.to_excel(tmp_file.name, index=False, engine="openpyxl")
        return tmp_file.name


@bot.tree.command(name="엑셀내보내기", description="[관리자 전용] 거래내역을 엑셀로 내보냅니다")
async def cmd_export_transactions(interaction: discord.Interaction, 대상자: Optional[discord.Member] = None) -> None:
    if not ensure_admin(interaction):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    target_account: Optional[str] = None
    if 대상자:
        target_account = get_account_number_by_user(대상자.id)
        if not target_account:
            await interaction.followup.send("계좌를 찾을 수 없습니다.", ephemeral=True)
            return

    excel_path = create_excel_transactions(target_account)
    if not excel_path:
        await interaction.followup.send("내보낼 거래내역이 없습니다.", ephemeral=True)
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = (
        f"거래내역_{target_account}_{timestamp}.xlsx" if target_account else f"전체거래내역_{timestamp}.xlsx"
    )

    embed = discord.Embed(title="📤 엑셀 내보내기 완료", color=0x00FF00)
    if target_account:
        embed.description = f"📊 **{target_account}**의 거래내역을 엑셀로 내보냈습니다."
    else:
        embed.description = "📊 **전체 거래내역**을 엑셀로 내보냈습니다."
    embed.add_field(name="파일명", value=filename, inline=False)
    embed.add_field(name="생성시간", value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), inline=False)

    with open(excel_path, "rb") as file:
        await interaction.followup.send(embed=embed, file=discord.File(file, filename=filename), ephemeral=True)

    os.unlink(excel_path)


###############################################################################
# Background tasks
###############################################################################


def collect_tax_from_accounts() -> None:
    settings = load_settings()
    tax_config = settings.get("tax_system", {})
    if not tax_config.get("enabled"):
        return

    last_collected = tax_config.get("last_collected")
    if not last_collected:
        return

    period_days = tax_config.get("period_days", 30)
    last_collected_date = datetime.fromisoformat(last_collected)
    if (datetime.now() - last_collected_date).days < period_days:
        return

    users = load_users()
    tax_rate = tax_config.get("rate", 0.0)
    total_collected = 0

    for account_number, data in users.items():
        record = AccountRecord.from_dict(account_number, data)
        if record.balance <= 0 or record.is_public:
            continue
        tax_amount = int(record.balance * tax_rate)
        if tax_amount <= 0:
            continue
        record.balance = max(0, record.balance - tax_amount)
        users[account_number] = record.to_dict()
        total_collected += tax_amount

    if total_collected > 0:
        save_users(users)
        settings["tax_system"]["last_collected"] = datetime.now().isoformat()
        save_settings(settings)
        print(f"세금 일괄 징수 완료: 총 {format_number_4digit(total_collected)}원")


def pay_monthly_salaries_to_members() -> None:
    settings = load_settings()
    salary_config = settings.get("salary_system", {})
    if not salary_config.get("enabled"):
        return

    now = datetime.now()
    last_paid = salary_config.get("last_paid")
    if last_paid:
        last_paid_date = datetime.fromisoformat(last_paid)
        if last_paid_date.year == now.year and last_paid_date.month == now.month:
            return

    source_info = salary_config.get("source_account", {})
    source_account = source_info.get("account_number")
    source_password = source_info.get("password")
    source_name = source_info.get("account_name")

    if not (source_account and source_password and source_name):
        print("월급 지급 실패: 공용계좌가 설정되지 않았습니다.")
        return

    if not verify_public_account(source_account, source_password):
        print("월급 지급 실패: 공용계좌 인증 실패")
        return

    users = load_users()
    if source_account not in users:
        print("월급 지급 실패: 지급원 계좌를 찾을 수 없습니다.")
        return

    if is_account_frozen(source_account):
        print("월급 지급 실패: 지급원 계좌가 동결되어 있습니다.")
        return

    salaries: Dict[str, int] = salary_config.get("salaries", {})
    if not salaries:
        return

    total_needed = 0
    recipients: List[Tuple[str, int, List[str]]] = []

    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue
            account_number = get_account_number_by_user(member.id)
            if not account_number or account_number not in users:
                continue
            role_total = 0
            role_names: List[str] = []
            for role in member.roles:
                salary = salaries.get(str(role.id))
                if salary:
                    role_total += salary
                    role_names.append(role.name)
            if role_total > 0:
                total_needed += role_total
                recipients.append((account_number, role_total, role_names))

    source_record = AccountRecord.from_dict(source_account, users[source_account])
    if source_record.balance < total_needed:
        print(
            "월급 지급 실패: 공용계좌 잔액 부족 (필요: "
            f"{format_number_4digit(total_needed)}원, 보유: {format_number_4digit(source_record.balance)}원)"
        )
        return

    for account_number, amount, roles in recipients:
        recipient_record = AccountRecord.from_dict(account_number, users[account_number])
        source_record.balance -= amount
        recipient_record.balance += amount
        users[source_account] = source_record.to_dict()
        users[account_number] = recipient_record.to_dict()
        add_transaction(
            "월급지급",
            source_account,
            account_number,
            amount,
            0,
            f"월급 지급 ({', '.join(roles)}) - {source_name}",
        )
        print(f"월급 지급: {account_number} - {format_number_4digit(amount)}원")

    save_users(users)
    settings["salary_system"]["last_paid"] = now.isoformat()
    save_settings(settings)
    print(f"월급 일괄 지급 완료: 총 {format_number_4digit(total_needed)}원")


@tasks.loop(hours=24)
async def collect_taxes() -> None:
    await bot.wait_until_ready()
    collect_tax_from_accounts()


@tasks.loop(hours=24)
async def pay_monthly_salaries() -> None:
    await bot.wait_until_ready()
    if datetime.now().day == 1:
        pay_monthly_salaries_to_members()


###############################################################################
# Discord events
###############################################################################


@bot.event
async def on_ready() -> None:
    print(f"{bot.user} 로그인 완료!")
    if not collect_taxes.is_running():
        collect_taxes.start()
        print("세금 징수 작업이 시작되었습니다.")
    if not pay_monthly_salaries.is_running():
        pay_monthly_salaries.start()
        print("월급 지급 작업이 시작되었습니다.")
    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료!")
    except Exception as exc:  # pragma: no cover - logging only
        print(f"동기화 실패: {exc}")


###############################################################################
# Entrypoint
###############################################################################


def main() -> None:
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
