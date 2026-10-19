package com.agibot.aiem.nav;

import java.util.List;

/** Validates explicit requests against the current map; never invents a destination. */
final class NavigationCommandFallback {
    static NavigationIntent resolve(String text, List<String> names) {
        String normalized = commandText(text);
        if (PresetMotionCatalog.hasNegationOrQuotedSpeech(normalized)) return null;
        String destination = normalized.replaceFirst(
                "^(?:(?:带|领)(?:我们|我)(?:去|到|前往)|"
                + "(?:导航|带路)(?:带)?(?:我们|我)?(?:去|到|前往)|前往|去)", "");
        if (destination.equals(normalized)) return null;
        String selected = null;
        for (String name : names) {
            if (!SpeechInputPolicy.normalize(name).equals(destination)) continue;
            if (selected != null && !selected.equals(name)) return null;
            selected = name;
        }
        return selected == null ? null : NavigationIntent.navigate(selected);
    }

    static String commandText(String text) {
        return SpeechInputPolicy.normalize(text)
                .replaceFirst("^(?:(?:你好|嗨)?(?:灵犀|灵溪|灵心)){1,2}", "")
                .replaceFirst("^(?:(?:麻烦|请你|请|帮我|劳驾|拜托|你能不能|能不能|能否)){1,3}", "");
    }

    static boolean isExplanatoryQuestion(String text) {
        return commandText(text).matches(".*(?:怎么|如何|为什么|什么意思|是什么|是否支持|会不会).*");
    }
}
