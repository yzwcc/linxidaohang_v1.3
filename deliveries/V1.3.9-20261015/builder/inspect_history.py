from pathlib import Path
import hashlib
import json
import tarfile
import zipfile

MAIN = Path(__file__).resolve().parents[3]
V139 = MAIN.with_name(MAIN.name + "-v1.3.9")
NAV = V139 / "robot_local_navigation_v3/robot_local_navigation_v3-new-auto-return-interaction-end/robot_local_navigation_v3"
OUT = Path(__file__).resolve().parent.parent

def sha(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()

def read_archive(path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as f:
            return {n: f.read(n) for n in f.namelist() if not n.endswith("/")}
    with tarfile.open(path, "r:gz") as f:
        return {n.name: f.extractfile(n).read() for n in f if n.isfile()}

def main():
    records = []
    for root in (V139, MAIN):
        for d in sorted(root.glob("*deployment")):
            for p in sorted((d / "dist").glob("*")):
                if not (p.name.endswith(".tar.gz") or p.suffix == ".zip"):
                    continue
                if "v1.3.9" not in p.name and d.name not in {
                    "internal_deployment", "tour_loop_persistence_fix_deployment"
                }:
                    continue
                if p.name.endswith(".zip") and p.with_suffix(".tar.gz").exists():
                    continue
                if d.name == "internal_deployment" and "1.0.20-" not in p.name:
                    continue
                data = read_archive(p)
                payload = {}
                for n, b in data.items():
                    if n.endswith((".py", ".js", ".html", ".json")) and "/payload/" in n:
                        if "/node_modules/" in n or "/runtime/" in n:
                            continue
                        current = None
                        if n.endswith(".py") and (NAV / Path(n).name).is_file():
                            current = NAV / Path(n).name
                        if "/console/dist/" in n:
                            current = V139 / "server/dist" / n.split("/console/dist/", 1)[1]
                        if current and current.exists():
                            payload[n.split("/payload/", 1)[1]] = {
                                "sha256_lf": sha(b),
                                "current_sha256_lf": sha(current.read_bytes()),
                                "matches_current": sha(b) == sha(current.read_bytes()),
                            }
                records.append({
                    "source": str(p.relative_to(root)),
                    "worktree": root.name,
                    "archive_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "bytes": p.stat().st_size,
                    "payload_comparison": payload,
                    "metadata": {n.split("/", 1)[-1]: b.decode("utf-8-sig") for n,b in data.items()
                                 if n.count("/") == 1 and Path(n).name in
                                 {"release-info.json", "release-info.txt", "manifest.json"}},
                })
    (OUT / "build-history-inventory.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for r in records:
        mismatches = [n for n,v in r["payload_comparison"].items() if not v["matches_current"]]
        print(r["worktree"], r["source"], "different:", ", ".join(mismatches))

if __name__ == "__main__":
    main()
