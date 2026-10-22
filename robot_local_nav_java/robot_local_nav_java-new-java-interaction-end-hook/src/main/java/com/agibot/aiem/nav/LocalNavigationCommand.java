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
    private static final List<String> POLITE_PREFIXES = List.of(
            "麻烦你", "请帮我", "请你", "赶紧", "快点", "麻烦", "帮我", "我想", "请", "快");
    private static final List<String> NAVIGATION_STOP_COMMANDS = List.of(
            "停", "停下", "停下来", "停一下", "先停一下", "停止", "停止导航", "取消导航", "取消当前导航",
            "急停", "停一停", "别走", "不要走", "别动", "不要动");
    private static final List<String> TASK_STOP_COMMANDS = List.of(
            "结束任务", "停止任务", "终止任务", "结束讲解", "停止讲解", "终止讲解",
            "结束讲解任务", "停止讲解任务", "终止讲解任务");

    private LocalNavigationCommand() {
    }

    static NavigationIntent resolve(String text, List<String> pointNames) {
        if (isQuestion(text) || hasQuotedOrConditionalContext(text)) {
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

    static Resolution resolveDetailed(String text, List<String> pointNames,
                                      List<String> ambiguousPointNames) {
        if (isQuestion(text) || hasQuotedOrConditionalContext(text)) {
            return Resolution.noMatch();
        }
        String normalizedText = normalize(text);
        if (normalizedText.isEmpty() || pointNames == null || pointNames.isEmpty()) {
            return Resolution.noMatch();
        }
        for (String prefix : PREFIXES) {
            if (!normalizedText.startsWith(prefix)) {
                continue;
            }
            String target = normalizedText.substring(prefix.length());
            Resolution result = resolveTarget(target, pointNames, ambiguousPointNames);
            if (result.matched() || result.ambiguous()) {
                return result;
            }
            for (String suffix : SUFFIXES) {
                if (!target.endsWith(suffix) || target.length() <= suffix.length()) {
                    continue;
                }
                result = resolveTarget(
                        target.substring(0, target.length() - suffix.length()),
                        pointNames, ambiguousPointNames);
                if (result.matched() || result.ambiguous()) {
                    return result;
                }
            }
            return Resolution.noMatch();
        }
        return Resolution.noMatch();
    }

    static boolean hasExplicitNavigationCommand(String text) {
        if (isQuestion(text) || hasQuotedOrConditionalContext(text)) {
            return false;
        }
        String normalizedText = normalize(text);
        return PREFIXES.stream().anyMatch(normalizedText::startsWith);
    }

    static NavigationIntent resolveStopCommand(String text) {
        String command = directCommandText(text);
        if (NAVIGATION_STOP_COMMANDS.contains(command)) {
            return NavigationIntent.stop();
        }
        if (TASK_STOP_COMMANDS.contains(command)) {
            return NavigationIntent.endTask();
        }
        return null;
    }

    private static String directCommandText(String text) {
        if (isQuestion(text) || hasQuotedOrConditionalContext(text)) {
            return "";
        }
        String command = normalize(text);
        for (String prefix : POLITE_PREFIXES) {
            if (command.startsWith(prefix) && command.length() > prefix.length()) {
                return command.substring(prefix.length());
            }
        }
        return command;
    }

    private static boolean hasQuotedOrConditionalContext(String text) {
        if (text == null) {
            return false;
        }
        if (text.matches(".*[\"'“”‘’「」『』《》].*")) {
            return true;
        }
        String normalizedText = normalize(text);
        return normalizedText.contains("如果")
                || normalizedText.contains("假如")
                || normalizedText.contains("要是")
                || normalizedText.contains("除非")
                || normalizedText.contains("万一")
                || normalizedText.contains("假设");
    }

    private static Resolution resolveTarget(String target, List<String> pointNames,
                                            List<String> ambiguousPointNames) {
        String normalizedTarget = normalize(target);
        if (normalizedTarget.isEmpty()) {
            return Resolution.noMatch();
        }
        if (ambiguousPointNames != null) {
            for (String name : ambiguousPointNames) {
                if (normalize(name).equals(normalizedTarget)) {
                    return Resolution.ambiguous(name);
                }
            }
        }
        String matchedName = null;
        int matchCount = 0;
        for (String pointName : pointNames) {
            if (normalize(pointName).equals(normalizedTarget)) {
                matchedName = pointName.trim();
                matchCount++;
            }
        }
        if (matchCount > 1) {
            return Resolution.ambiguous(matchedName);
        }
        return matchCount == 1 ? Resolution.match(matchedName) : Resolution.noMatch();
    }

    static String ambiguousPointReply() {
        return "这个叫法对应多个导航点，请说出更完整的点位名称。";
    }

    record Resolution(String targetName, String ambiguousName) {
        static Resolution noMatch() {
            return new Resolution("", "");
        }

        static Resolution match(String targetName) {
            return new Resolution(targetName, "");
        }

        static Resolution ambiguous(String name) {
            return new Resolution("", name == null ? "" : name.trim());
        }

        boolean matched() {
            return !targetName.isBlank();
        }

        boolean ambiguous() {
            return !ambiguousName.isBlank();
        }
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
