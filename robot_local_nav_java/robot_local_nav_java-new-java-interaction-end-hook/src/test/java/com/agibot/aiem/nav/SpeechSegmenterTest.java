package com.agibot.aiem.nav;

import java.util.List;

final class SpeechSegmenterTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) {
            throw new AssertionError(name);
        }
        checks++;
    }

    public static void main(String[] args) {
        SpeechSegmenter segmenter = new SpeechSegmenter();
        check(segmenter.append("第一句。第二句").equals(List.of("第一句。")),
                "hard punctuation flushes a complete sentence");
        check(segmenter.finish().equals(List.of("第二句")),
                "finish flushes the remaining text");

        segmenter = new SpeechSegmenter();
        check(segmenter.append("12345678901234，后续").equals(List.of("12345678901234，")),
                "soft punctuation flushes only after the minimum prefix length");

        segmenter = new SpeechSegmenter();
        List<String> longSegments = segmenter.append("😀".repeat(37));
        check(longSegments.size() == 1, "long text emits one bounded segment");
        check(longSegments.get(0).codePointCount(0, longSegments.get(0).length()) == 36,
                "segment limit counts Unicode code points");
        check(segmenter.finish().equals(List.of("😀")),
                "supplementary characters remain intact after splitting");

        segmenter = new SpeechSegmenter();
        segmenter.append("pending");
        segmenter.cancel();
        check(segmenter.finish().isEmpty(), "cancel discards the unsubmitted remainder");
        System.out.println("SpeechSegmenterTest passed: " + checks + " assertions");
    }
}
