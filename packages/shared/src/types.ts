/**
 * Shared type definitions consumed by both the Discord bot (Node.js) and the
 * Roblox server scripts via emitted typings/metadata.  The goal is to ensure a
 * single source of truth for payload shapes moving through messaging topics and
 * the audit log.
 */

export type EconomySource = "discord" | "game";

export interface EconomyTransactionCommand {
  /** Globally unique transaction id (UUID v4). */
  txnId: string;
  /** Roblox user id. */
  userId: string;
  /** Signed amount to apply to the balance. Positive = credit, negative = debit. */
  delta: number;
  /** Human readable reason for auditing. */
  reason?: string;
  /** Actor that initiated the change. Discord user tag or Roblox server id. */
  actor: string;
  /** Where the command originated from. */
  source: EconomySource;
}

export interface EconomyBalanceState {
  userId: string;
  balance: number;
  lastTxnId?: string;
  updatedAt: number;
}

export interface EconomyEventBroadcast {
  type: "economy.update";
  payload: {
    txnId: string;
    userId: string;
    delta: number;
    balance: number;
    actor: string;
    source: EconomySource;
    reason?: string;
    /** ISO timestamp string. */
    occurredAt: string;
  };
}

export interface DiscordCommandEnvelope {
  type: "economy.command";
  payload: EconomyTransactionCommand;
}

export type MessagingEnvelope = EconomyEventBroadcast | DiscordCommandEnvelope;

export interface AuditLogEntry {
  txnId: string;
  userId: string;
  delta: number;
  actor: string;
  source: EconomySource;
  reason?: string;
  createdAt: Date;
}

export interface AuditQuery {
  userId?: string;
  limit?: number;
}

export interface TransactionResult {
  balance: number;
  processed: boolean;
  record: ProcessedTransactionRecord;
}

export interface ProcessedTransactionRecord {
  txnId: string;
  delta: number;
  balanceAfter: number;
  actor: string;
  source: EconomySource;
  processedAt: number;
  reason?: string;
}

export interface IdempotencyRequestContext {
  txnId: string;
  userId: string;
  delta: number;
}

export type HealthStatus =
  | { status: "ok"; timestamp: string }
  | { status: "degraded"; reason: string; timestamp: string };

export interface ApiTransactionLogRequest {
  signature: string;
  /** Optional idempotency key to dedupe webhook retries. */
  idempotencyKey?: string;
  payload: EconomyEventBroadcast["payload"];
}

export interface ApiTransactionLogResponse {
  accepted: boolean;
  deduped?: boolean;
}
