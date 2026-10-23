package com.agibot.aiem.nav;

import java.util.ArrayList;
import java.util.List;

final class StreamingJsonReplyExtractorTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) {
            throw new AssertionError(name);
        }
        checks++;
    }

    public static void main(String[] args) {
        List<String> emitted = new ArrayList<>();
        StreamingJsonReplyExtractor extractor = new StreamingJsonReplyExtractor(emitted::add);
        extractor.accept("{\"reply\":\"held until intent.\",");
        check(emitted.isEmpty(), "reply is held until the chat intent is known");

        extractor.accept("\"intent\":\"chat\"}");
        check(extractor.isChatIntent(), "complete intent field unlocks chat reply extraction");
        check(String.join("", emitted).equals("held until intent."),
                "a single reply field before intent is emitted once chat is confirmed");

        List<String> decoded = new ArrayList<>();
        StreamingJsonReplyExtractor escaped = new StreamingJsonReplyExtractor(decoded::add);
        escaped.accept("{\"intent\":\"chat\",\"reply\":\"A\\u4e2");
        check(String.join("", decoded).equals("A"),
                "incomplete unicode escape does not leak partial escape characters");
        escaped.accept("dB\\nC\"}");
        check(String.join("", decoded).equals("A\u4e2dB\nC"),
                "escaped unicode and newlines are decoded across token boundaries");

        List<String> actionReply = new ArrayList<>();
        StreamingJsonReplyExtractor action =
                new StreamingJsonReplyExtractor(actionReply::add);
        action.accept("{\"intent\":\"navigate\",\"reply\":\"not a chat answer\"}");
        check(actionReply.isEmpty(), "non-chat replies never reach the speech consumer");
        System.out.println("StreamingJsonReplyExtractorTest passed: " + checks + " assertions");
    }
}
