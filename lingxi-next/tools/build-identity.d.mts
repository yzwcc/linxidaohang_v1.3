export type Inventory = { algorithm: string; sha256: string; files: { path: string; bytes: number; sha256: string }[] };
export type BuildIdentity = { schema: number; product: { name: string; version: string; androidVersionCode: number }; source: Inventory };
export const BUILD_IDENTITY_FILE: string;
export function productVersion(metadata: unknown): BuildIdentity["product"];
export function fileInventory(root: string, inputs: string[], exclude?: (relative: string) => boolean): Promise<Inventory>;
export function webBuildIdentity(root: string): Promise<BuildIdentity>;
export function assertCurrentWebBuild(root: string): Promise<BuildIdentity>;
export function androidBuildIdentity(root: string): Promise<Inventory>;
export function assertSyncedWebAssets(root: string): Promise<Inventory>;
