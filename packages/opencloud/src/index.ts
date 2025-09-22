import crypto from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import {
  applyIdempotentTransaction,
  EconomyEventBroadcast,
  EconomyTransactionCommand,
  ProcessedTransactionRecord,
  ProcessedTxnBuffer,
  TransactionResult,
} from "@discord-roblox/shared";

const DEFAULT_BASE_URL = "https://apis.roblox.com";
const ECONOMY_DATASTORE_NAME = process.env.ROBLOX_DATASTORE_NAME ?? "EconomyWallet";
const ECONOMY_SCOPE = "global";

export interface OpenCloudClientOptions {
  universeId: string;
  apiKey: string;
  baseUrl?: string;
  maxRetries?: number;
  backoffMs?: number;
}

interface RequestOptions extends RequestInit {
  path: string;
  query?: Record<string, string | number | undefined>;
  retryable?: boolean;
}

interface DataStoreEntry<T> {
  data: T | null;
  version?: string;
}

export interface EconomyProfileData {
  balance: number;
  processed: ProcessedTransactionRecord[];
}

export interface ApplyEconomyTransactionParams {
  userId: string;
  command: EconomyTransactionCommand;
}

export interface ApplyEconomyTransactionResult extends TransactionResult {
  profile: EconomyProfileData;
}

export interface OpenCloudClient {
  readEconomyProfile(userId: string): Promise<DataStoreEntry<EconomyProfileData>>;
  applyEconomyTransaction(params: ApplyEconomyTransactionParams): Promise<ApplyEconomyTransactionResult>;
  publishDiscordCommand(command: EconomyTransactionCommand): Promise<void>;
  publishEconomyBroadcast(event: EconomyEventBroadcast["payload"]): Promise<void>;
}

interface RobloxError {
  message: string;
  code: number;
  details?: unknown;
}

async function withBackoff<T>(fn: () => Promise<T>, opts: { retries: number; baseDelay: number }): Promise<T> {
  let attempt = 0;
  let lastErr: unknown;
  while (attempt <= opts.retries) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      if (attempt === opts.retries) {
        break;
      }
      const waitMs = opts.baseDelay * 2 ** attempt + Math.random() * 100;
      await delay(waitMs);
      attempt += 1;
    }
  }
  throw lastErr;
}

