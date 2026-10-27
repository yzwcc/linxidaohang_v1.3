import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

type Session = { token: string; expiresAt: number };

export class SessionManager {
  private readonly sessions = new Map<string, Session>();
  private readonly expectedHash: Buffer;

  constructor(pairingCodeHash: string, private readonly ttlMs = 8 * 60 * 60 * 1000) {
    const normalized = pairingCodeHash.trim().toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(normalized)) throw new Error("A SHA-256 pairing-code hash is required");
    this.expectedHash = Buffer.from(normalized, "hex");
  }

  pair(code: string): string | null {
    for (const [key, session] of this.sessions) {
      if (session.expiresAt <= Date.now()) this.sessions.delete(key);
    }
    if (this.sessions.size >= 128) return null;
    if (typeof code !== "string" || code.length < 8 || code.length > 256) return null;
    const supplied = createHash("sha256").update(code, "utf8").digest();
    if (!timingSafeEqual(this.expectedHash, supplied)) return null;
    const token = randomBytes(32).toString("base64url");
    this.sessions.set(token, { token, expiresAt: Date.now() + this.ttlMs });
    return token;
  }

  isValid(token: string | undefined): boolean {
    if (!token) return false;
    const session = this.sessions.get(token);
    if (!session) return false;
    if (session.expiresAt <= Date.now()) {
      this.sessions.delete(token);
      return false;
    }
    return true;
  }

  revoke(token: string | undefined): void {
    if (token) this.sessions.delete(token);
  }
}

export function pairingCodeHash(code: string): string {
  return createHash("sha256").update(code, "utf8").digest("hex");
}

export function extractBearer(header: string | undefined): string | undefined {
  const match = header?.match(/^Bearer\s+([A-Za-z0-9_-]{32,})$/i);
  return match?.[1];
}

export function websocketBearer(protocolHeader: string | undefined): string | undefined {
  const protocol = protocolHeader?.split(",").map((part) => part.trim()).find((part) => part.startsWith("bearer."));
  return protocol?.slice("bearer.".length);
}
