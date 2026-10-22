package com.agibot.aiem.nav;

import java.text.Normalizer;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

final class PresetMotionCatalog {
    record Motion(String name, String label, String defaultArea, List<String> allowedAreas) {}
    record Selection(Motion motion, String area) {}

    private static final Map<String, Motion> MOTIONS = new LinkedHashMap<>();
    private static final Map<String, String> ALIASES = new LinkedHashMap<>();
    private static final List<String> PREFIXES = List.of(
            "我想让你", "麻烦你", "请你", "给大家", "给我们", "我想让", "麻烦",
            "帮我", "给我", "跟我", "和我", "向我", "请", "用",
            "来一个", "来个", "做一个", "做个", "做一下", "做", "摆一个", "摆个", "摆", "让你");
    private static final List<String> SUFFIXES = List.of(
            "给我看看", "给大家看看", "一下", "一个", "动作", "吧", "呗", "呀", "啊");
    private static final List<String> FORBIDDEN_CONTEXT = List.of(
            "不要", "不用", "不想", "不需要", "不许", "不能", "禁止", "取消", "停止",
            "他说", "她说", "他们说", "刚才说", "转述", "转告", "听说", "据说", "提到",
            "如果", "假如", "要是", "倘若", "万一", "除非", "否则",
            "吗", "么", "能不能", "可不可以", "可以不可以", "能否", "会不会", "是否",
            "怎么做", "是什么");
    private static final List<String> QUOTE_MARKERS = List.of(
            "\"", "'", "“", "”", "‘", "’", "「", "」", "『", "』", "《", "》");

    static {
        register("wave", "挥手", "right_hand", List.of("left_hand", "right_hand"),
                "挥手", "挥挥手", "挥一下手", "摆手", "摆摆手", "打招呼", "打个招呼");
        register("handshake", "握手", "right_hand", List.of("left_hand", "right_hand"),
                "握手", "握个手", "握一下手", "握握手");
        register("raise_hand", "举手", "right_hand", List.of("left_hand", "right_hand"),
                "举手", "举一下手", "抬手", "抬一下手");
        register("flying_kiss", "飞吻", "right_hand", List.of("left_hand", "right_hand"),
                "飞吻", "送飞吻", "送个飞吻", "送一个飞吻", "亲一个");
        register("sweetheart", "比心", "both_hands", List.of("left_hand", "right_hand", "both_hands"),
                "比心", "比个心", "比爱心", "比个爱心", "做心形", "做心形手势");
        register("high_five", "击掌", "right_hand", List.of("left_hand", "right_hand"),
                "击掌", "击个掌", "来个击掌", "拍手");
        register("salute", "敬礼", "right_hand", List.of("left_hand", "right_hand"),
                "敬礼", "敬个礼");
        register("horizontal_arm", "平举", "both_hands", List.of("left_hand", "right_hand", "both_hands"),
                "平举", "平举双手", "双手平举", "手臂平举");
        register("hug", "拥抱", "whole_body", List.of("whole_body"),
                "拥抱", "抱抱", "做个拥抱");
        register("cheer", "加油", "whole_body", List.of("whole_body"),
                "加油动作", "做个加油", "来个加油动作");
        register("clap", "鼓掌", "whole_body", List.of("whole_body"),
                "鼓掌", "鼓掌一下", "拍手鼓掌");
        register("handx", "双手打叉", "whole_body", List.of("whole_body"),
                "双手打叉", "打叉", "双手交叉");
        register("chestwave", "胸前挥手", "right_hand", List.of("left_hand", "right_hand"),
                "胸前挥手", "在胸前挥手", "胸前挥挥手");
        register("bow", "鞠躬", "whole_body", List.of("whole_body"),
                "鞠躬", "鞠个躬");
        register("bye", "拜拜", "whole_body", List.of("whole_body"),
                "拜拜", "挥手告别", "说再见动作");
        register("lightwave", "动感光波", "whole_body", List.of("whole_body"),
                "动感光波", "发射动感光波");
        register("scratch_head", "挠头", "whole_body", List.of("whole_body"),
                "挠头", "挠挠头", "抓抓头");
        register("scratch_back", "抓屁股", "whole_body", List.of("whole_body"),
                "抓屁股", "挠屁股");
    }

    private PresetMotionCatalog() {}

    static Selection resolve(String rawName, String rawArea) {
        String key = normalizeToken(rawName);
        String canonical = ALIASES.getOrDefault(key, key);
        Motion motion = MOTIONS.get(canonical);
        if (motion == null) return null;

        String area = normalizeArea(rawArea);
        if (area.isBlank()) area = motion.defaultArea();
        if (!motion.allowedAreas().contains(area)) return null;
        return new Selection(motion, area);
    }

