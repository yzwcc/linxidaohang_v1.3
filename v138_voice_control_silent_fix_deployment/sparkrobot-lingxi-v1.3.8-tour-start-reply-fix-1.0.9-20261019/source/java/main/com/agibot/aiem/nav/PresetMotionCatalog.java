package com.agibot.aiem.nav;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

final class PresetMotionCatalog {
    record Motion(String name, String label, String defaultArea, Set<String> allowedAreas, String reply) {}
    record Selection(Motion motion, String area) {}

    private static final Map<String, Motion> MOTIONS = new LinkedHashMap<>();
    private static final Map<String, String> ALIASES = new LinkedHashMap<>();

    static {
        register("wave", "挥手", "right_hand", Set.of("left_hand", "right_hand"), "好的，给你挥挥手。", "wave_hand", "挥手", "greet", "greeting", "say_hello", "打招呼", "打个招呼");
        register("handshake", "握手", "right_hand", Set.of("left_hand", "right_hand"), "好的，和你握个手。", "shake_hand", "握手");
        register("raise_hand", "举手", "right_hand", Set.of("left_hand", "right_hand"), "好的，我来举手。", "raisehand", "举手");
        register("flying_kiss", "飞吻", "right_hand", Set.of("left_hand", "right_hand"), "好的，送你一个飞吻。", "blow_kiss", "飞吻");
        register("sweetheart", "比心", "both_hands", Set.of("left_hand", "right_hand", "both_hands"), "好的，给你比个心。", "heart", "heart_pose", "比心");
        register("high_five", "击掌", "right_hand", Set.of("left_hand", "right_hand"), "好的，来击个掌。", "highfive", "击掌");
        register("salute", "敬礼", "right_hand", Set.of("left_hand", "right_hand"), "好的，向你敬礼。", "敬礼");
        register("horizontal_arm", "平举", "both_hands", Set.of("left_hand", "right_hand", "both_hands"), "好的，我来平举双手。", "arms_horizontal", "平举");
        register("hug", "拥抱", "whole_body", Set.of("whole_body"), "好的，给你一个拥抱。", "拥抱");
        register("cheer", "加油", "whole_body", Set.of("whole_body"), "好的，一起加油。", "加油");
        register("clap", "鼓掌", "whole_body", Set.of("whole_body"), "好的，我来鼓掌。", "clap_hands", "鼓掌");
        register("handx", "双手打叉", "whole_body", Set.of("whole_body"), "好的，我来做双手打叉动作。", "hand_x", "双手打叉");
        register("chestwave", "胸前挥手", "right_hand", Set.of("left_hand", "right_hand"), "好的，我在胸前挥挥手。", "chest_wave", "胸前挥手");
        register("bow", "鞠躬", "whole_body", Set.of("whole_body"), "好的，向你鞠个躬。", "鞠躬");
        register("bye", "拜拜", "whole_body", Set.of("whole_body"), "好的，拜拜。", "goodbye", "拜拜");
        register("lightwave", "动感光波", "whole_body", Set.of("whole_body"), "好的，来一个动感光波。", "light_wave", "动感光波");
        register("scratch_head", "挠头", "whole_body", Set.of("whole_body"), "好的，我来挠挠头。", "挠头");
        register("scratch_back", "抓屁股", "whole_body", Set.of("whole_body"), "好的，我来做这个动作。", "抓屁股");
    }

    private PresetMotionCatalog() {}

    static Selection resolve(String rawName, String rawArea) {
        String key = normalizeToken(rawName);
        String canonical = ALIASES.getOrDefault(key, key);
        Motion motion = MOTIONS.get(canonical);
        if (motion == null) return null;
        String requestedArea = normalizeArea(rawArea);
        String area = requestedArea.isBlank() || !motion.allowedAreas().contains(requestedArea)
                ? motion.defaultArea()
                : requestedArea;
        return new Selection(motion, area);
    }

