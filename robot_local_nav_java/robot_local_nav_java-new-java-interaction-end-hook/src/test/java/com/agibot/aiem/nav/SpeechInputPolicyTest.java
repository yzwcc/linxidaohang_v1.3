package com.agibot.aiem.nav;

import java.util.List;

public final class SpeechInputPolicyTest {
    private static int checks;

    private static void check(boolean value, String name) {
        if (!value) throw new AssertionError(name);
        checks++;
    }

    public static void main(String[] args) {
        for (String text : List.of("", " ", "。", "嗯", "啊啊", "好好好嗯", "4",
                "哈哈哈", "你好你好", "那个这个")) {
            check(SpeechInputPolicy.isLikelyNoise(text), "noise rejected: " + text);
        }
        for (String text : List.of("是", "否", "不", "对", "行", "停", "继续",
                "停止导航", "什么是智元机器人", "我需要办理业务")) {
            check(!SpeechInputPolicy.isLikelyNoise(text), "meaningful input retained: " + text);
        }
        check(SpeechInputPolicy.isEcho("我会做甜甜的星星梦", "我会做甜甜的星星梦，梦里全是小朋友"),
                "long playback fragment detected");
        check(!SpeechInputPolicy.isEcho("停", "停"), "short safety command is never treated as echo");
        check(SpeechInputPolicy.isAddressed("你好，灵犀，今天星期几"), "wake alias normalization");
        System.out.println("SpeechInputPolicyTest passed: " + checks + " assertions");
    }
}
