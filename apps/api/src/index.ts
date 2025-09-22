import "dotenv/config";
import crypto from "node:crypto";
import http from "node:http";
import express, { NextFunction, Request, Response } from "express";
import cors from "cors";
import helmet from "helmet";
import morgan from "morgan";
import { PrismaClient, Prisma } from "@prisma/client";
import { ApiTransactionLogResponse, EconomyEventBroadcast, HealthStatus } from "@discord-roblox/shared";

const PORT = Number(process.env.PORT ?? 3000);
const rawSecret = process.env.HMAC_SECRET;

if (!rawSecret) {
  throw new Error("HMAC_SECRET is required to start the API server");
}

const HMAC_SECRET: string = rawSecret;

const prisma = new PrismaClient();

interface RawBodyRequest extends Request {
  rawBody?: Buffer;
}

const app = express();
app.use(helmet());
app.use(cors());
app.use(
  express.json({
    verify: (req: RawBodyRequest, _res, buf) => {
      req.rawBody = Buffer.from(buf);
    },
  }),
);
app.use(morgan("combined"));

function asyncHandler<T extends Request>(handler: (req: T, res: Response, next: NextFunction) => Promise<void>) {
  return (req: T, res: Response, next: NextFunction) => {
    handler(req, res, next).catch(next);
  };
}

function verifySignature(payload: unknown, signature?: string): boolean {
  if (!signature) return false;
  const body = Buffer.from(JSON.stringify(payload));
  const hmac = crypto.createHmac("sha256", HMAC_SECRET);
  hmac.update(body);
  const expected = hmac.digest("hex");
  const normalized = signature.toLowerCase();
  if (expected.length !== normalized.length) {
    return false;
  }
  try {
    return crypto.timingSafeEqual(Buffer.from(normalized, "hex"), Buffer.from(expected, "hex"));
  } catch (err) {
    return false;
  }
}

function hashPayload(payload: unknown): string {
  const hash = crypto.createHash("sha256");
  hash.update(JSON.stringify(payload));
  return hash.digest("hex");
}

app.get(
  "/health",
  asyncHandler(async (_req, res) => {
    const status: HealthStatus = { status: "ok", timestamp: new Date().toISOString() };
    res.json(status);
  }),
);

app.post(
  "/log/transactions",
  asyncHandler(async (req: RawBodyRequest, res) => {
    const signatureHeader = req.header("X-Signature");
    const idempotencyKey = req.header("Idempotency-Key") ?? req.body?.idempotencyKey;
    const body = req.body as { payload?: EconomyEventBroadcast["payload"]; signature?: string };

    const signature = signatureHeader ?? body.signature;
    const payload = body?.payload;

    if (!payload) {
      res.status(400).json({ error: "payload is required" });
      return;
    }

    if (!verifySignature(payload, signature)) {
      res.status(401).json({ error: "invalid signature" });
      return;
    }

    if (!idempotencyKey) {
      res.status(400).json({ error: "Idempotency-Key header required" });
      return;
    }

    const payloadHash = hashPayload(payload);

    const result = await prisma.$transaction(async (tx: Prisma.TransactionClient) => {
      try {
        await tx.webhookDelivery.create({
          data: {
            key: idempotencyKey,
            payloadHash,
          },
        });
      } catch (err) {
        const existing = await tx.webhookDelivery.findUnique({ where: { key: idempotencyKey } });
        if (!existing || existing.payloadHash !== payloadHash) {
          throw new Error("Idempotency key conflict");
        }
        return { accepted: true, deduped: true } satisfies ApiTransactionLogResponse;
      }

      await tx.auditLog.upsert({
        where: { txnId: payload.txnId },
        create: {
          txnId: payload.txnId,
          userId: payload.userId,
          delta: payload.delta,
          actor: payload.actor,
          source: payload.source,
          reason: payload.reason ?? null,
        },
        update: {},
      });
      return { accepted: true, deduped: false } satisfies ApiTransactionLogResponse;
    });

    res.json(result);
  }),
);

app.get(
  "/log/transactions",
  asyncHandler(async (req, res) => {
    const limit = Math.min(Number(req.query.limit ?? 20), 100);
    const userId = req.query.userId ? String(req.query.userId) : undefined;
    const entries = await prisma.auditLog.findMany({
      where: userId ? { userId } : undefined,
      orderBy: { createdAt: "desc" },
      take: limit,
    });
    res.json(entries);
  }),
);

app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  // Centralised error handler keeps stack traces out of the response body.
  console.error("API error", err);
  res.status(500).json({ error: "internal_error" });
});

const server = http.createServer(app);

server.listen(PORT, () => {
  console.log(`API server listening on :${PORT}`);
});
