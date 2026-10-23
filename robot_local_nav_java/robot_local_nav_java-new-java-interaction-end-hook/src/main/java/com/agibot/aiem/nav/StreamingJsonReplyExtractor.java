package com.agibot.aiem.nav;

import java.util.function.Consumer;

final class StreamingJsonReplyExtractor {
    private final StringBuilder content = new StringBuilder();
    private final StringBuilder emittedReply = new StringBuilder();
    private final Consumer<String> replyConsumer;
    private String intent;

    StreamingJsonReplyExtractor(Consumer<String> replyConsumer) {
        this.replyConsumer = replyConsumer;
    }

    void accept(String delta) {
        if (delta == null || delta.isEmpty()) {
            return;
        }
        content.append(delta);
        if (intent == null) {
            ParsedString value = findStringField(content, "intent");
            if (value != null && value.complete()) {
                intent = value.value();
            }
        }
        if (!"chat".equals(intent)) {
            return;
        }
        ParsedString reply = findStringField(content, "reply");
        if (reply == null || reply.value().length() <= emittedReply.length()) {
            return;
        }
        String next = reply.value().substring(emittedReply.length());
        emittedReply.append(next);
        replyConsumer.accept(next);
    }

    boolean isChatIntent() {
        return "chat".equals(intent);
    }

    String streamedReply() {
        return emittedReply.toString();
    }

    private static ParsedString findStringField(CharSequence source, String expectedKey) {
        String text = source.toString();
        int cursor = 0;
        while (cursor < text.length()) {
            int quote = text.indexOf('"', cursor);
            if (quote < 0) {
                return null;
            }
            ParsedString key = parseString(text, quote);
            if (!key.complete()) {
                return null;
            }
            int afterKey = skipWhitespace(text, key.endIndex() + 1);
            if (key.value().equals(expectedKey)
                    && afterKey < text.length()
                    && text.charAt(afterKey) == ':') {
                int valueStart = skipWhitespace(text, afterKey + 1);
                if (valueStart >= text.length() || text.charAt(valueStart) != '"') {
                    return null;
                }
                return parseString(text, valueStart);
            }
            cursor = key.endIndex() + 1;
        }
        return null;
    }

    private static ParsedString parseString(String text, int quoteIndex) {
        StringBuilder value = new StringBuilder();
        int cursor = quoteIndex + 1;
        while (cursor < text.length()) {
            char current = text.charAt(cursor++);
            if (current == '"') {
                return new ParsedString(value.toString(), cursor - 1, true);
            }
            if (current != '\\') {
                value.append(current);
                continue;
            }
            if (cursor >= text.length()) {
                return new ParsedString(value.toString(), -1, false);
            }
            char escaped = text.charAt(cursor++);
            switch (escaped) {
                case '"', '\\', '/' -> value.append(escaped);
                case 'b' -> value.append('\b');
                case 'f' -> value.append('\f');
                case 'n' -> value.append('\n');
                case 'r' -> value.append('\r');
                case 't' -> value.append('\t');
                case 'u' -> {
                    if (cursor + 4 > text.length()) {
                        return new ParsedString(value.toString(), -1, false);
                    }
                    int codeUnit = 0;
                    for (int index = 0; index < 4; index++) {
                        int digit = Character.digit(text.charAt(cursor + index), 16);
                        if (digit < 0) {
                            return new ParsedString(value.toString(), -1, false);
                        }
                        codeUnit = (codeUnit << 4) | digit;
                    }
                    value.append((char) codeUnit);
                    cursor += 4;
                }
                default -> {
                    return new ParsedString(value.toString(), -1, false);
                }
            }
        }
        return new ParsedString(value.toString(), -1, false);
    }

    private static int skipWhitespace(String text, int cursor) {
        while (cursor < text.length() && Character.isWhitespace(text.charAt(cursor))) {
            cursor++;
        }
        return cursor;
    }

    private record ParsedString(String value, int endIndex, boolean complete) {
    }
}
