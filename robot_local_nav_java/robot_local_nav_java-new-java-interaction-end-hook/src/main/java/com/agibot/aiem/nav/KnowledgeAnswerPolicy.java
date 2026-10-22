package com.agibot.aiem.nav;

import java.util.List;
import java.util.Locale;

final class KnowledgeAnswerPolicy {
    static final String GROUNDED_FALLBACK =
            "这个信息我目前没有核实到可靠资料，不能确定地回答。请向现场工作人员确认。";
    private static final String UNCLEAR_FALLBACK = "抱歉，我刚才没有听清楚，请再说一遍。";
    private static final List<String> LOCAL_FACT_MARKERS = List.of(
            "灵犀", "灵心", "火花", "机器人", "本机器人", "这台机器人", "这个机器人",
            "这里", "本馆", "展厅", "展馆", "场馆", "公司", "园区", "服务台", "前台", "接待台",
            "展品", "会议室", "地图", "点位", "导航", "讲解", "产品", "设备", "洗手间",
            "卫生间", "厕所", "电梯", "楼梯", "入口", "出口", "停车场", "停车位", "楼层");
    private static final List<String> CURRENT_FACT_MARKERS = List.of(
            "今天", "现在", "当前", "目前", "最新", "刚才", "几点", "什么时候", "日期",
            "天气", "价格", "多少钱", "费用", "电话", "地址", "路线", "位置", "哪一层",
            "几楼", "开放", "营业", "活动安排", "负责人", "联系方式", "票价", "版本",
            "型号", "参数", "配置", "服务时间", "哪里", "在哪", "哪儿", "哪边");
    private static final List<String> QUESTION_MARKERS = List.of(
            "什么", "怎么", "如何", "谁", "哪", "多少", "几", "吗", "有没有", "是否",
            "能不能", "能否", "会不会", "可以不可以");
    private static final List<String> OPINION_QUERY_MARKERS = List.of(
            "怎么看", "你认为", "你觉得", "谈谈你对", "评价一下");
    private static final List<String> SMALL_TALK = List.of(
            "你好", "您好", "嗨", "哈喽", "hello", "hi", "早上好", "中午好", "下午好",
            "晚上好", "谢谢", "多谢", "感谢", "辛苦了", "再见", "拜拜", "你好吗",
            "最近怎么样", "很高兴见到你");
    private static final List<String> GENERATIVE_REQUESTS = List.of(
            "讲个故事", "讲一个故事", "讲个笑话", "讲一个笑话", "说个笑话", "编个故事",
            "编一个故事", "陪我聊天", "陪我聊聊天", "夸夸我", "安慰我", "逗我开心");
    private KnowledgeAnswerPolicy() {
    }

    static boolean shouldLookup(String question) {
        String normalized = normalize(question);
        return !normalized.isEmpty()
                && !isSmallTalk(normalized)
                && !containsAny(normalized, GENERATIVE_REQUESTS)
                && requiresGrounding(question);
    }

    static String quickReply(String question, String robotName) {
        String normalized = normalize(question);
        if (!isSmallTalk(normalized)) {
            return null;
        }
        if (containsAny(normalized, List.of("谢谢", "多谢", "感谢", "辛苦了"))) {
            return "不客气，很高兴能帮到你。";
        }
        if (containsAny(normalized, List.of("再见", "拜拜"))) {
            return "再见，祝你今天愉快。";
        }
        if (containsAny(normalized, List.of("你好吗", "最近怎么样"))) {
            return "我运行正常，随时可以帮你。";
        }
        String name = robotName == null || robotName.isBlank() ? "灵犀" : robotName.trim();
        return "你好，我是" + name + "，很高兴和你聊天。";
    }

    static boolean requiresGrounding(String question) {
        String normalized = normalize(question);
        boolean questionLike = containsAny(normalized, QUESTION_MARKERS)
                || (question != null && question.trim().matches(".*[?？]$"));
        if (!questionLike) {
            return false;
        }
        if (containsAny(normalized, LOCAL_FACT_MARKERS)) {
            return true;
        }
        return containsAny(normalized, CURRENT_FACT_MARKERS)
                && !containsAny(normalized, OPINION_QUERY_MARKERS);
    }

    static String chooseReply(String question, String modelReply, String knowledgeAnswer) {
        if (knowledgeAnswer != null && !knowledgeAnswer.isBlank()) {
            return knowledgeAnswer.trim();
        }
        if (requiresGrounding(question)) {
            return GROUNDED_FALLBACK;
        }
        if (modelReply == null || modelReply.isBlank()) {
            return UNCLEAR_FALLBACK;
        }
        return modelReply.trim();
    }

    private static boolean isSmallTalk(String normalized) {
        for (String phrase : SMALL_TALK) {
            String candidate = normalize(phrase);
            if (normalized.equals(candidate)
                    || normalized.matches(java.util.regex.Pattern.quote(candidate) + "(你|呀|啊|哦|啦|呢)?")) {
                return true;
            }
        }
        return false;
    }

    private static boolean containsAny(String value, List<String> phrases) {
        for (String phrase : phrases) {
            if (value.contains(normalize(phrase))) {
                return true;
            }
        }
        return false;
    }

    private static String normalize(String value) {
        if (value == null) {
            return "";
        }
        return value.toLowerCase(Locale.ROOT)
                .replaceAll("[\\s\\p{Punct}，。！？、；：‘’“”《》【】（）]", "");
    }
}
