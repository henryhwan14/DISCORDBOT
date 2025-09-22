--!strict
--[[
  Roblox server authoritative economy handler.
  Responsibilities:
    * Maintain ProfileService-backed session locks per user
    * Apply idempotent transactions received via MessagingService
    * Publish economy state updates for UI caches and external services
    * Forward processed transactions to the Node.js API for auditing

  Design notes:
    - Only the server holding the ProfileService lock mutates wallet data.
      Other servers will receive the command but simply no-op, ensuring a
      single writer per user even when seven game servers are active.
    - The processed transaction ring buffer mirrors the shared TypeScript
      implementation to guarantee "exactly once" semantics for a txnId.
    - Secrets (HMAC, API endpoint) are required from ServerStorage to avoid
      leaking keys in the repository.  Provide a ModuleScript named
      `EconomySecrets` returning `{ TransactionLogEndpoint: string, HmacSecret: string }`.
]]

local Players = game:GetService("Players")
local MessagingService = game:GetService("MessagingService")
local HttpService = game:GetService("HttpService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local ProfileService = require(script:WaitForChild("ProfileService"))
local Secrets = require(game:GetService("ServerStorage"):WaitForChild("EconomySecrets"))

local API_ENDPOINT: string = Secrets.TransactionLogEndpoint
local HMAC_SECRET: string = Secrets.HmacSecret

local PROFILE_TEMPLATE = {
sessionVersion = 1,
balance = 0,
processed = {},
}

local RING_SIZE = 64
local COMMAND_TOPIC = "discord-commands"
local EVENT_TOPIC_PREFIX = "economy-events:"

-- Utility: modulo 2^32 arithmetic to keep SHA256 maths in bounds.
local function mod32(value: number): number
return value % 0x100000000
end

local bit = bit32
local band = bit.band
local bor = bit.bor
local bxor = bit.bxor
local bnot = bit.bnot
local rrotate = bit.rrotate
local rshift = bit.rshift
local lshift = bit.lshift

local function toBytes(words: { number }): string
local out = table.create(#words * 4)
for _, word in ipairs(words) do
table.insert(out, string.char(band(rshift(word, 24), 0xFF)))
table.insert(out, string.char(band(rshift(word, 16), 0xFF)))
table.insert(out, string.char(band(rshift(word, 8), 0xFF)))
table.insert(out, string.char(band(word, 0xFF)))
end
return table.concat(out)
end

local K = {
0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5, 0x3956C25B, 0x59F111F1, 0x923F82A4, 0xAB1C5ED5,
0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3, 0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174,
0xE49B69C1, 0xEFBE4786, 0x0FC19DC6, 0x240CA1CC, 0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7, 0xC6E00BF3, 0xD5A79147, 0x06CA6351, 0x14292967,
0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13, 0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85,
0xA2BFE8A1, 0xA81A664B, 0xC24B8B70, 0xC76C51A3, 0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5, 0x391C0CB3, 0x4ED8AA4A, 0x5B9CCA4F, 0x682E6FF3,
0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208, 0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2,
}

local function sha256Binary(message: string): string
local messageLength = #message
local bytes = { string.byte(message, 1, messageLength) }
table.insert(bytes, 0x80)
while (#bytes % 64) ~= 56 do
table.insert(bytes, 0x00)
end
local bitLength = messageLength * 8
local high = math.floor(bitLength / 0x100000000)
local low = bitLength % 0x100000000
for shift = 24, 0, -8 do
table.insert(bytes, band(rshift(high, shift), 0xFF))
end
for shift = 24, 0, -8 do
table.insert(bytes, band(rshift(low, shift), 0xFF))
end

local h0 = 0x6A09E667
local h1 = 0xBB67AE85
local h2 = 0x3C6EF372
local h3 = 0xA54FF53A
local h4 = 0x510E527F
local h5 = 0x9B05688C
local h6 = 0x1F83D9AB
local h7 = 0x5BE0CD19

local w = table.create(64)
for chunk = 1, #bytes, 64 do
for i = 0, 15 do
local base = chunk + (i * 4)
w[i + 1] = bor(lshift(bytes[base], 24), lshift(bytes[base + 1], 16), lshift(bytes[base + 2], 8), bytes[base + 3])
end
for i = 17, 64 do
local s0 = bxor(rrotate(w[i - 15], 7), rrotate(w[i - 15], 18), rshift(w[i - 15], 3))
local s1 = bxor(rrotate(w[i - 2], 17), rrotate(w[i - 2], 19), rshift(w[i - 2], 10))
w[i] = mod32(w[i - 16] + s0 + w[i - 7] + s1)
end

local a = h0
local b = h1
local c = h2
local d = h3
local e = h4
local f = h5
local g = h6
local hh = h7

for i = 1, 64 do
local S1 = bxor(rrotate(e, 6), rrotate(e, 11), rrotate(e, 25))
local ch = bxor(band(e, f), band(bnot(e), g))
local temp1 = mod32(hh + S1 + ch + K[i] + w[i])
local S0 = bxor(rrotate(a, 2), rrotate(a, 13), rrotate(a, 22))
local maj = bxor(band(a, b), band(a, c), band(b, c))
local temp2 = mod32(S0 + maj)

hh = g
g = f
f = e
e = mod32(d + temp1)
d = c
c = b
b = a
a = mod32(temp1 + temp2)
end

h0 = mod32(h0 + a)
h1 = mod32(h1 + b)
h2 = mod32(h2 + c)
h3 = mod32(h3 + d)
h4 = mod32(h4 + e)
h5 = mod32(h5 + f)
h6 = mod32(h6 + g)
h7 = mod32(h7 + hh)
end

return toBytes({ h0, h1, h2, h3, h4, h5, h6, h7 })
end

local function toHex(str: string): string
return (str:gsub(".", function(char)
return string.format("%02x", string.byte(char))
end))
end

local function hmacSha256(key: string, message: string): string
local blockSize = 64
if #key > blockSize then
key = sha256Binary(key)
end
if #key < blockSize then
key = key .. string.rep(string.char(0), blockSize - #key)
end

local oKeyPad = table.create(blockSize)
local iKeyPad = table.create(blockSize)
for i = 1, blockSize do
local byte = string.byte(key, i)
oKeyPad[i] = string.char(bxor(byte, 0x5C))
iKeyPad[i] = string.char(bxor(byte, 0x36))
end

local inner = sha256Binary(table.concat(iKeyPad) .. message)
local digest = sha256Binary(table.concat(oKeyPad) .. inner)
return toHex(digest)
end

local RingBuffer = {}
RingBuffer.__index = RingBuffer

function RingBuffer.new(size: number, seed: { any }?)
local self = setmetatable({
size = size,
items = {},
lookup = {},
}, RingBuffer)
if seed then
for _, record in ipairs(seed) do
self:push(record)
end
end
return self
end

function RingBuffer:push(record)
local existing = self.lookup[record.txnId]
if existing then
return false, existing
end
table.insert(self.items, record)
self.lookup[record.txnId] = record
if #self.items > self.size then
local removed = table.remove(self.items, 1)
if removed then
self.lookup[removed.txnId] = nil
end
end
return true, record
end

function RingBuffer:list()
return table.clone(self.items)
end

local ProfileStore = ProfileService.GetProfileStore("EconomyWallet", PROFILE_TEMPLATE)

export type EconomyCommand = {
txnId: string,
userId: string,
delta: number,
reason: string?,
actor: string,
source: string,
}

local ActiveSessions: { [string]: { profile: any, ring: any } } = {}

local function broadcastEvent(userId: string, record)
local occurred = DateTime.fromUnixTimestampMillis(record.processedAt):ToIsoDateTime()
local payload = {
type = "economy.update",
payload = {
txnId = record.txnId,
userId = userId,
delta = record.delta,
balance = record.balanceAfter,
actor = record.actor,
source = record.source,
reason = record.reason,
occurredAt = occurred,
},
}
local ok, err = pcall(function()
MessagingService:PublishAsync(EVENT_TOPIC_PREFIX .. userId, HttpService:JSONEncode(payload))
end)
if not ok then
warn("Failed to publish economy event", err)
end

local remote = ReplicatedStorage:FindFirstChild("EconomyUpdated")
if remote and remote:IsA("RemoteEvent") then
local player = Players:GetPlayerByUserId(tonumber(userId))
if player then
remote:FireClient(player, payload.payload)
end
end
end

local function logToApi(userId: string, record)
local payload = {
txnId = record.txnId,
userId = userId,
delta = record.delta,
actor = record.actor,
source = record.source,
balance = record.balanceAfter,
occurredAt = record.processedAt,
reason = record.reason,
}
local payloadJson = HttpService:JSONEncode(payload)
local body = HttpService:JSONEncode({ payload = payload })
local signature = hmacSha256(HMAC_SECRET, payloadJson)
local headers = {
["Content-Type"] = "application/json",
["X-Signature"] = signature,
["Idempotency-Key"] = string.format("roblox-%s-%s", game.JobId, record.txnId),
}
local ok, err = pcall(function()
HttpService:PostAsync(API_ENDPOINT, body, Enum.HttpContentType.ApplicationJson, false, headers)
end)
if not ok then
warn("Failed to log transaction", err)
end
end

local function attachProfile(userId: string, profile)
profile:Reconcile()
profile.MetaData.MetaTags = profile.MetaData.MetaTags or {}
profile.MetaData.MetaTags.sessionOwner = game.JobId
profile.Data.balance = profile.Data.balance or 0
profile.Data.processed = profile.Data.processed or {}

local ring = RingBuffer.new(RING_SIZE, profile.Data.processed)
ActiveSessions[userId] = { profile = profile, ring = ring }

profile:ListenToRelease(function()
ActiveSessions[userId] = nil
end)

return ActiveSessions[userId]
end

local function releaseProfile(userId: string)
local state = ActiveSessions[userId]
if state then
state.profile:Release()
ActiveSessions[userId] = nil
end
end

local function applyCommand(command: EconomyCommand)
local userId = tostring(command.userId)
local state = ActiveSessions[userId]
local releaseAfter = false

if not state then
local profile = ProfileStore:LoadProfileAsync("wallet_" .. userId, "ForceLoad")
if not profile then
warn("Could not acquire profile for", userId, "likely owned by another server")
return
end
state = attachProfile(userId, profile)
releaseAfter = true
end

local nextBalance = state.profile.Data.balance + command.delta
local now = DateTime.now()
local record = {
txnId = command.txnId,
delta = command.delta,
balanceAfter = nextBalance,
actor = command.actor,
source = command.source,
processedAt = now.UnixTimestampMillis,
reason = command.reason,
}

local inserted, existing = state.ring:push(record)
if not inserted then
-- Duplicate txnId observed; return previously computed state to honour idempotency.
record = existing
else
state.profile.Data.balance = nextBalance
state.profile.Data.processed = state.ring:list()
broadcastEvent(userId, record)
logToApi(userId, record)
end

if releaseAfter then
releaseProfile(userId)
end
end

local function onPlayerAdded(player: Player)
local profile = ProfileStore:LoadProfileAsync("wallet_" .. player.UserId, "ForceLoad")
if not profile then
player:Kick("Wallet locked by another server. Please rejoin.")
return
end
profile:AddUserId(player.UserId)
local state = attachProfile(tostring(player.UserId), profile)
profile:ListenToRelease(function()
ActiveSessions[tostring(player.UserId)] = nil
player:Kick("Wallet session released")
end)

-- Push latest balance to UI when player joins.
local now = DateTime.now()
broadcastEvent(tostring(player.UserId), {
txnId = "session-refresh",
delta = 0,
balanceAfter = state.profile.Data.balance,
actor = "system",
source = "game",
processedAt = now.UnixTimestampMillis,
})
end

Players.PlayerAdded:Connect(onPlayerAdded)
Players.PlayerRemoving:Connect(function(player)
releaseProfile(tostring(player.UserId))
end)

-- Messaging subscription for Discord commands.
local function subscribe()
local ok, connection = pcall(function()
return MessagingService:SubscribeAsync(COMMAND_TOPIC, function(message)
local success, envelope = pcall(function()
return HttpService:JSONDecode(message.Data)
end)
if not success then
warn("Failed to decode command", envelope)
return
end
if envelope.type == "economy.command" and envelope.payload then
applyCommand(envelope.payload)
end
end)
end)
if not ok then
error("Unable to subscribe to discord-commands topic: " .. tostring(connection))
end
return connection
end

subscribe()

print("Economy bridge initialised for job", game.JobId)
