import { createHash, randomBytes } from "node:crypto";
import { cp, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { downloadVerified as download } from "./download.mjs";
import { startMavenProxy } from "./maven-proxy.mjs";

if (process.platform !== "win32") throw new Error("This bootstrap supports the Windows build workstation; use Gradle with your configured Android SDK on Linux.");
const root = path.resolve(".local");
const sdk = path.join(root, "android-sdk");
await mkdir(root, { recursive: true });
const run = (command, args, options = {}) => new Promise((resolve, reject) => {
  const child = spawn(command, args, { stdio: options.input ? ["pipe", "inherit", "inherit"] : "inherit", ...options });
  if (options.input) child.stdin.end(options.input);
  child.on("error", reject);
  child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`${path.basename(command)} exited ${code}`)));
});
const jdkArchive = path.join(root, "zulu-jdk21.zip");
const jdkParent = path.join(root, "jdk21");
if (!existsSync(jdkParent)) {
  await download("https://cdn.azul.com/zulu/bin/zulu21.54.107-ca-jdk21.0.13-win_x64.zip",
    jdkArchive, "sha256", "69e9fd7afafe0d70235884f8d9ab15cf8a4b43215b27900d710841e1fb3c7126");
  await mkdir(jdkParent);
  await run("tar", ["-xf", jdkArchive, "-C", jdkParent]);
}
const javaFolder = (await readdir(jdkParent)).find((name) => existsSync(path.join(jdkParent, name, "bin", "java.exe")));
if (!javaFolder) throw new Error("JDK extraction incomplete");
const javaHome = path.join(jdkParent, javaFolder);
const environment = { ...process.env, JAVA_HOME: javaHome, ANDROID_HOME: sdk, ANDROID_SDK_ROOT: sdk,
  GRADLE_USER_HOME: path.join(root, "gradle"), PATH: `${path.join(javaHome, "bin")};${process.env.PATH}` };
const sdkmanager = path.join(sdk, "cmdline-tools", "bin", "sdkmanager.bat");
if (!existsSync(sdkmanager)) {
  await download("https://dl.google.com/android/repository/commandlinetools-win-16111833_latest.zip",
    path.join(root, "android-tools.zip"), "sha1", "57d04f2d75eb8e8fffc5000a987e5de4b5a63e9d");
  await mkdir(sdk, { recursive: true });
  await run("tar", ["-xf", path.join(root, "android-tools.zip"), "-C", sdk]);
}
const androidCli = path.join(sdk, "cmdline-tools", "bin", "android.exe");
if (!existsSync(path.join(sdk, "platforms", "android-35", "android.jar")) ||
    !existsSync(path.join(sdk, "build-tools", "35.0.0", "aapt2.exe"))) {
  await run(androidCli, ["--no-metrics", `--sdk=${sdk}`, "sdk", "install",
    "platform-tools", "platforms/android-35", "build-tools/35.0.0"], { env: environment });
}
if (!existsSync(path.join(sdk, "platforms", "android-35", "android.jar")) ||
    !existsSync(path.join(sdk, "build-tools", "35.0.0", "aapt2.exe"))) {
  throw new Error("Android platform/build tools installation is incomplete");
}
const propertyPath = sdk.replaceAll("\\", "/").replace(/[^\x20-\x7e]/g,
  (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
await writeFile("android/local.properties", `sdk.dir=${propertyPath}\n`, "ascii");
if (!environment.LINGXI_ANDROID_KEYSTORE) {
  const keyDir = path.join(root, "keys");
  await mkdir(keyDir, { recursive: true });
  const key = path.join(keyDir, "commissioning.jks");
  const passwordFile = path.join(keyDir, "commissioning-password.txt");
  if (!existsSync(key)) {
    const password = randomBytes(32).toString("base64url");
    await writeFile(passwordFile, password, { mode: 0o600 });
    await run(path.join(javaHome, "bin", "keytool.exe"), ["-genkeypair", "-keystore", key,
      "-storepass:env", "LINGXI_SIGN_PASSWORD", "-keypass:env", "LINGXI_SIGN_PASSWORD",
      "-alias", "commissioning", "-keyalg", "RSA", "-keysize", "3072", "-validity", "3650",
      "-dname", "CN=Lingxi Next Commissioning,OU=Local Development"], { env: { ...environment, LINGXI_SIGN_PASSWORD: password } });
  }
  environment.LINGXI_ANDROID_KEYSTORE = key;
  environment.LINGXI_ANDROID_STORE_PASSWORD = await readFile(passwordFile, "utf8");
  environment.LINGXI_ANDROID_KEY_PASSWORD = environment.LINGXI_ANDROID_STORE_PASSWORD;
  environment.LINGXI_ANDROID_KEY_ALIAS = "commissioning";
  console.log("Using a local commissioning signing key. Production signing requires the product owner's key.");
}
const gradleHash = "f397b287023acdba1e9f6fc5ea72d22dd63669d59ed4a289a29b1a76eee151c6";
const gradleArchive = path.join(root, "gradle-8.11.1-bin.zip");
await download([
  "https://mirrors.huaweicloud.com/gradle/gradle-8.11.1-bin.zip",
  "https://downloads.gradle.org/distributions/gradle-8.11.1-bin.zip",
], gradleArchive, "sha256", gradleHash);
const wrapper = path.join(root, "offline-wrapper");
await mkdir(wrapper, { recursive: true });
await cp("android/gradle/wrapper/gradle-wrapper.jar", path.join(wrapper, "gradle-wrapper.jar"));
await writeFile(path.join(wrapper, "gradle-wrapper.properties"),
  `distributionUrl=${pathToFileURL(gradleArchive).href.replaceAll(":", "\\:")}\ndistributionSha256Sum=${gradleHash}\n`);
const proxy = await startMavenProxy(path.join(root, "maven-cache"));
try {
  await run(path.join(javaHome, "bin", "java.exe"), ["-classpath", path.join(wrapper, "gradle-wrapper.jar"),
    "org.gradle.wrapper.GradleWrapperMain", "--no-daemon", "assembleRelease"], {
    cwd: path.resolve("android"), env: { ...environment, LINGXI_BUILD_MAVEN_PROXY: proxy.url },
  });
} finally { await proxy.close(); }
await mkdir("artifacts", { recursive: true });
const bytes = await readFile("android/app/build/outputs/apk/release/app-release.apk");
await writeFile("artifacts/lingxi-next-0.1.0-commissioning.apk", bytes);
await writeFile("artifacts/lingxi-next-0.1.0-commissioning.apk.sha256", `${createHash("sha256").update(bytes).digest("hex")}  lingxi-next-0.1.0-commissioning.apk\n`);
console.log("APK ready under artifacts/; signing keys are excluded from delivery.");
