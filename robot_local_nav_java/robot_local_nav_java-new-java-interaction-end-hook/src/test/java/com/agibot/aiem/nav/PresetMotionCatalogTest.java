package com.agibot.aiem.nav;

import java.util.LinkedHashMap;
import java.util.Map;

final class PresetMotionCatalogTest {
    public static void main(String[] args) {
        verifiesR15CatalogAndVoiceCommands();
        verifiesHandAndAreaSelection();
        rejectsAmbiguousOrQuotedCommands();
        verifiesModelCannotPromoteRejectedSpeech();
        System.out.println("PresetMotionCatalogTest passed");
    }

    private static void verifiesR15CatalogAndVoiceCommands() {
        Map<String, String> commands = new LinkedHashMap<>();
        commands.put("wave", "请你挥挥手");
        commands.put("handshake", "请和我握个手");
        commands.put("raise_hand", "麻烦你举手");
        commands.put("flying_kiss", "送一个飞吻");
        commands.put("sweetheart", "给大家比个爱心");
        commands.put("high_five", "来个击掌");
        commands.put("salute", "请敬礼");
        commands.put("horizontal_arm", "双手平举");
        commands.put("hug", "做个拥抱");
        commands.put("cheer", "做个加油动作");
        commands.put("clap", "请鼓掌");
        commands.put("handx", "双手打叉");
        commands.put("chestwave", "请在胸前挥手");
        commands.put("bow", "麻烦鞠躬");
        commands.put("bye", "挥手告别");
        commands.put("lightwave", "发射动感光波");
        commands.put("scratch_head", "请你挠挠头");
        commands.put("scratch_back", "请挠屁股");

        check(commands.size() == PresetMotionCatalog.all().size(), "catalog action count");
        for (Map.Entry<String, String> entry : commands.entrySet()) {
            PresetMotionCatalog.Selection selection =
                    PresetMotionCatalog.resolveVoiceCommand(entry.getValue());
            check(selection != null, "command accepted: " + entry.getValue());
            check(entry.getKey().equals(selection.motion().name()), "canonical action");
            check(selection.motion().defaultArea().equals(selection.area()), "default area");
        }
    }

    private static void verifiesHandAndAreaSelection() {
        assertSelection("请用左手握手", "handshake", "left_hand");
        assertSelection("请用右手挥手", "wave", "right_hand");
        assertSelection("请你挥手！", "wave", "right_hand");
        assertSelection("请双手比心", "sweetheart", "both_hands");
        check(PresetMotionCatalog.resolve("hug", "right_hand") == null, "incompatible area");
        check(PresetMotionCatalog.resolve("handshake", "whole_body") == null, "invalid area");
    }

    private static void rejectsAmbiguousOrQuotedCommands() {
        for (String text : new String[] {
                "你会握手吗",
                "能不能给我握个手",
                "不要握手",
                "别比心",
                "别挥手",
                "他说握手",
                "刚才说请挥手",
                "如果可以就握手",
                "假如方便请比心",
                "“握手”",
                "\"请挥手\"",
                "我想了解如何握手"
        }) {
            check(PresetMotionCatalog.resolveVoiceCommand(text) == null, "must reject: " + text);
            check(PresetMotionCatalog.mentionsMotionText(text), "must preserve action context: " + text);
        }
    }

    private static void verifiesModelCannotPromoteRejectedSpeech() {
        for (String text : new String[] {"能不能握手？", "不要握手", "他说“请挥手”", "如果可以就比心"}) {
            NavigationIntent intent = NavigationIntent.fromJsonText(
                    "{\"intent\":\"preset_motion\",\"question\":\"" + text + "\"}");
            check(!intent.isPresetMotion(), "model action rejected: " + text);
            check("chat".equals(intent.intent), "rejected action becomes chat: " + text);
        }

        NavigationIntent accepted = NavigationIntent.fromJsonText(
                "{\"intent\":\"preset_motion\",\"question\":\"请和我握个手\"}");
        check(accepted.isPresetMotion(), "explicit command accepted");
        check("handshake".equals(accepted.targetName), "canonical handshake");
        check("right_hand".equals(accepted.motionArea), "handshake default area");
    }

    private static void assertSelection(String text, String motion, String area) {
        PresetMotionCatalog.Selection selection = PresetMotionCatalog.resolveVoiceCommand(text);
        check(selection != null, "selection exists: " + text);
        check(motion.equals(selection.motion().name()), "motion: " + text);
        check(area.equals(selection.area()), "area: " + text);
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
