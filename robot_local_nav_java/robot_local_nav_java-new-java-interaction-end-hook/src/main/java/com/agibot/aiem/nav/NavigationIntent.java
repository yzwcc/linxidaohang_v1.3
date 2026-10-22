/*
 * Decompiled with CFR 0.152.
 */
package com.agibot.aiem.nav;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;

final class NavigationIntent {
    final String intent;
    final String targetName;
    final String motionArea;
    final String question;
    final String reply;

    private NavigationIntent(String string, String string2, String string3, String string4) {
        this(string, string2, "", string3, string4);
    }

    private NavigationIntent(String string, String string2, String string3, String string4, String string5) {
        this.intent = NavigationIntent.normalize(string);
        this.targetName = string2 == null ? "" : string2.trim();
        this.motionArea = string3 == null ? "" : string3.trim();
        this.question = string4 == null ? "" : string4.trim();
        this.reply = string5 == null ? "" : string5.trim();
    }

    static NavigationIntent fromJsonText(String string) {
        String string2;
        JSONObject jSONObject = JSON.parseObject(NavigationIntent.extractJsonObject(string));
        String string3 = jSONObject.getString("intent");
        if (string3 == null || string3.isBlank()) {
            throw new IllegalArgumentException("LLM JSON missing required field: intent");
        }
        String string4 = NavigationIntent.normalize(string3);
        if (!("navigate".equals(string4) || "stop_navigation".equals(string4) || "start_task".equals(string4) || "pause_task".equals(string4) || "resume_task".equals(string4) || "end_task".equals(string4) || "sing".equals(string4) || "dance".equals(string4) || "preset_motion".equals(string4) || "chat".equals(string4))) {
            throw new IllegalArgumentException("Unsupported LLM intent: " + string3);
        }
        if ("navigate".equals(string4) && ((string2 = jSONObject.getString("target_name")) == null || string2.isBlank())) {
            throw new IllegalArgumentException("LLM JSON missing target_name for navigate intent");
        }
        String question = jSONObject.getString("question");
        if ("preset_motion".equals(string4)) {
            PresetMotionCatalog.Selection selection = PresetMotionCatalog.resolveVoiceCommand(question);
            if (selection == null) {
                return new NavigationIntent("chat", "", "", question, jSONObject.getString("reply"));
            }
            return presetMotion(selection.motion().name(), selection.area(), question);
        }
        return new NavigationIntent(string4, jSONObject.getString("target_name"), "", question, jSONObject.getString("reply"));
    }

    static NavigationIntent chat(String string) {
        return new NavigationIntent("chat", "", "", string);
    }

    static NavigationIntent sing(String string) {
        return new NavigationIntent("sing", "", "", string);
    }

    static NavigationIntent dance(String string) {
        return new NavigationIntent("dance", "", "", string);
    }

    static NavigationIntent dance(String string, String string2) {
        return new NavigationIntent("dance", string, "", string2);
    }

    static NavigationIntent presetMotion(String motion, String area, String command) {
        PresetMotionCatalog.Selection selection = PresetMotionCatalog.resolve(motion, area);
        if (selection == null) throw new IllegalArgumentException("Unsupported preset motion: " + motion);
        return new NavigationIntent("preset_motion", selection.motion().name(), selection.area(), command, "");
    }

    static NavigationIntent chatQuestion(String question, String reply) {
        return new NavigationIntent("chat", "", "", question, reply);
    }

    static NavigationIntent startTask() {
        return new NavigationIntent("start_task", "", "", "");
    }

    static NavigationIntent pauseTask() {
        return new NavigationIntent("pause_task", "", "", "");
    }

    static NavigationIntent resumeTask() {
        return new NavigationIntent("resume_task", "", "", "");
    }

    static NavigationIntent endTask() {
        return new NavigationIntent("end_task", "", "", "");
    }

    boolean isNavigate() {
        return "navigate".equals(this.intent);
    }

    boolean isStop() {
        return "stop_navigation".equals(this.intent);
    }

    boolean isStartTask() {
        return "start_task".equals(this.intent);
    }

    boolean isEndTask() {
        return "end_task".equals(this.intent);
    }

    boolean isPauseTask() {
        return "pause_task".equals(this.intent);
    }

    boolean isResumeTask() {
        return "resume_task".equals(this.intent);
    }

    boolean isSing() {
        return "sing".equals(this.intent);
    }

    boolean isDance() {
        return "dance".equals(this.intent);
    }

    boolean isPresetMotion() {
        return "preset_motion".equals(this.intent);
    }

    String replyOrDefault(String string) {
        return this.reply.isBlank() ? string : this.reply;
    }

    String questionOrDefault(String string) {
        return this.question.isBlank() ? string : this.question;
    }

    private static String normalize(String string) {
        String string2;
        String string3 = string2 = string == null ? "" : string.trim().toLowerCase();
        if ("stop".equals(string2) || "cancel".equals(string2)) {
            return "stop_navigation";
        }
        if ("navigation".equals(string2) || "nav".equals(string2)) {
            return "navigate";
        }
        if ("starttask".equals(string2) || "task_start".equals(string2) || "tour_start".equals(string2)) {
            return "start_task";
        }
        if ("pausetask".equals(string2) || "task_pause".equals(string2) || "tour_pause".equals(string2)) {
            return "pause_task";
        }
        if ("resumetask".equals(string2) || "task_resume".equals(string2) || "tour_resume".equals(string2) || "continue_task".equals(string2)) {
            return "resume_task";
        }
        if ("endtask".equals(string2) || "stop_task".equals(string2) || "task_stop".equals(string2) || "tour_stop".equals(string2) || "finish_task".equals(string2)) {
            return "end_task";
        }
        if ("song".equals(string2) || "music".equals(string2) || "sing_song".equals(string2)) {
            return "sing";
        }
        if ("dancing".equals(string2) || "perform_dance".equals(string2) || "show_dance".equals(string2)) {
            return "dance";
        }
        if ("motion".equals(string2) || "gesture".equals(string2) || "action".equals(string2)
                || "preset_action".equals(string2)) {
            return "preset_motion";
        }
        return string2.isBlank() ? "chat" : string2;
    }

    private static String extractJsonObject(String string) {
        String string2;
        String string3 = string2 = string == null ? "" : string.trim();
        if (string2.startsWith("```")) {
            string2 = string2.replaceFirst("^```[a-zA-Z]*\\s*", "");
            string2 = string2.replaceFirst("\\s*```$", "");
        }
        int n = string2.indexOf(123);
        int n2 = string2.lastIndexOf(125);
        if (n < 0 || n2 <= n) {
            throw new IllegalArgumentException("LLM result is not a JSON object: " + string);
        }
        return string2.substring(n, n2 + 1);
    }
}
