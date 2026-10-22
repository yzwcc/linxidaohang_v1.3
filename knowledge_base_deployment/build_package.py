#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
VERSION = "1.0.7-20261023"
NAME = f"sparkrobot-lingxi-kb-{VERSION}"
DIST = HERE / "dist"
PRESET_MOTION_NAMES = (
    "wave",
    "handshake",
    "raise_hand",
    "flying_kiss",
    "sweetheart",
    "high_five",
    "salute",
    "horizontal_arm",
    "hug",
    "cheer",
    "clap",
    "handx",
    "chestwave",
    "bow",
    "bye",
    "lightwave",
    "scratch_head",
    "scratch_back",
)
JAVA_AGENT_DIR = (
    HERE.parent / "robot_local_nav_java"
    / "robot_local_nav_java-new-java-interaction-end-hook"
)
JAVA_AGENT_BUILDER = JAVA_AGENT_DIR / "build_voice_gate_agent.py"
JAVA_AGENT_JAR = JAVA_AGENT_DIR / "target" / "robot-local-navigation-java-0.1.0-all-kb-voice-latency-20261023.jar"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    DIST.mkdir(exist_ok=True)
    output = DIST / f"{NAME}.zip"
    subprocess.run([sys.executable, str(JAVA_AGENT_BUILDER)], check=True)
    with tempfile.TemporaryDirectory(prefix=".kb-build-", dir=DIST) as temp:
        stage = Path(temp) / NAME
        stage.mkdir()
        if not JAVA_AGENT_JAR.is_file():
            raise RuntimeError(f"缺少已编译的知识库语音接入 JAR：{JAVA_AGENT_JAR}")
        for relative in ("install.sh", "install_voice_agent.sh", "control.sh", "release-info.txt", "README.md"):
            shutil.copy2(HERE / relative, stage / relative)
        for directory in ("service", "systemd", "templates", "docs"):
            shutil.copytree(HERE / directory, stage / directory)
        (stage / "agent").mkdir()
        shutil.copy2(JAVA_AGENT_JAR, stage / "agent" / "robot-local-navigation-java-all-kb.jar")
        for path in stage.rglob("*"):
            if path.is_file() and path.suffix in {".sh", ""} and path.name in {"install.sh", "install_voice_agent.sh", "control.sh", "kb-search", "retriever_service.py"}:
                path.chmod(0o755)
        manifest = []
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            manifest.append({"path": path.relative_to(stage).as_posix(), "sha256": digest(path), "size": path.stat().st_size})
        package_manifest = {
            "format": "sparkrobot-lingxi-kb-v1",
            "version": VERSION,
            "voice_interaction_policy": {
                "wake_mode": "enforce",
                "metadata_required": False,
                "front_half_angle_deg": 60,
                "min_snr_db": 6,
                "max_echo_rms": 0.12,
                "min_vad_probability": 0.70,
                "min_asr_confidence": 0.65,
                "wake_signal_max_age_ms": 8000,
                "session_window_ms": 8000,
                "acoustic_dsp_included": False,
            },
            "voice_response_policy": {
                "point_names_cache_ttl_ms": 10000,
                "retriever_timeout_ms": 1200,
                "retriever_min_score": 0.60,
                "retriever_rerank": False,
                "retriever_qa_precheck": False,
                "text_input_greetings_use_deterministic_reply": True,
                "local_or_current_facts_require_grounding": True,
                "unsupported_grounded_facts": "refuse",
                "legacy_generated_qa_cache": "disabled_for_answer_selection",
                "text_chat_reuses_intent_reply": True,
                "blank_intent_reply_uses_chat_fallback": True,
                "streaming_model_output": False,
                "tts_delay_changed": False,
                "known_point_navigation_fast_path": "exact_active_map_voice_name_before_intent_model",
                "raw_audio_navigation_still_requires_transcription_model": True,
                "local_stop_commands_before_intent_model": True,
                "ambiguous_navigation_alias": "clarify_without_navigation_submission",
                "low_asr_confidence": "robot_tts_repeat_prompt_without_navigation_submission",
                "start_resume_commands_require_asr_confidence_gate": True,
                "factual_retrieval_strategy": "parallel_with_intent_parsing",
                "factual_retrieval_worker_threads": 2,
                "factual_retrieval_queue_capacity": 4,
                "factual_retrieval_queue_full_fallback": "serial_after_intent_parsing",
            },
            "preset_motion_policy": {
                "action_count": len(PRESET_MOTION_NAMES),
                "actions": list(PRESET_MOTION_NAMES),
                "execution_mode": "explicit_imperative_only",
                "busy_navigation_or_tour": "reject",
                "live_danmu": "disabled",
                "acknowledgement": "submission_only",
            },
            "files": manifest,
        }
        (stage / "manifest.json").write_text(
            json.dumps(package_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output.unlink(missing_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(item for item in stage.rglob("*") if item.is_file()):
                relative = PurePosixPath(NAME) / PurePosixPath(path.relative_to(stage).as_posix())
                info = zipfile.ZipInfo(relative.as_posix())
                executable = path.name in {"install.sh", "install_voice_agent.sh", "control.sh", "kb-search", "retriever_service.py"}
                info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    checksum = digest(output)
    output.with_suffix(output.suffix + ".sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(f"部署包：{output}")
    print(f"SHA-256：{checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
