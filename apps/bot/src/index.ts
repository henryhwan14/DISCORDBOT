import "dotenv/config";
import { randomUUID } from "node:crypto";
import { setTimeout as wait } from "node:timers/promises";
import {
  ChatInputCommandInteraction,
  Client,
  EmbedBuilder,
  GatewayIntentBits,
  REST,
  Routes,
  SlashCommandBuilder,
} from "discord.js";
import { createOpenCloudClient } from "@discord-roblox/opencloud";
import { EconomyTransactionCommand, ProcessedTransactionRecord } from "@discord-roblox/shared";

const rawDiscordToken = process.env.DISCORD_TOKEN;
const rawDiscordAppId = process.env.DISCORD_APP_ID;
const rawUniverseId = process.env.ROBLOX_UNIVERSE_ID;
const rawApiKey = process.env.ROBLOX_API_KEY;
const API_BASE_URL = process.env.API_BASE_URL ?? "http://localhost:3000";

if (!rawDiscordToken || !rawDiscordAppId || !rawUniverseId || !rawApiKey) {
  throw new Error("Missing required environment configuration for Discord bot");
}

const DISCORD_TOKEN: string = rawDiscordToken;
const DISCORD_APP_ID: string = rawDiscordAppId;
const ROBLOX_UNIVERSE_ID: string = rawUniverseId;
const ROBLOX_API_KEY: string = rawApiKey;

const openCloud = createOpenCloudClient({ universeId: ROBLOX_UNIVERSE_ID, apiKey: ROBLOX_API_KEY });

const rest = new REST({ version: "10" }).setToken(DISCORD_TOKEN);

async function registerCommands() {
  const commands = [
    new SlashCommandBuilder()
      .setName("give")
      .setDescription("Credit Robux-equivalent balance for a Roblox user")
      .addStringOption((option) =>
        option.setName("user").setDescription("Roblox user id").setRequired(true),
      )
      .addIntegerOption((option) =>
        option
          .setName("amount")
          .setDescription("Amount to credit")
          .setRequired(true)
          .setMinValue(1),
      )
      .addStringOption((option) =>
        option.setName("reason").setDescription("Audit reason").setRequired(false),
      ),
    new SlashCommandBuilder()
      .setName("deduct")
      .setDescription("Debit balance for a Roblox user")
      .addStringOption((option) =>
        option.setName("user").setDescription("Roblox user id").setRequired(true),
      )
      .addIntegerOption((option) =>
        option
          .setName("amount")
          .setDescription("Amount to debit")
          .setRequired(true)
          .setMinValue(1),
      )
      .addStringOption((option) =>
        option.setName("reason").setDescription("Audit reason").setRequired(false),
      ),
    new SlashCommandBuilder()
      .setName("balance")
      .setDescription("Fetch the current wallet balance")
      .addStringOption((option) =>
        option.setName("user").setDescription("Roblox user id").setRequired(true),
      ),
    new SlashCommandBuilder()
      .setName("audit")
      .setDescription("List recent transactions")
      .addStringOption((option) =>
        option.setName("user").setDescription("Roblox user id").setRequired(false),
      )
      .addIntegerOption((option) =>
        option
          .setName("limit")
          .setDescription("Max transactions to display (default 5)")
          .setMinValue(1)
          .setMaxValue(20)
          .setRequired(false),
      ),
  ].map((command) => command.toJSON());

  await rest.put(Routes.applicationCommands(DISCORD_APP_ID), { body: commands });
}

async function waitForTransaction(txnId: string, userId: string, timeoutMs = 10_000): Promise<ProcessedTransactionRecord | undefined> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const profile = await openCloud.readEconomyProfile(userId);
    const record = profile.data?.processed?.find((entry) => entry.txnId === txnId);
    if (record) {
      return record;
    }
    await wait(500);
  }
  return undefined;
}

