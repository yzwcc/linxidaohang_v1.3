package com.agibot.aiem.nav;

final class KnowledgeAnswerPolicyTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        if (!condition) {
            throw new AssertionError(name);
        }
        checks++;
    }

    public static void main(String[] args) {
        check(!KnowledgeAnswerPolicy.shouldLookup("你好呀！"),
                "simple greeting skips knowledge retrieval");
        check(!KnowledgeAnswerPolicy.shouldLookup("谢谢。"),
                "courtesy reply skips knowledge retrieval");
        check(!KnowledgeAnswerPolicy.shouldLookup("讲个笑话"),
                "generative request skips knowledge retrieval");
        check(!KnowledgeAnswerPolicy.shouldLookup("陪我聊聊天"),
                "open-ended companionship skips knowledge retrieval");
        check(!KnowledgeAnswerPolicy.shouldLookup("为什么天空是蓝色的？"),
                "general knowledge does not add a serial remote lookup");
        check(KnowledgeAnswerPolicy.shouldLookup("你们展厅几点开放？"),
                "venue fact still uses knowledge retrieval");
        check(KnowledgeAnswerPolicy.requiresGrounding("洗手间在哪里？"),
                "facility directions require grounding");
        check(!KnowledgeAnswerPolicy.requiresGrounding("这里的环境真漂亮。"),
                "local mentions in a non-factual statement do not trigger refusal");
        check(!KnowledgeAnswerPolicy.requiresGrounding("我现在很开心。"),
                "current-time wording in a personal statement does not trigger refusal");
        check(!KnowledgeAnswerPolicy.shouldLookup("我现在很开心。"),
                "ordinary personal statement skips knowledge retrieval");
        check(!KnowledgeAnswerPolicy.requiresGrounding("你现在怎么看人工智能？"),
                "general opinion question is not mistaken for a current fact");
        check(KnowledgeAnswerPolicy.requiresGrounding("我们展厅今天几点开放？"),
                "current venue fact requires grounding");
        check(KnowledgeAnswerPolicy.requiresGrounding("机器人现在在哪个位置？"),
                "robot state requires grounding");
        check(KnowledgeAnswerPolicy.requiresGrounding("机器人能不能跳舞？"),
                "robot capability questions require grounding");
        check(!KnowledgeAnswerPolicy.requiresGrounding("为什么天空是蓝色的？"),
                "general knowledge is not treated as a local fact");
        check(KnowledgeAnswerPolicy.GROUNDED_FALLBACK.equals(
                        KnowledgeAnswerPolicy.chooseReply(
                                "你们展厅几点开放？", "模型猜测的时间", null)),
                "unsupported local fact is refused");
        check("资料中的开放时间是九点。".equals(
                        KnowledgeAnswerPolicy.chooseReply(
                                "你们展厅几点开放？", "模型猜测的时间", "资料中的开放时间是九点。")),
                "grounded retrieval answer takes precedence");
        check("天空呈蓝色主要与光的散射有关。".equals(
                        KnowledgeAnswerPolicy.chooseReply(
                                "为什么天空是蓝色的？", "天空呈蓝色主要与光的散射有关。", null)),
                "general conversational answer is preserved");
        check(KnowledgeAnswerPolicy.chooseReply("讲个故事", "", null)
                        .equals("抱歉，我刚才没有听清楚，请再说一遍。"),
                "empty model result uses a fixed recovery response");
        check("你好，我是灵犀，很高兴和你聊天。".equals(
                        KnowledgeAnswerPolicy.quickReply("你好呀！", "灵犀")),
                "greeting can be answered deterministically without model inference");
        check("不客气，很高兴能帮到你。".equals(
                        KnowledgeAnswerPolicy.quickReply("谢谢你", "灵犀")),
                "thanks can be answered deterministically");
        check(KnowledgeAnswerPolicy.quickReply("带我去展厅，然后说你好", "灵犀") == null,
                "mixed navigation instruction is not mistaken for a greeting");
        System.out.println("KnowledgeAnswerPolicyTest passed: " + checks + " assertions");
    }
}
