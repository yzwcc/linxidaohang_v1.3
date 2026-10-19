package com.agibot.aiem.nav;

final class SpeechInputPolicy {
    private SpeechInputPolicy() {}

    static String normalize(String text) {
        return text == null ? "" : text.toLowerCase(java.util.Locale.ROOT)
                .replaceAll("[\\p{P}\\p{Z}\\s]", "");
    }

    static boolean isFiller(String text) {
        String value = normalize(text);
        return value.isEmpty() || value.matches("[嗯啊呃哦噢唔额诶哎]+")
                || value.matches("好{1,4}[嗯啊哦]*");
    }

    static boolean isLikelyNoise(String text) {
        String value = normalize(text);
        if (isFiller(text)) return true;
        // ASR often emits a lone digit or punctuation fragment for background
        // sounds. It is never a useful robot request by itself.
        if (value.matches("\\d+")) return true;
        if (value.length() < 3) return false;

        int codePointCount = value.codePointCount(0, value.length());
        long distinctCount = value.codePoints().distinct().count();
        if (codePointCount >= 2 && distinctCount == 1) return true;

        // Reject a repeated ASR fragment such as "哈哈哈哈" or "你好你好".
        if (codePointCount >= 4 && value.matches("(.{1,8})\\1+")) return true;

        // ASR/VAD fragments made only of repeated hesitation syllables should not
        // open a wake session or reach the model. Keep meaningful short commands
        // such as "停止" and "继续" untouched.
        if (codePointCount >= 4 && distinctCount <= 2 && repeatedRatio(value) >= 0.75) {
            return true;
        }
        return value.matches("(?:嗯|啊|呃|哦|噢|唔|额|诶|哎|那个|这个){2,}");
    }

    static boolean isEcho(String input, String spoken) {
        String query = normalize(input);
        String reply = normalize(spoken);
        // Only suppress substantial verbatim fragments. Short commands remain usable.
        return query.length() >= 8 && reply.contains(query);
    }

    static boolean isAddressed(String text) {
        return normalize(text).matches("^(你好灵犀|灵犀灵犀|嗨灵犀|哈喽露米|嘿露米|嗨露米|灵溪灵溪|嗨灵溪|hellolumi|hilumi|heylumi).*");
    }

    private static double repeatedRatio(String value) {
        int total = value.codePointCount(0, value.length());
        int most = 0;
        for (int codePoint : value.codePoints().distinct().toArray()) {
            int count = (int) value.codePoints().filter(candidate -> candidate == codePoint).count();
            most = Math.max(most, count);
        }
        return total == 0 ? 0.0 : (double) most / total;
    }

    static boolean asksIdentity(String text) {
        return normalize(text).matches(".*(你是谁|你叫什么|介绍你自己|介绍一下你自己).* ".trim());
    }

    static String cleanReply(String question, String answer) {
        if (answer == null) return "";
        if (asksIdentity(question)) return answer.trim();
        String cleaned = answer.replaceAll("(?:你好[呀啊！!，, ]*)?我是(?:灵犀|灵溪)[^。！？!?.]*[。！？!?.]?", "").trim();
        return cleaned.isBlank() ? "请说一下你想了解的具体问题。" : cleaned;
    }
}