async function handleTransactionCommand(interaction: ChatInputCommandInteraction, delta: number) {
  const userId = interaction.options.getString("user", true);
  const amount = interaction.options.getInteger("amount", true);
  const reason = interaction.options.getString("reason") ?? undefined;

  const signedDelta = delta * amount;
  const txnId = randomUUID();

  const command: EconomyTransactionCommand = {
    txnId,
    userId,
    delta: signedDelta,
    reason,
    actor: `${interaction.user.tag}`,
    source: "discord",
  };

  await interaction.deferReply({ ephemeral: true });

  try {
    await openCloud.publishDiscordCommand(command);
    const record = await waitForTransaction(txnId, userId);
    if (!record) {
      await interaction.editReply(
        `Command ${txnId} dispatched. Confirmation not received yet; check again shortly.`,
      );
      return;
    }

    const embed = new EmbedBuilder()
      .setTitle(`Transaction ${record.txnId}`)
      .setDescription("Update processed via Roblox ledger")
      .addFields(
        { name: "User", value: userId, inline: true },
        { name: "Delta", value: `${record.delta}`, inline: true },
        { name: "Balance", value: `${record.balanceAfter}`, inline: true },
      )
      .setFooter({ text: `Actor: ${record.actor}` })
      .setTimestamp(record.processedAt);

    if (record.reason) {
      embed.addFields({ name: "Reason", value: record.reason, inline: false });
    }

    await interaction.editReply({ content: "Transaction confirmed", embeds: [embed] });
  } catch (err) {
    console.error("Failed to process transaction", err);
    await interaction.editReply(`Failed to dispatch transaction: ${(err as Error).message}`);
  }
}

async function handleBalance(interaction: ChatInputCommandInteraction) {
  const userId = interaction.options.getString("user", true);
  await interaction.deferReply({ ephemeral: true });
  try {
    const profile = await openCloud.readEconomyProfile(userId);
    const balance = profile.data?.balance ?? 0;
    const processed = profile.data?.processed ?? [];
    const lastTxn = processed.length ? processed[processed.length - 1].txnId : undefined;
    const embed = new EmbedBuilder()
      .setTitle(`Balance for ${userId}`)
      .addFields({ name: "Balance", value: `${balance}`, inline: true })
      .setFooter({ text: lastTxn ? `Last txn: ${lastTxn}` : "No transactions recorded" });
    await interaction.editReply({ embeds: [embed] });
  } catch (err) {
    console.error("Failed to fetch balance", err);
    await interaction.editReply(`Failed to fetch balance: ${(err as Error).message}`);
  }
}

async function handleAudit(interaction: ChatInputCommandInteraction) {
  const userId = interaction.options.getString("user") ?? undefined;
  const limit = interaction.options.getInteger("limit") ?? 5;
  await interaction.deferReply({ ephemeral: true });
  try {
    const url = new URL("/log/transactions", API_BASE_URL);
    if (userId) url.searchParams.set("userId", userId);
    url.searchParams.set("limit", String(limit));
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`API responded ${response.status}`);
    }
    const logs = (await response.json()) as Array<{
      txnId: string;
      userId: string;
      delta: number;
      actor: string;
      source: string;
      createdAt: string;
      reason?: string | null;
    }>;

    if (!logs.length) {
      await interaction.editReply("No audit entries found.");
      return;
    }

    const description = logs
      .map((entry) => {
        const reasonSuffix = entry.reason ? ` — ${entry.reason}` : "";
        return `• **${entry.txnId}** | ${entry.delta} | ${entry.userId} | ${entry.source}${reasonSuffix} | ${new Date(entry.createdAt).toLocaleString()}`;
      })
      .join("\n");

    const embed = new EmbedBuilder().setTitle("Recent transactions").setDescription(description);
    await interaction.editReply({ embeds: [embed] });
  } catch (err) {
    console.error("Failed to fetch audit log", err);
    await interaction.editReply(`Failed to fetch audit log: ${(err as Error).message}`);
  }
}

const client = new Client({ intents: [GatewayIntentBits.Guilds] });

client.once("ready", () => {
  console.log(`Logged in as ${client.user?.tag}`);
});

client.on("interactionCreate", async (interaction) => {
  if (!interaction.isChatInputCommand()) return;
  switch (interaction.commandName) {
    case "give":
      await handleTransactionCommand(interaction, +1);
      break;
    case "deduct":
      await handleTransactionCommand(interaction, -1);
      break;
    case "balance":
      await handleBalance(interaction);
      break;
    case "audit":
      await handleAudit(interaction);
      break;
    default:
      await interaction.reply({ content: "Unknown command", ephemeral: true });
      break;
  }
});

async function main() {
  await registerCommands();
  await client.login(DISCORD_TOKEN);
}

main().catch((err) => {
  console.error("Bot failed to start", err);
  process.exit(1);
});
