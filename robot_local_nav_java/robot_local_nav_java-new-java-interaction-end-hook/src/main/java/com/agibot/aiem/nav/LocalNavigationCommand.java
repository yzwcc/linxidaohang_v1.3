package com.agibot.aiem.nav;

import java.util.List;
import java.util.Locale;

final class LocalNavigationCommand {
    private static final List<String> PREFIXES = List.of(
            "麻烦你带我去", "麻烦你带我到", "麻烦带我去", "麻烦带我到",
            "能不能带我去", "可以带我去", "请帮我导航到", "帮我导航到",
            "请带我前往", "带我前往", "请导航到", "请导航去",
            "请带我去", "请带我到", "导航到", "导航去",
            "带我去", "带我到", "领我去", "领我到",
            "我想去", "前往", "去");
    private static final List<String> SUFFIXES = List.of(
            "谢谢你", "谢谢", "一下", "吧");

    private LocalNavigationCommand() {
    }

    static NavigationIntent resolve(String text, List<String> pointNames) {
        if (isQuestion(text)) {
            return null;
        }
        String normalizedText = normalize(text);
        if (normalizedText.isEmpty() || pointNames == null || pointNames.isEmpty()) {
            return null;
        }

        for (String prefix : PREFIXES) {
            if (!normalizedText.startsWith(prefix)) {
                continue;
            }
            String target = normalizedText.substring(prefix.length());
            String matchedName = findExactName(target, pointNames);
            if (matchedName == null) {
                for (String suffix : SUFFIXES) {
                    if (!target.endsWith(suffix) || target.length() <= suffix.length()) {
                        continue;
                    }
                    matchedName = findExactName(
                            target.substring(0, target.length() - suffix.length()), pointNames);
                    if (matchedName != null) {
                        break;
                    }
                }
            }
            return matchedName == null ? null : NavigationIntent.navigate(matchedName, text);
        }
        return null;
    }

    private static boolean isQuestion(String text) {
        if (text == null) {
            return false;
        }
        String trimmed = text.trim();
        return trimmed.endsWith("?")
                || trimmed.endsWith("？")
                || trimmed.endsWith("吗")
                || trimmed.endsWith("么")
                || trimmed.endsWith("呢")
                || trimmed.endsWith("可不可以")
                || trimmed.endsWith("行不行")
                || trimmed.endsWith("好不好")
                || trimmed.endsWith("是不是")
                || trimmed.endsWith("是否");
    }

    private static String findExactName(String target, List<String> pointNames) {
        String normalizedTarget = normalize(target);
        if (normalizedTarget.isEmpty()) {
            return null;
        }
        for (String pointName : pointNames) {
            if (normalize(pointName).equals(normalizedTarget)) {
                return pointName.trim();
            }
        }
        return null;
    }

    private static String normalize(String value) {
        if (value == null) {
            return "";
        }
        return value.trim()
                .toLowerCase(Locale.ROOT)
                .replaceAll("\\s+", "")
                .replaceAll("^[，。！？、；：,.!?;:]+|[，。！？、；：,.!?;:]+$", "");
    }
}
