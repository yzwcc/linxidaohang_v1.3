import crypto from "node:crypto";
import { z } from "zod";
import type { AppConfig } from "./config.js";

const tokenPayloadSchema = z.object({
  sub: z.string().min(1),
  iat: z.number(),
  exp: z.number()
});

export type SessionUser = {
  id: string;
};

function base64url(input: Buffer | string): string {
  return Buffer.from(input).toString("base64url");
}

function sign(data: string, secret: string): string {
  return crypto.createHmac("sha256", secret).update(data).digest("base64url");
}

export function issueToken(userId: string, config: AppConfig): string {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    sub: userId,
    iat: now,
    exp: now + config.auth.tokenTtlSec
  };
  const encoded = base64url(JSON.stringify(payload));
  return `${encoded}.${sign(encoded, config.auth.tokenSecret)}`;
}

export function verifyToken(token: string, config: AppConfig): SessionUser | null {
  const [encoded, signature] = token.split(".");
  if (!encoded || !signature) return null;
  const expected = sign(encoded, config.auth.tokenSecret);
  if (Buffer.byteLength(signature) !== Buffer.byteLength(expected)) return null;
  if (!crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) return null;
  const parsed = tokenPayloadSchema.safeParse(JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")));
  if (!parsed.success) return null;
  if (parsed.data.exp < Math.floor(Date.now() / 1000)) return null;
  return { id: parsed.data.sub };
}

export async function loginWithWxCode(code: string, config: AppConfig): Promise<string> {
  if (config.auth.devBypass) {
    return issueToken("dev-user", config);
  }
  if (!config.auth.appId || !config.auth.appSecret) {
    throw new Error("MINIPROGRAM_APPID and MINIPROGRAM_SECRET are required");
  }
  const url = new URL("https://api.weixin.qq.com/sns/jscode2session");
  url.searchParams.set("appid", config.auth.appId);
  url.searchParams.set("secret", config.auth.appSecret);
  url.searchParams.set("js_code", code);
  url.searchParams.set("grant_type", "authorization_code");
  const response = await fetch(url);
  const body = await response.json() as { openid?: string; errmsg?: string; errcode?: number };
  if (!response.ok || !body.openid) {
    throw new Error(body.errmsg || `WeChat login failed: ${response.status}`);
  }
  return issueToken(body.openid, config);
}
