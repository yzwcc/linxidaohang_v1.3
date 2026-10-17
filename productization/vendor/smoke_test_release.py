#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import getpass
import os
import secrets
import shlex
import tempfile
from pathlib import Path, PurePosixPath

from deploy_robot import connect, load_paramiko, resolve_openssl, run_remote, safe_extract, upload, verify_release


REMOTE_PREFIX = "/tmp/sparkrobot-release-smoke-"
CLASS_LOADER_SOURCE = """public final class SparkRobotClassLoadCheck {
  public static void main(String[] args) throws Exception {
    Class.forName(args[0], false, SparkRobotClassLoadCheck.class.getClassLoader());
    System.out.println("class-loaded=" + args[0]);
  }
}
"""


def release_root(top: str, version: str) -> str:
    safe_version = PurePosixPath(version)
    if safe_version.is_absolute() or len(safe_version.parts) != 1 or ".." in safe_version.parts:
        raise RuntimeError("发布版本路径不安全")
    return f"{{remote}}/unpacked/{top}/rootfs/opt/sparkrobot/lingxi-nav/releases/{version}"


def main() -> int:
    parser = argparse.ArgumentParser(description="厂商专用：在机器人临时目录验证完整发布归档，不安装或启动导航")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="agi")
    parser.add_argument("--ssh-key")
    parser.add_argument("--password-env", default="SPARKROBOT_SSH_PASSWORD")
    parser.add_argument("--openssl")
    args = parser.parse_args()

    archive = Path(args.archive).resolve()
    if not archive.is_file():
        raise RuntimeError("发布归档不存在")

    remote_dir = REMOTE_PREFIX + secrets.token_hex(8)
    client = None
    temp_parent = Path(archive.anchor) if os.name == "nt" and archive.anchor else None
    with tempfile.TemporaryDirectory(prefix=".sparkrobot-smoke-", dir=temp_parent) as temp_name:
        release = safe_extract(archive, Path(temp_name) / "release")
        manifest = verify_release(release, resolve_openssl(args.openssl))
        version = str(manifest["version"])
        password = os.environ.get(args.password_env) or (None if args.ssh_key else getpass.getpass("机器人 SSH 密码："))
        paramiko = load_paramiko()
        client = connect(paramiko, args.host, args.port, args.user, password, args.ssh_key)
        try:
            run_remote(client, f"mkdir -m 700 {shlex.quote(remote_dir)}")
            remote_archive = f"{remote_dir}/release.tar.gz"
            upload(client, archive, remote_archive)
            top = release.name
            root = release_root(top, version).format(remote=remote_dir)
            source = base64.b64encode(CLASS_LOADER_SOURCE.encode("ascii")).decode("ascii")
            token = secrets.token_urlsafe(48)
            command = f"""
set -euo pipefail
mkdir -p {shlex.quote(remote_dir + '/unpacked')} {shlex.quote(remote_dir + '/classcheck')}
tar -xzf {shlex.quote(remote_archive)} -C {shlex.quote(remote_dir + '/unpacked')}
root={shlex.quote(root)}
test -x "$root/runtime/node"
test -x "$root/runtime/java/bin/java"
test -f "$root/java/robot-local-navigation-java-all.jar"
test -f "$root/java/linksoul-agentsdk-1.4.0.jar"
"$root/runtime/node" --version
"$root/runtime/java/bin/java" -version
printf '%s' {shlex.quote(source)} | base64 -d > {shlex.quote(remote_dir + '/SparkRobotClassLoadCheck.java')}
javac -d {shlex.quote(remote_dir + '/classcheck')} {shlex.quote(remote_dir + '/SparkRobotClassLoadCheck.java')}
"$root/runtime/java/bin/java" -Xverify:all \
  -cp {shlex.quote(remote_dir + '/classcheck')}:"$root/java/robot-local-navigation-java-all.jar":"$root/java/linksoul-agentsdk-1.4.0.jar" \
  SparkRobotClassLoadCheck com.agibot.aiem.nav.LingxiNavAgentApp
port=$((31000 + RANDOM % 1000))
cd "$root/console"
env DEPLOYMENT_MODE=robot HOST=127.0.0.1 PORT="$port" \
  PUBLIC_BASE_URL="http://127.0.0.1:$port" DEV_AUTH_BYPASS=true \
  TOKEN_SECRET={shlex.quote(token)} JAVA_AGENT_AUTO_START=false \
  "$root/runtime/node" app.mjs > {shlex.quote(remote_dir + '/console.log')} 2>&1 &
pid=$!
cleanup_console() {{ kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; }}
trap cleanup_console EXIT
for _ in $(seq 1 40); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$port/health" > {shlex.quote(remote_dir + '/health.json')} 2>/dev/null; then
    cat {shlex.quote(remote_dir + '/health.json')}
    cleanup_console
    trap - EXIT
    exit 0
  fi
  kill -0 "$pid" 2>/dev/null || {{ cat {shlex.quote(remote_dir + '/console.log')} >&2; exit 1; }}
  sleep 0.25
done
cat {shlex.quote(remote_dir + '/console.log')} >&2
exit 1
"""
            output = run_remote(client, command, timeout=180)
            print(output)
        finally:
            if client is not None:
                try:
                    cleanup = (
                        f"case {shlex.quote(remote_dir)} in "
                        f"{REMOTE_PREFIX}*) rm -rf -- {shlex.quote(remote_dir)} ;; *) exit 2 ;; esac"
                    )
                    run_remote(client, cleanup, timeout=30)
                finally:
                    client.close()
    print(f"ARM64 临时冒烟验证通过：version={version}，host={args.host}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ARM64 临时冒烟验证失败：{exc}", file=os.sys.stderr)
        raise SystemExit(1)
