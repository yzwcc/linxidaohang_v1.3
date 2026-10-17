package com.agibot.aiem.nav;

import java.util.List;

/** Used only when the model is unavailable; never guesses a destination. */
final class NavigationCommandFallback {
    static NavigationIntent resolve(String text, List<String> names) {
        String normalized = SpeechInputPolicy.normalize(text)
                .replaceFirst("^(?:你好)?(?:灵犀|灵心)", "")
                .replaceFirst("^(?:麻烦|请|帮我)", "");
        if (PresetMotionCatalog.hasNegationOrQuotedSpeech(normalized)) return null;
        String destination = normalized.replaceFirst(
                "^(?:(?:带|领)(?:我们|我)(?:去|到)|导航(?:去|到))", "");
        if (destination.equals(normalized)) return null;
        String selected = null;
        for (String name : names) {
            if (!SpeechInputPolicy.normalize(name).equals(destination)) continue;
            if (selected != null && !selected.equals(name)) return null;
            selected = name;
        }
        return selected == null ? null : NavigationIntent.navigate(selected);
    }
}