export function createOpenCloudClient(options: OpenCloudClientOptions): OpenCloudClient {
  const baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
  const maxRetries = options.maxRetries ?? 4;
  const backoffMs = options.backoffMs ?? 250;

  async function request(req: RequestOptions): Promise<Response> {
    const url = new URL(req.path, baseUrl);
    if (req.query) {
      for (const [key, value] of Object.entries(req.query)) {
        if (value !== undefined) {
          url.searchParams.set(key, String(value));
        }
      }
    }

    const headers = new Headers({
      Accept: "application/json",
      "x-api-key": options.apiKey,
    });

    if (req.headers) {
      const custom = new Headers(req.headers as HeadersInit);
      custom.forEach((value, key) => headers.set(key, value));
    }

    if (!headers.has("Content-Type") && req.body) {
      headers.set("Content-Type", "application/json");
    }

    return withBackoff(
      async () => {
        const response = await fetch(url, {
          ...req,
          headers,
        });

        if (response.status === 429 || response.status >= 500) {
          const retryAfter = response.headers.get("Retry-After");
          const delayMs = retryAfter ? Number(retryAfter) * 1000 : undefined;
          if (delayMs) {
            await delay(delayMs);
          }
          throw new Error(`Roblox API rate limited or transient failure (${response.status})`);
        }

        if (!response.ok) {
          const body = await safeJson<RobloxError>(response);
          const error = new Error(`Roblox API request failed: ${response.status} ${response.statusText} ${body?.message ?? ""}`);
          (error as Error & { code?: number }).code = body?.code;
          throw error;
        }

        return response;
      },
      { retries: req.retryable === false ? 0 : maxRetries, baseDelay: backoffMs },
    );
  }

  async function safeJson<T>(response: Response): Promise<T | undefined> {
    try {
      return (await response.clone().json()) as T;
    } catch (err) {
      return undefined;
    }
  }

  async function getDataStoreEntry<T>(entryKey: string): Promise<DataStoreEntry<T>> {
    try {
      const response = await request({
        path: `/datastores/v1/universes/${options.universeId}/standard-datastores/datastore/entries/entry`,
        query: {
          datastoreName: ECONOMY_DATASTORE_NAME,
          scope: ECONOMY_SCOPE,
          entryKey,
        },
        method: "GET",
      });

      if (response.status === 204) {
        return { data: null };
      }

      const version = response.headers.get("roblox-entry-version") ?? undefined;
      const data = (await response.json()) as T;
      return { data, version };
    } catch (err) {
      if (err instanceof Error && (err as Error & { code?: number }).code === 1) {
        // Missing entry -> treat as null so we can create on first write.
        return { data: null };
      }
      throw err;
    }
  }

  async function postDataStoreEntry<T>({ entryKey, data, version }: { entryKey: string; data: T; version?: string }) {
    return request({
      path: `/datastores/v1/universes/${options.universeId}/standard-datastores/datastore/entries/entry`,
      query: {
        datastoreName: ECONOMY_DATASTORE_NAME,
        scope: ECONOMY_SCOPE,
        entryKey,
        matchVersion: version,
      },
      method: "POST",
      body: JSON.stringify(data),
      retryable: false,
    });
  }

  async function updateEconomyProfile(userId: string, command: EconomyTransactionCommand): Promise<ApplyEconomyTransactionResult> {
    const entryKey = `wallet:${userId}`;

    return withBackoff(
      async () => {
        const current = await getDataStoreEntry<EconomyProfileData>(entryKey);
        const profile: EconomyProfileData = current.data ?? {
          balance: 0,
          processed: [],
        };

        const buffer = new ProcessedTxnBuffer();
        for (const record of profile.processed) {
          buffer.record(record);
        }

        const result = applyIdempotentTransaction(
          profile.balance,
          {
            txnId: command.txnId,
            delta: command.delta,
            actor: command.actor,
            source: command.source,
            reason: command.reason,
          },
          buffer,
        );

        if (!result.processed) {
          return { ...result, profile };
        }

        const updatedProfile: EconomyProfileData = {
          balance: result.balance,
          processed: buffer.listNewestFirst().reverse(),
        };

        try {
          await postDataStoreEntry({ entryKey, data: updatedProfile, version: current.version });
        } catch (err) {
          if (err instanceof Error && (err as Error & { code?: number }).code === 11) {
            // Version conflict -> re-run outer loop to simulate UpdateAsync retry semantics.
            throw err;
          }
          throw err;
        }

        return { ...result, profile: updatedProfile };
      },
      { retries: maxRetries, baseDelay: backoffMs },
    );
  }

  async function publishMessage(topic: string, payload: unknown): Promise<void> {
    const path = `/cloud/messaging/v1/universes/${options.universeId}/topics/${encodeURIComponent(topic)}`;
    const serialized = JSON.stringify(payload);
    const body = JSON.stringify({ message: serialized });
    await request({
      path,
      method: "POST",
      body,
      headers: {
        "Content-Type": "application/json",
        "Content-MD5": crypto.createHash("md5").update(body).digest("base64"),
      },
    });
  }

  return {
    async readEconomyProfile(userId: string) {
      return getDataStoreEntry<EconomyProfileData>(`wallet:${userId}`);
    },
    async applyEconomyTransaction(params) {
      return updateEconomyProfile(params.userId, params.command);
    },
    async publishDiscordCommand(command) {
      await publishMessage("discord-commands", { type: "economy.command", payload: command });
    },
    async publishEconomyBroadcast(event) {
      await publishMessage(`economy-events:${event.userId}`, { type: "economy.update", payload: event });
    },
  };
}