    static Selection resolveVoiceCommand(String rawText) {
        if (hasDisallowedContext(rawText)) return null;
        String command = normalizeSpeechText(rawText);
        if (command.isBlank()) return null;

        for (String wakeWord : List.of("你好灵犀", "嗨灵犀", "灵犀", "灵心")) {
            if (command.startsWith(wakeWord)) {
                command = command.substring(wakeWord.length());
                break;
            }
        }
        for (int count = 0; count < 5; count++) {
            String prefix = PREFIXES.stream().filter(command::startsWith).findFirst().orElse("");
            if (prefix.isEmpty()) break;
            command = command.substring(prefix.length());
        }

        String commandWithArea = command;
        String area = "";
        if (command.contains("双手") || command.contains("两只手") || command.contains("两手")) {
            area = "both_hands";
            command = command.replace("两只手", "").replace("双手", "").replace("两手", "");
        } else if (command.contains("左手")) {
            area = "left_hand";
            command = command.replace("左手", "");
        } else if (command.contains("右手")) {
            area = "right_hand";
            command = command.replace("右手", "");
        }

        for (int count = 0; count < 5; count++) {
            if (ALIASES.containsKey(command)) break;
            String suffix = SUFFIXES.stream().filter(command::endsWith).findFirst().orElse("");
            if (suffix.isEmpty()) break;
            command = command.substring(0, command.length() - suffix.length());
        }
        String canonical = ALIASES.get(command);
        if (canonical == null) return null;
        Selection selection = resolve(canonical, area);
        if (selection != null) return selection;
        if (!area.isBlank() && canonical.equals(ALIASES.get(commandWithArea))) {
            return resolve(canonical, "");
        }
        return null;
    }

    static boolean mentionsMotionText(String rawText) {
        String normalized = normalizeSpeechText(rawText);
        return !normalized.isBlank() && ALIASES.keySet().stream().anyMatch(normalized::contains);
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

    static List<Motion> all() {
        return List.copyOf(MOTIONS.values());
    }

    private static void register(
            String name,
            String label,
            String defaultArea,
            List<String> allowedAreas,
            String... aliases) {
        MOTIONS.put(name, new Motion(name, label, defaultArea, List.copyOf(allowedAreas)));
        ALIASES.put(name, name);
        for (String alias : aliases) ALIASES.put(normalizeSpeechText(alias), name);
    }

    private static String normalizeArea(String value) {
        return switch (normalizeToken(value)) {
            case "left", "lefthand", "左手" -> "left_hand";
            case "right", "righthand", "右手" -> "right_hand";
            case "both", "bothhands", "双手" -> "both_hands";
            case "whole", "wholebody", "全身" -> "whole_body";
            default -> normalizeToken(value);
        };
    }

    private static boolean hasDisallowedContext(String rawText) {
        if (rawText == null || rawText.isBlank()) return true;
        if (rawText.contains("?") || rawText.contains("？")) {
            return true;
        }
        for (String marker : QUOTE_MARKERS) {
            if (rawText.contains(marker)) return true;
        }
        String normalized = normalizeSpeechText(rawText);
        if (FORBIDDEN_CONTEXT.stream().anyMatch(normalized::contains)) return true;
        int negation = normalized.indexOf("别");
        if (negation < 0) return false;
        return ALIASES.keySet().stream()
                .anyMatch(alias -> normalized.indexOf(alias, negation + 1) >= 0);
    }

    private static String normalizeSpeechText(String value) {
        if (value == null) return "";
        String normalized = Normalizer.normalize(value, Normalizer.Form.NFKC).toLowerCase(Locale.ROOT);
        StringBuilder result = new StringBuilder(normalized.length());
        normalized.codePoints().forEach(codePoint -> {
            int type = Character.getType(codePoint);
            boolean punctuation = type == Character.CONNECTOR_PUNCTUATION
                    || type == Character.DASH_PUNCTUATION
                    || type == Character.START_PUNCTUATION
                    || type == Character.END_PUNCTUATION
                    || type == Character.INITIAL_QUOTE_PUNCTUATION
                    || type == Character.FINAL_QUOTE_PUNCTUATION
                    || type == Character.OTHER_PUNCTUATION;
            if (!Character.isWhitespace(codePoint) && !punctuation) result.appendCodePoint(codePoint);
        });
        return result.toString();
    }

    private static String normalizeToken(String value) {
        return value == null ? "" : value.trim().toLowerCase(Locale.ROOT)
                .replace('-', '_').replace(' ', '_');
    }
}
