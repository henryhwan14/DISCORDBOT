# Discord ↔ Roblox Economy Bridge

Production-oriented reference implementation that connects a Discord bot to a
Roblox experience with a single wallet ledger shared across every live server.
The system enforces idempotent transactions, ProfileService session locks, and
full audit logging.

## Architecture

```
┌──────────┐      Discord Slash Commands     ┌──────────┐
│ Discord  │ ───────────────────────────────▶│  Bot     │
│  Users   │                                 │ (Node)   │
└──────────┘                                 └────▲─────┘
                                                    │
                                                    │ Open Cloud Messaging
                                                    │ + DataStore (poll)
                                             ┌──────┴─────┐
                                             │ Roblox    │
                                             │ Servers   │◀─┐  Economy events
                                             │ (7x)      │  ├───────────────────┐
                                             └──▲─────┬──┘  │                   │
                                                │     │     │                   │
                         Profile lock + ledger ─┘     │     │                   │
                                                      │     │                   │
                                   Audit webhook ◀────┘     │                   │
                                                      │      ▼                  │
                                              ┌───────┴──────┐                 │
                                              │ Express API  │                 │
                                              │  + SQLite    │─────────────────┘
                                              └──────────────┘
```

Key flows:

1. Slash commands (`/give`, `/deduct`, `/balance`, `/audit`) arrive at the
   Discord bot.  Mutating commands publish an `economy.command` envelope to the
   Roblox Open Cloud Messaging topic `discord-commands`.
2. Every Roblox game server listens for commands. Only the server that holds the
   ProfileService session lock for a user mutates the wallet data. The script
   applies idempotent updates, persists the last 64 transactions, publishes a
   broadcast on `economy-events:{userId}`, and forwards the transaction to the
   Node API for audit logging.
3. The Discord bot polls the Open Cloud DataStore to confirm that the
   transaction committed. `/balance` simply reads the ledger. `/audit` calls the
   API server which surfaces Prisma-backed SQLite audit rows.
4. The Express API validates HMAC signatures and idempotency keys before
   recording the audit entry.

## Repository layout

| Path | Description |
| --- | --- |
| `apps/bot` | Discord bot written with `discord.js` v14. |
| `apps/api` | Express API server that handles health checks and audit log ingestion. |
| `packages/opencloud` | Open Cloud DataStore + Messaging client with retry/backoff. |
| `packages/shared` | Shared TypeScript types and the 64-slot processed transaction ring buffer. |
| `roblox/ServerScript.service.lua` | ProfileService-based Roblox server script. |
| `infra/docker-compose.yml` | Development docker-compose file (API + SQLite volume). |
| `Procfile` | Process definitions for Procfile-based hosting. |

## Prerequisites

- Node.js 20+
- pnpm 8+
- SQLite 3 (for local development)
- Discord application with a bot token and application ID
- Roblox Open Cloud API key with Messaging + DataStore permissions
- Roblox experience configured to include the provided server script

Create a `.env` file in the repository root based on `.env.sample`:

```dotenv
DISCORD_TOKEN=...
DISCORD_APP_ID=...
ROBLOX_UNIVERSE_ID=...
ROBLOX_API_KEY=...
ROBLOX_DATASTORE_NAME=EconomyWallet
HMAC_SECRET=super-secret-hex
DATABASE_URL="file:./data/audit.db"
API_BASE_URL="http://localhost:3000"
```

## Installation & building

```bash
pnpm install
pnpm prisma:generate    # generates Prisma client in apps/api
pnpm build              # type-check and emit JS for every workspace package
```

## Running locally

### Discord bot

```bash
pnpm --filter bot dev
```

The bot registers the slash commands on startup and logs in using the token
from the environment.

### API server

```bash
pnpm --filter api prisma migrate deploy
pnpm --filter api dev
```

Alternatively run both bot and API in process managers (Heroku, fly.io) using
`Procfile`, or launch the API with Docker Compose:

```bash
cd infra
docker compose up
```

The Compose file mounts the repository into `/workspace`, runs `pnpm install`,
applies migrations, and starts the Express dev server with live reload.

### Roblox server script wiring

