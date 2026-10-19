"""Publish only reports that match the exact independently tested archive."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_complete import NAME, DOCS, digest


def main():
    dist = HERE / "dist"
    archive = dist / (NAME + ".tar.gz")
    expected = digest(archive.read_bytes())
    reports = {}
    for platform, filename in (("Windows", "windows.json"), ("Linux", "linux.json")):
        raw = (HERE / "qa" / filename).read_bytes()
        report = json.loads(raw)
        assert report["status"] == "passed" and report["tested_archive_sha256"] == expected
        assert report["runtime_changes"] == ["java/robot-local-navigation-java-all.jar"]
        assert len(report["merged_increments"]) == 6
        reports[platform] = report
        (dist / ("验证结果-" + platform + ".json")).write_bytes(raw)
    assert len(reports["Windows"]["java_regression"]) == 14
    assert reports["Windows"]["server_regression"]["numPassedTests"] == 74
    assert "133 passed" in reports["Windows"]["python_regression"]
    assert len(reports["Linux"]["lifecycle"]) == 7
    for name in DOCS:
        assert (dist / name).read_bytes() == (HERE / name).read_bytes()
    (dist / "交付验证结果.md").write_bytes((HERE / "交付验证结果.md").read_bytes())
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size,
                      "sha256": expected, "windows": "passed", "linux": "passed"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