    /**
     * Resolve only high-confidence imperative motion phrases locally.  This
     * keeps a network/model timeout from turning an unambiguous robot action
     * into a failed voice request.
     */
    static Selection resolveVoiceCommand(String rawText) {
        String text = normalizeSpeechText(rawText);
        if (text.isBlank() || looksLikeMotionQuestion(text) || hasNegationOrQuotedSpeech(text)) return null;
        String imperative = text.replaceFirst("^(?:你好)?(?:灵犀|灵心)", "")
                .replaceFirst("^(?:能不能|可不可以|可以|麻烦|请)(?:你)?", "");
        if (imperative.matches("(?:(?:给|跟|和|向)(?:我|我们|大家|客人|小朋友|他们))?"
                + "(?:打个招呼|打招呼|打一下招呼|挥手打招呼|挥挥手打个招呼)(?:一下|吧|呗|呀|啊)?")) {
            return resolve("wave", requestedArea(text));
        }
        if (imperative.length() > 24 || !imperative.matches(
                "^(?:给我|跟我|和我|向我|来|做|用|握|比|挥|摆|举|抬|飞吻|送个吻|亲一个|击掌|拍手|敬礼|鞠躬|鼓掌|拜拜|再见).*$")) return null;
        String area = requestedArea(text);
        if (containsAny(text, "握手", "握个手", "握一下手", "握握手")) {
            return resolve("handshake", area);
        }
        if (containsAny(text, "比心", "比个心", "比爱心", "比个爱心", "爱心")) {
            return resolve("sweetheart", area);
        }
        if (containsAny(text, "挥手", "挥挥手", "挥一下手", "摆手", "摆摆手")) {
            return resolve("wave", area);
        }
        if (containsAny(text, "举手", "抬手")) {
            return resolve("raise_hand", area);
        }
        if (containsAny(text, "飞吻", "送个吻", "亲一个")) {
            return resolve("flying_kiss", area);
        }
        if (containsAny(text, "击掌", "拍手")) {
            return resolve("high_five", area);
        }
        if (containsAny(text, "敬礼")) {
            return resolve("salute", area);
        }
        if (containsAny(text, "鞠躬")) {
            return resolve("bow", area);
        }
        if (containsAny(text, "鼓掌")) {
            return resolve("clap", area);
        }
        if (containsAny(text, "拜拜", "再见")) {
            return resolve("bye", area);
        }
        return null;
    }

    static String promptDescription() {
        StringBuilder result = new StringBuilder();
        for (Motion motion : MOTIONS.values()) {
            if (result.length() > 0) result.append("；");
            result.append(motion.label()).append('=').append(motion.name())
                    .append('(').append(String.join("/", motion.allowedAreas())).append(')');
        }
        return result.toString();
    }

    private static void register(
            String name,
            String label,
            String defaultArea,
            Set<String> allowedAreas,
            String reply,
            String... aliases) {
        Motion motion = new Motion(name, label, defaultArea, Set.copyOf(allowedAreas), reply);
        MOTIONS.put(name, motion);
        ALIASES.put(name, name);
        for (String alias : aliases) ALIASES.put(normalizeToken(alias), name);
    }

    private static String normalizeArea(String value) {
        String normalized = normalizeToken(value);
        return switch (normalized) {
            case "left", "lefthand", "左手" -> "left_hand";
            case "right", "righthand", "右手" -> "right_hand";
            case "both", "bothhands", "双手" -> "both_hands";
            case "whole", "wholebody", "全身" -> "whole_body";
            default -> normalized;
        };
    }

    private static String requestedArea(String text) {
        if (containsAny(text, "双手", "两只手", "两手")) return "both_hands";
        if (text.contains("左手")) return "left_hand";
        if (text.contains("右手")) return "right_hand";
        return "";
    }

    private static boolean looksLikeMotionQuestion(String text) {
        if (!containsAny(text, "你会", "会不会", "能不能", "可以吗", "能吗", "会吗", "怎么做", "是什么")) {
            return false;
        }
        return !containsAny(text, "给我", "跟我", "和我", "请", "来个", "来一个", "做个", "做一下", "一下", "吧");
    }

    static boolean hasNegationOrQuotedSpeech(String text) {
        String normalized = normalizeSpeechText(text).replace("能不能", "能否");
        return containsAny(normalized, "不要", "别", "不用", "不想", "不需要", "不许",
                "不能", "禁止", "取消", "停止", "他说", "她说", "刚才说", "如果", "假如", "不是让");
    }

    private static boolean containsAny(String text, String... values) {
        for (String value : values) {
            if (text.contains(value)) return true;
        }
        return false;
    }

    private static String normalizeSpeechText(String value) {
        return value == null ? "" : value.toLowerCase(Locale.ROOT)
                .replaceAll("[\\s\\p{Punct}，。！？、；：“”‘’（）【】《》…—-]+", "")
                .trim();
    }

    private static String normalizeToken(String value) {
        return value == null ? "" : value.trim().toLowerCase(Locale.ROOT)
                .replace('-', '_').replace(' ', '_');
    }
}