1. Add [ProfileService](https://github.com/MadStudioRoblox/ProfileService) under
   `ServerScriptService/ProfileService`.
2. Place `roblox/ServerScript.service.lua` inside `ServerScriptService`.
3. Create `ServerStorage/EconomySecrets` ModuleScript returning:

   ```lua
   return {
     TransactionLogEndpoint = "https://your.api.host/log/transactions",
     HmacSecret = "super-secret-hex"
   }
   ```
4. (Optional) Add a `RemoteEvent` named `EconomyUpdated` under
   `ReplicatedStorage` for UI refreshes.
5. Enable Studio API access to Open Cloud Messaging/DataStore and configure the
   universe-level API key.

## Multi-server consistency testing

Roblox Studio provides a simple way to simulate the seven-server topology:

1. Open the place and configure **Test** → **Start Servers** to `7` with
   `1` player each.
2. Click **Start** to spawn seven independent jobs. Each job subscribes to the
   `discord-commands` topic.
3. Trigger `/give` or `/deduct` from Discord. Only the server that currently
   hosts the player (or the offline worker that acquires the ProfileService
   lock) mutates the wallet. Observe the Studio output — other jobs receive the
   command but immediately no-op, guaranteeing a single writer.
4. Use `/balance` from Discord or the in-game UI on any server. Every server
   receives the resulting `economy.update` broadcast and shows the identical
   balance value, satisfying the acceptance criteria.
5. For crash recovery, stop one server in Studio while it holds a lock. After
   the 30s ProfileService session timeout, re-issue a command; another server
   acquires the lock and continues processing without human intervention.

## Idempotent transaction verification

1. Execute `/give user:123 amount:10 reason:"test"`.
2. Immediately re-run the same command with `txnId` reused (copy the ID from the
   first response and use `/give`'s optional `reason` field to supply it).
3. The Roblox server script detects the existing transaction in the 64-slot ring
   buffer, skips mutation, and the Discord bot reports that the command was
   already processed. The audit API also returns `deduped: true` for webhook
   retries because of the Prisma-backed idempotency table.

## Audit log access

- `POST /log/transactions` (Roblox → API) requires an `Idempotency-Key` header
  and `X-Signature` (HMAC-SHA256 of the payload). The Express middleware checks
  signature + idempotency, writes to SQLite, and returns `{ accepted: true }`.
- `GET /log/transactions?userId=123&limit=10` is used by the `/audit` Discord
  command to enumerate the most recent transactions.

## Rate limiting & retries

- Open Cloud requests are wrapped in exponential backoff (base 250ms, capped by
  `maxRetries`) in `@discord-roblox/opencloud`.
- Messaging publishes include MD5 hashes as recommended by Roblox to guarantee
  payload integrity.
- Express API centralises error handling to avoid leaking stack traces.

## Failure-handling scenarios

| Scenario | Recovery strategy |
| --- | --- |
| **Profile lock owner crashes** | ProfileService releases the session after its heartbeat timeout. Other servers retry and acquire the lock automatically. |
| **Duplicate command** | The ring buffer short-circuits replays; DataStore balance stays unchanged and audit log deduplicates via idempotency keys. |
| **Messaging delivery gaps** | Discord bot polls DataStore (`waitForTransaction`) so transient publish failures still confirm via persisted state. A fallback task in Roblox can republish the latest balance periodically. |
| **API unreachable** | Roblox script logs a warning and retries on the next transaction; balance consistency is unaffected because the ledger write already succeeded. |
| **Budget exhaustion (Open Cloud/ Messaging)** | Backoff and jitter reduce contention. For sustained limits, lower command throughput or shard per-universe topics as outlined in the operations FAQ. |

## Operations FAQ

- **How do we keep the budget under the cap?**
  - Batch non-urgent adjustments and execute them during off-peak hours.
  - Tune `maxRetries`/`backoffMs` in `createOpenCloudClient` to align with the
    current Messaging/DataStore budget.
  - Use `/audit` instead of custom tooling to avoid redundant reads.
- **What if a Messaging publish never arrives?**
  - The Roblox script persists every committed transaction and publishes
    `economy.update`. Discord re-reads the DataStore when confirmations take too
    long, so the ledger remains authoritative. You can also create a watchdog in
    Roblox that republishes the most recent state on a fixed cadence.
- **How do we rotate secrets?**
  - Replace the Open Cloud API key and Discord token in the `.env` file and
    restart the processes. Update `ServerStorage/EconomySecrets` for the Roblox
    endpoint/HMAC secret. The Prisma SQLite database is unaffected.
- **How do we simulate outages?**
  - Stop the Roblox server that owns a session and confirm another server picks
    it up. Kill the API container to verify that the bot still reports balances
    (it will log warnings due to missing audit responses).

## Testing checklist

- Run `pnpm build` to ensure every package compiles.
- Launch seven Roblox servers in Studio to validate single-writer behaviour.
- Execute the Discord commands to confirm:
  - `/balance` returns identical values regardless of which server the player
    is on.
  - Replaying a command with the same `txnId` is ignored.
  - Audit logs list the last **N** entries.

## Additional notes

- The Roblox script depends on the standard ProfileService module; include it in
  your experience and keep it updated.
- `EconomySecrets` keeps production credentials out of source control. Adopt the
  same pattern for any remote events, HTTP endpoints, or feature flags.
- Extend the system with more commands by reusing the shared types and
  `ProcessedTxnBuffer` utility to remain idempotent.
