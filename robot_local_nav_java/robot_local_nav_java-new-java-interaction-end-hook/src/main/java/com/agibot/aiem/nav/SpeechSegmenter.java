package com.agibot.aiem.nav;

import java.util.ArrayList;
import java.util.List;

final class SpeechSegmenter {
    private static final int MIN_SOFT_BOUNDARY = 14;
    private static final int MAX_CODE_POINTS = 36;
    private final StringBuilder pending = new StringBuilder();

    List<String> append(String value) {
        if (value != null && !value.isEmpty()) {
            pending.append(value);
        }
        return drain(false);
    }

    List<String> finish() {
        return drain(true);
    }

    void cancel() {
        pending.setLength(0);
    }

    private List<String> drain(boolean flushRemainder) {
        ArrayList<String> segments = new ArrayList<>();
        while (!pending.isEmpty()) {
            int hard = findBoundary(false);
            if (hard >= 0) {
                segments.add(take(hard + 1));
                continue;
            }
            int soft = findBoundary(true);
            if (soft >= 0) {
                segments.add(take(soft + 1));
                continue;
            }
            int codePoints = pending.codePointCount(0, pending.length());
            if (flushRemainder || codePoints >= MAX_CODE_POINTS) {
                int cut = pending.offsetByCodePoints(0, Math.min(codePoints, MAX_CODE_POINTS));
                segments.add(take(cut));
                continue;
            }
            break;
        }
        return List.copyOf(segments);
    }

    private int findBoundary(boolean soft) {
        int limit = pending.offsetByCodePoints(
                0, Math.min(pending.codePointCount(0, pending.length()), MAX_CODE_POINTS));
        int codePoints = 0;
        for (int index = 0; index < limit; ) {
            int codePoint = pending.codePointAt(index);
            index += Character.charCount(codePoint);
            codePoints++;
            if (soft && codePoints < MIN_SOFT_BOUNDARY) {
                continue;
            }
            if (isHardBoundary(codePoint) || soft && isSoftBoundary(codePoint)) {
                return index - 1;
            }
        }
        return -1;
    }

    private String take(int endExclusive) {
        String result = pending.substring(0, endExclusive);
        pending.delete(0, endExclusive);
        return result;
    }

    private static boolean isHardBoundary(int codePoint) {
        return ".!?;".indexOf(codePoint) >= 0
                || codePoint == '\n'
                || codePoint == '\u3002'
                || codePoint == '\uff01'
                || codePoint == '\uff1f'
                || codePoint == '\uff1b';
    }

    private static boolean isSoftBoundary(int codePoint) {
        return ",:".indexOf(codePoint) >= 0
                || codePoint == '\uff0c'
                || codePoint == '\uff1a';
    }
}
