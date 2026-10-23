#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
VERSION = "1.0.10-20261023"
NAME = f"sparkrobot-lingxi-kb-{VERSION}"
ARCHIVE = HERE / "dist" / f"{NAME}.zip"
VOICE_GATE_CLASSES = {
    "com/agibot/aiem/nav/AppConfig.class",
    "com/agibot/aiem/nav/LingxiNavAgentApp.class",
    "com/agibot/aiem/nav/LingxiNavAgentApp$StreamingTtsSession.class",
    "com/agibot/aiem/nav/LingxiNavAgentApp$TtsController.class",
    "com/agibot/aiem/nav/LingxiNavAgentApp$TtsDecision.class",
    "com/agibot/aiem/nav/LocalNavigationCommand.class",
    "com/agibot/aiem/nav/VoiceGate.class",
    "com/agibot/aiem/nav/WakeSessionGate.class",
    "com/agibot/aiem/nav/SpeechInputPolicy.class",
    "com/agibot/aiem/nav/Soc2WakeEvidenceProvider.class",
    "com/agibot/aiem/nav/VoiceMetadataDiagnostics.class",
    "com/agibot/aiem/nav/NavigationIntent.class",
    "com/agibot/aiem/nav/PresetMotionCatalog.class",
    "com/agibot/aiem/nav/PresetMotionCatalog$Motion.class",
    "com/agibot/aiem/nav/PresetMotionCatalog$Selection.class",
    "com/agibot/aiem/nav/NavBridgeClient.class",
    "com/agibot/aiem/nav/DoubaoArkClient.class",
    "com/agibot/aiem/nav/DoubaoArkClient$SseChunk.class",
    "com/agibot/aiem/nav/SpeechSegmenter.class",
    "com/agibot/aiem/nav/StreamingJsonReplyExtractor.class",
    "com/agibot/aiem/nav/StreamingJsonReplyExtractor$ParsedString.class",
    "com/agibot/aiem/nav/RetrieverClient.class",
    "com/agibot/aiem/nav/KnowledgeAnswerPolicy.class",
}
PRESET_MOTION_NAMES = {
    "wave", "handshake", "raise_hand", "flying_kiss", "sweetheart", "high_five",
    "salute", "horizontal_arm", "hug", "cheer", "clap", "handx", "chestwave",
    "bow", "bye", "lightwave", "scratch_head", "scratch_back",
}
RESPONSE_POLICY = {
    "point_names_cache_ttl_ms": 10000,
    "retriever_timeout_ms": 1200,
    "retriever_min_score": 0.60,
    "retriever_rerank": False,
    "retriever_qa_precheck": False,
    "text_input_greetings_use_deterministic_reply": True,
    "local_or_current_facts_require_grounding": True,
    "unsupported_grounded_facts": "refuse",
    "ordinary_general_questions_use_model_knowledge": True,
    "generic_domain_terms_alone_require_grounding": False,
    "legacy_generated_qa_cache": "disabled_for_answer_selection",
    "text_chat_reuses_intent_reply": True,
    "blank_intent_reply_uses_chat_fallback": True,
    "streaming_replies_default_enabled": False,
    "streaming_replies_scope": "plain_text_chat_only",
    "streaming_replies_require_robot_tts_mode": True,
    "streaming_tts_first_segment_interrupts": True,
    "streaming_tts_continuations_ordered": True,
    "streaming_tts_new_normal_reply_preempts_current_stream": True,
    "streaming_tts_queue_requires_robot_validation": True,
    "streaming_tts_dispatch_revalidates_owner_after_service_wait": True,
    "streaming_tts_stopped_stream_ids_rejected": True,
    "streaming_tts_call_async_cancellation_supported_by_bridge": False,
    "streaming_tts_end_action_stops_robot_playback": False,
    "streaming_tts_preempted_full_reply_fallback": "suppressed",
    "java_tts_decisions_use_owner_tokens": True,
    "streaming_model_failure_before_first_speech": "retry_non_streaming",
    "streaming_model_failure_after_first_speech": "urgent_interruption_prompt",
    "streaming_tts_continuation_failure": "complete_final_reply_if_owner_current",
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
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    sidecar = ARCHIVE.with_suffix(ARCHIVE.suffix + ".sha256")
    if not ARCHIVE.is_file() or not sidecar.is_file():
        raise RuntimeError("缺少部署包或 SHA-256 文件")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    if digest(ARCHIVE) != expected:
        raise RuntimeError("部署包 SHA-256 不一致")
    with zipfile.ZipFile(ARCHIVE) as archive:
        names = set(archive.namelist())
        required = {
            f"{NAME}/install.sh", f"{NAME}/install_voice_agent.sh", f"{NAME}/control.sh", f"{NAME}/release-info.txt",
            f"{NAME}/manifest.json", f"{NAME}/service/retriever_service.py",
            f"{NAME}/service/kb-search", f"{NAME}/systemd/sparkrobot-lingxi-kb.service",
            f"{NAME}/templates/retriever.env.example", f"{NAME}/agent/robot-local-navigation-java-all-kb.jar",
            f"{NAME}/docs/灵心知识库独立部署教程.md",
        }
        if not required <= names:
            raise RuntimeError("部署包缺少：" + ", ".join(sorted(required - names)))
        for member in names:
            path = PurePosixPath(member)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"ZIP 包含不安全路径：{member}")
        manifest = json.loads(archive.read(f"{NAME}/manifest.json"))
        if manifest["version"] != VERSION:
            raise RuntimeError("版本清单不正确")
        release_info = archive.read(f"{NAME}/release-info.txt").decode("utf-8")
        if f"VERSION={VERSION}" not in release_info:
            raise RuntimeError("release-info.txt 版本不正确")
        voice_policy = manifest.get("voice_interaction_policy", {})
        if (
            voice_policy.get("wake_mode") != "enforce"
            or voice_policy.get("metadata_required") is not False
            or voice_policy.get("wake_signal_max_age_ms") != 8000
            or voice_policy.get("session_window_ms") != 8000
            or voice_policy.get("acoustic_dsp_included") is not False
        ):
            raise RuntimeError("语音拒识策略清单与默认实现不一致")
        if manifest.get("voice_response_policy") != RESPONSE_POLICY:
            raise RuntimeError("语音响应策略清单与默认实现不一致")
        motion_policy = manifest.get("preset_motion_policy", {})
        if (
            motion_policy.get("action_count") != 18
            or set(motion_policy.get("actions", [])) != PRESET_MOTION_NAMES
            or motion_policy.get("execution_mode") != "explicit_imperative_only"
            or motion_policy.get("busy_navigation_or_tour") != "reject"
            or motion_policy.get("live_danmu") != "disabled"
            or motion_policy.get("acknowledgement") != "submission_only"
        ):
            raise RuntimeError("预置动作策略清单与实现不一致")
        for item in manifest["files"]:
            member = f"{NAME}/{item['path']}"
            with archive.open(member) as handle:
                if hashlib.sha256(handle.read()).hexdigest() != item["sha256"]:
                    raise RuntimeError(f"文件校验失败：{item['path']}")
        agent_bytes = archive.read(
            f"{NAME}/agent/robot-local-navigation-java-all-kb.jar"
        )
        with zipfile.ZipFile(io.BytesIO(agent_bytes)) as agent:
            missing_gate = VOICE_GATE_CLASSES - set(agent.namelist())
            if missing_gate:
                raise RuntimeError(
                    "Java Agent 缺少交互拒识类：" + ", ".join(sorted(missing_gate))
                )
            prompt_class = agent.read("com/agibot/aiem/nav/DoubaoArkClient.class")
            if (
                "使用模型已有知识正常回答".encode("utf-8") not in prompt_class
                or "不要因为当前上下文或知识库没有提供常识答案而拒答".encode("utf-8")
                not in prompt_class
            ):
                raise RuntimeError("Java Agent 未包含普通问题由大模型回答的提示策略")
            if "com/agibot/aiem/nav/KnowledgeAnswerPolicyTest.class" in agent.namelist():
                raise RuntimeError("知识库 Java Agent 不应包含回归测试类")
        for marker in (b"St87591540", b"ROBOT_SSH_PASSWORD=1", b"1.0.6"):
            for member in names:
                if member.endswith((".zip", ".gz", ".7z")):
                    continue
                if marker in archive.read(member):
                    raise RuntimeError(f"套件包含不应出现的内容：{member}")
    print("KB_PACKAGE_AUDIT_OK")
    print(f"archive={ARCHIVE}")
    print(f"sha256={expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
