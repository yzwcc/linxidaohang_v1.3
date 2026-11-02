import { createHash } from "node:crypto";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import path from "node:path";
import { z } from "zod";

const configurationSchema = z.object({
  schemaVersion: z.literal(1),
  stateDirectory: z.string().min(1),
  agentUrl: z.string().refine((value) => /^http:\/\/127\.0\.0\.1:([1-9][0-9]{0,4})$/.test(value) &&
    Number(value.split(":").at(-1)) <= 65535),
  agentTokenFile: z.string().min(1),
}).strict();

function privatePath(file: string, directory = false) {
  const info = lstatSync(file);
  if (!path.isAbsolute(file) || path.normalize(file) !== file || realpathSync(file) !== file ||
      !(directory ? info.isDirectory() : info.isFile()) ||
      (process.platform === "linux" && (info.uid !== process.getuid?.() || (info.mode & 0o077) !== 0))) {
    throw new Error("direct_owned_private_product_path_required");
  }
  return info;
}

export function readProductConfiguration(file: string, expected: string, excluded: string[]) {
  if (!/^[a-f0-9]{64}$/.test(expected) || privatePath(file).size > 16384) {
    throw new Error("bounded_verified_product_configuration_required");
  }
  const bytes = readFileSync(file);
  if (createHash("sha256").update(bytes).digest("hex") !== expected) {
    throw new Error("unchanged_product_configuration_required");
  }
  const configuration = configurationSchema.parse(JSON.parse(bytes.toString("utf8")));
  privatePath(configuration.stateDirectory, true);
  const tokenInfo = privatePath(configuration.agentTokenFile);
  if (tokenInfo.size < 1 || tokenInfo.size > 4096) throw new Error("bounded_product_agent_token_required");
  const values = [file, configuration.stateDirectory, configuration.agentTokenFile, ...excluded];
  for (let first = 0; first < values.length; first += 1) {
    for (let second = first + 1; second < values.length; second += 1) {
      if (first >= 3) continue;
      const left = path.resolve(values[first]), right = path.resolve(values[second]);
      if (left === right || left.startsWith(right + path.sep) || right.startsWith(left + path.sep)) {
        throw new Error("separate_product_configuration_state_and_runtime_required");
      }
    }
  }
  const token = readFileSync(configuration.agentTokenFile, "utf8").trim();
  if (!/^[\x21-\x7e]{32,4096}$/.test(token)) throw new Error("valid_product_agent_token_required");
  return { stateDirectory: configuration.stateDirectory, agentUrl: configuration.agentUrl, internalAgentToken: token };
}
