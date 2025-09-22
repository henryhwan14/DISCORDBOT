import { EconomySource, ProcessedTransactionRecord, TransactionResult } from "./types";

/**
 * Fixed-size ring buffer dedicated to keeping the last N processed transaction
 * identifiers.  Roblox DataStore does not provide native support for
 * UpdateAsync-style conflict detection outside of Lua, so we replicate the
 * behaviour in memory.  This buffer ensures that any retry carrying the same
 * txnId is observed and short-circuited before mutating state.
 */
export class ProcessedTxnBuffer {
  private readonly buffer: Array<ProcessedTransactionRecord | null>;
  private readonly size: number;
  private cursor = 0;
  private readonly lookup = new Map<string, ProcessedTransactionRecord>();

  constructor(size = 64) {
    if (size <= 0) {
      throw new Error("Ring buffer size must be positive");
    }
    this.size = size;
    this.buffer = new Array(size).fill(null);
  }

  /**
     * Attempt to record a transaction. Returns the processed record if the
     * transaction was new, otherwise returns the existing record so callers can
     * maintain idempotency semantics.
     */
  record(txn: ProcessedTransactionRecord): { inserted: boolean; record: ProcessedTransactionRecord } {
    const existing = this.lookup.get(txn.txnId);
    if (existing) {
      return { inserted: false, record: existing };
    }

    const displaced = this.buffer[this.cursor];
    if (displaced) {
      this.lookup.delete(displaced.txnId);
    }

    this.buffer[this.cursor] = txn;
    this.lookup.set(txn.txnId, txn);
    this.cursor = (this.cursor + 1) % this.size;

    return { inserted: true, record: txn };
  }

  get(txnId: string): ProcessedTransactionRecord | undefined {
    return this.lookup.get(txnId);
  }

  listNewestFirst(): ProcessedTransactionRecord[] {
    const result: ProcessedTransactionRecord[] = [];
    for (let i = 0; i < this.size; i += 1) {
      const idx = (this.cursor - 1 - i + this.size) % this.size;
      const entry = this.buffer[idx];
      if (entry) {
        result.push(entry);
      }
    }
    return result;
  }
}

/**
 * Apply a delta to the provided balance with strict idempotency semantics.
 * Returns the resulting balance and a record of the transaction.  The record is
 * only inserted into the ring buffer if the transaction id has not been seen.
 */
export function applyIdempotentTransaction(
  currentBalance: number,
  params: {
    txnId: string;
    delta: number;
    actor: string;
    source: EconomySource;
    reason?: string;
    timestamp?: number;
  },
  buffer: ProcessedTxnBuffer,
): TransactionResult {
  const record: ProcessedTransactionRecord = {
    txnId: params.txnId,
    delta: params.delta,
    balanceAfter: currentBalance + params.delta,
    actor: params.actor,
    source: params.source,
    reason: params.reason,
    processedAt: params.timestamp ?? Date.now(),
  };

  const { inserted, record: persisted } = buffer.record(record);

  return {
    balance: inserted ? record.balanceAfter : currentBalance,
    processed: inserted,
    record: persisted,
  };
}
