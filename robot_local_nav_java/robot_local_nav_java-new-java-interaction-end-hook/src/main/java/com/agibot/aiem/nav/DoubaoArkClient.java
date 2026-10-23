/*
 * Decompiled with CFR 0.152.
 */
package com.agibot.aiem.nav;

import com.agibot.aiem.nav.AppConfig;
import com.agibot.aiem.nav.NavigationIntent;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.alibaba.fastjson2.JSONWriter;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Base64;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

final class DoubaoArkClient {
    private final HttpClient httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5L)).build();
    private final String url;
    private final String apiKey;
    private final String model;
    private final String robotDisplayName;

    DoubaoArkClient(AppConfig appConfig) {
        this.url = appConfig.doubaoUrl;
        this.apiKey = appConfig.effectiveDoubaoApiKey();
        this.model = appConfig.doubaoModel;
        this.robotDisplayName = appConfig.robotDisplayName;
    }

    NavigationIntent parseInteraction(String string, List<String> list) throws IOException, InterruptedException {
        String prompt = this.buildTextInteractionPrompt(list);
        String string2 = this.callModel(DoubaoArkClient.buildTextMessages(prompt, string), 0.0);
        return NavigationIntent.fromJsonText(string2);
    }

    NavigationIntent parseInteractionStreaming(
            String text, List<String> pointNames, Consumer<String> replyTextConsumer)
            throws IOException, InterruptedException {
        StreamingJsonReplyExtractor extractor = new StreamingJsonReplyExtractor(replyTextConsumer);
        String response = this.callModelStreaming(
                DoubaoArkClient.buildTextMessages(this.buildTextInteractionPrompt(pointNames), text),
                0.0,
                extractor::accept);
        NavigationIntent intent = NavigationIntent.fromJsonText(response);
        if (extractor.isChatIntent() && !"chat".equals(intent.intent)) {
            throw new IOException("Streaming interaction intent changed after reply output");
        }
        return intent;
    }

    NavigationIntent parseInteractionAudio(byte[] byArray, List<String> list) throws IOException, InterruptedException {
        byte[] byArray2 = DoubaoArkClient.pcm16leToWav(byArray, 16000, 1, 16);
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("data", Base64.getEncoder().encodeToString(byArray2));
        jSONObject.put("format", "wav");
        JSONArray jSONArray = new JSONArray();
        JSONObject jSONObject2 = new JSONObject();
        jSONObject2.put("type", "input_audio");
        jSONObject2.put("input_audio", jSONObject);
        jSONArray.add(jSONObject2);
        JSONObject jSONObject3 = new JSONObject();
        jSONObject3.put("type", "text");
        jSONObject3.put("text", DoubaoArkClient.buildAudioInteractionPrompt(list));
        jSONArray.add(jSONObject3);
        JSONArray jSONArray2 = new JSONArray();
        jSONArray2.add(DoubaoArkClient.message(
                "system", this.buildAudioSystemPrompt(list) + DoubaoArkClient.buildPresetMotionPrompt()));
        jSONArray2.add(DoubaoArkClient.message("user", jSONArray));
        String string = this.callModel(jSONArray2, 0.0);
        return NavigationIntent.fromJsonText(string);
    }

    String chat(String string) throws IOException, InterruptedException {
        String systemPrompt = """
                你是机器人“%s”的中文语音助手，对外只自称“%s”，不要提及底层模型或供应商。
                用自然、简洁的口语回答，通常不超过两句话。不要输出 JSON 或 Markdown。
                """.formatted(this.robotDisplayName, this.robotDisplayName)
                + DoubaoArkClient.buildChatReplyPrompt();
        return this.callModel(DoubaoArkClient.buildTextMessages(systemPrompt, string), 0.2);
    }

    String identityReply() {
        String string = this.robotDisplayName == null || this.robotDisplayName.isBlank() ? "\u7075\u7280" : this.robotDisplayName;
        return "\u6211\u662f" + string + "\uff0c\u53ef\u4ee5\u966a\u4f60\u804a\u5929\u3001\u56de\u7b54\u95ee\u9898\uff0c\u4e5f\u53ef\u4ee5\u914d\u5408\u73b0\u573a\u4e92\u52a8\u548c\u76f4\u64ad\u95f4\u4ea4\u6d41\u3002";
    }

    NavigationIntent understandNavigation(String string, List<String> list) throws IOException, InterruptedException {
        return this.parseInteraction(string, list);
    }

    private String callModel(JSONArray jSONArray, double d) throws IOException, InterruptedException {
        String string;
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("model", this.model);
        jSONObject.put("stream", false);
        jSONObject.put("temperature", d);
        jSONObject.put("messages", jSONArray);
        JSONObject jSONObject2 = new JSONObject();
        jSONObject2.put("type", "disabled");
        jSONObject.put("thinking", jSONObject2);
        HttpRequest httpRequest = HttpRequest.newBuilder().uri(URI.create(this.url)).timeout(Duration.ofSeconds(15L)).header("Content-Type", "application/json; charset=utf-8").header("Authorization", "Bearer " + this.apiKey).POST(HttpRequest.BodyPublishers.ofString(jSONObject.toJSONString(new JSONWriter.Feature[0]), StandardCharsets.UTF_8)).build();
        long startedAtNanos = System.nanoTime();
        HttpResponse<String> httpResponse = this.httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        long elapsedMs = java.util.concurrent.TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos);
        org.slf4j.LoggerFactory.getLogger(DoubaoArkClient.class).info(
                "Doubao request completed: model={}, status={}, elapsedMs={}, streaming=false",
                this.model, httpResponse.statusCode(), elapsedMs);
        if (httpResponse.statusCode() < 200 || httpResponse.statusCode() >= 300) {
            throw new IOException("Doubao HTTP " + httpResponse.statusCode() + ": " + httpResponse.body());
        }
        JSONObject jSONObject3 = JSON.parseObject(httpResponse.body());
        JSONArray jSONArray2 = jSONObject3.getJSONArray("choices");
        if (jSONArray2 == null || jSONArray2.isEmpty()) {
            throw new IOException("Doubao returned no choices: " + httpResponse.body());
        }
        JSONObject jSONObject4 = jSONArray2.getJSONObject(0).getJSONObject("message");
        String string2 = string = jSONObject4 == null ? "" : jSONObject4.getString("content");
        if (string == null || string.isBlank()) {
            throw new IOException("Doubao returned empty content: " + httpResponse.body());
        }
        return string.trim();
    }

    private String callModelStreaming(
            JSONArray messages, double temperature, Consumer<String> contentConsumer)
            throws IOException, InterruptedException {
        JSONObject body = new JSONObject();
        body.put("model", this.model);
        body.put("stream", true);
        body.put("temperature", temperature);
        body.put("messages", messages);
        JSONObject thinking = new JSONObject();
        thinking.put("type", "disabled");
        body.put("thinking", thinking);
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(this.url))
                .timeout(Duration.ofSeconds(45))
                .header("Content-Type", "application/json; charset=utf-8")
                .header("Accept", "text/event-stream")
                .header("Authorization", "Bearer " + this.apiKey)
                .POST(HttpRequest.BodyPublishers.ofString(
                        body.toJSONString(new JSONWriter.Feature[0]), StandardCharsets.UTF_8))
                .build();
        long startedAtNanos = System.nanoTime();
        long firstContentAtNanos = 0L;
        StringBuilder content = new StringBuilder();
        boolean sawDone = false;
        boolean sawFinishReason = false;
        HttpResponse<InputStream> response =
                this.httpClient.send(request, HttpResponse.BodyHandlers.ofInputStream());
        try (InputStream responseBody = response.body()) {
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                String errorBody = new String(responseBody.readAllBytes(), StandardCharsets.UTF_8);
                throw new IOException("Doubao streaming HTTP " + response.statusCode() + ": " + errorBody);
            }
            try (BufferedReader reader =
                    new BufferedReader(new InputStreamReader(responseBody, StandardCharsets.UTF_8))) {
                String line;
                StringBuilder eventData = new StringBuilder();
                while ((line = reader.readLine()) != null) {
                    if (line.isEmpty()) {
                        if (eventData.length() > 0) {
                            SseChunk chunk = this.readSseChunk(eventData.toString());
                            sawDone |= chunk.done();
                            sawFinishReason |= chunk.finished();
                            if (!chunk.content().isEmpty()) {
                                if (firstContentAtNanos == 0L) {
                                    firstContentAtNanos = System.nanoTime();
                                }
                                content.append(chunk.content());
                                contentConsumer.accept(chunk.content());
                            }
                            eventData.setLength(0);
                            if (sawDone) {
                                break;
                            }
                        }
                    } else if (!line.startsWith(":") && line.startsWith("data:")) {
                        if (eventData.length() > 0) {
                            eventData.append('\n');
                        }
                        String value = line.substring(5);
                        eventData.append(value.startsWith(" ") ? value.substring(1) : value);
                    }
                }
                if (!sawDone && eventData.length() > 0) {
                    SseChunk chunk = this.readSseChunk(eventData.toString());
                    sawDone |= chunk.done();
                    sawFinishReason |= chunk.finished();
                    if (!chunk.content().isEmpty()) {
                        if (firstContentAtNanos == 0L) {
                            firstContentAtNanos = System.nanoTime();
                        }
                        content.append(chunk.content());
                        contentConsumer.accept(chunk.content());
                    }
                }
            }
        }
        if (content.length() == 0 || (!sawDone && !sawFinishReason)) {
            throw new IOException("Doubao streaming response ended before a complete choice");
        }
        long elapsedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNanos);
        long firstContentMs = firstContentAtNanos == 0L
                ? -1L
                : TimeUnit.NANOSECONDS.toMillis(firstContentAtNanos - startedAtNanos);
        org.slf4j.LoggerFactory.getLogger(DoubaoArkClient.class).info(
                "Doubao streaming request completed: model={}, elapsedMs={}, firstContentMs={}, chars={}",
                this.model, elapsedMs, firstContentMs, content.length());
        return content.toString().trim();
    }

    private SseChunk readSseChunk(String data) throws IOException {
        if ("[DONE]".equals(data.trim())) {
            return new SseChunk("", true, false);
        }
        JSONObject root;
        try {
            root = JSON.parseObject(data);
        } catch (RuntimeException exception) {
            throw new IOException("Invalid Doubao streaming event", exception);
        }
        JSONArray choices = root == null ? null : root.getJSONArray("choices");
        if (choices == null || choices.isEmpty()) {
            return new SseChunk("", false, false);
        }
        JSONObject choice = choices.getJSONObject(0);
        String finishReason = choice.getString("finish_reason");
        JSONObject delta = choice.getJSONObject("delta");
        String content = delta == null ? null : delta.getString("content");
        return new SseChunk(
                content == null ? "" : content,
                false,
                finishReason != null && !finishReason.isBlank());
    }

    private String buildTextInteractionPrompt(List<String> pointNames) {
        return DoubaoArkClient.buildInteractionPrompt(pointNames)
                .replace("\u4e03\u7c7b", "\u5341\u7c7b")
                .replace(
                        "navigate|stop_navigation|start_task|end_task|sing|dance|chat",
                        "navigate|stop_navigation|start_task|pause_task|resume_task|end_task|sing|dance|preset_motion|chat")
                + DoubaoArkClient.buildPresetMotionPrompt()
                + DoubaoArkClient.buildChatReplyPrompt();
    }

    private record SseChunk(String content, boolean done, boolean finished) {
    }

    private static JSONArray buildTextMessages(String string, String string2) {
        JSONArray jSONArray = new JSONArray();
        jSONArray.add(DoubaoArkClient.message("system", string));
        jSONArray.add(DoubaoArkClient.message("user", string2));
        return jSONArray;
    }

    private static JSONObject message(String string, Object object) {
        JSONObject jSONObject = new JSONObject();
        jSONObject.put("role", string);
        jSONObject.put("content", object);
        return jSONObject;
    }

    private static String buildAudioInteractionPrompt(List<String> list) {
        return "识别语音原话，question 保留原文；按系统要求分类并严格输出一个 JSON 对象。";
    }

    private static String buildChatReplyPrompt() {
        return """

                普通问答回复规则：
                - intent=chat 时，reply 必须是机器人最终要说的简短口语回答，不要留空；通常不超过两句话。
                - 普通常识、科学原理、语言、历史、观点和开放问题，使用模型已有知识正常回答；不要因为当前上下文或知识库没有提供常识答案而拒答。
                - 只有用户询问本机机器人的专属状态/能力、具体场馆/企业/地点资料或实时信息时，才必须依靠上下文或知识库依据；这类问题没有依据时明确说目前无法核实，不要猜测。
                - 对模型确实不确定的普通问题，简短说明不确定之处或询问澄清；不要把“当前资料中没有”当成无法回答普通问题的理由。
                - reply 中不要输出 JSON、Markdown 或对分类过程的说明。
                """;
    }

    private String buildAudioSystemPrompt(List<String> list) {
        String string = list == null || list.isEmpty() ? "[]" : JSON.toJSONString(list);
        return ("你是机器人“%s”的中文语音助手。一次完成语音识别、意图判断和回答，不要输出 Markdown。\n"
                + "只输出 JSON：{\"intent\":\"navigate|stop_navigation|start_task|pause_task|resume_task|end_task|sing|dance|preset_motion|chat\","
                + "\"target_name\":\"\",\"question\":\"识别出的用户原话\",\"reply\":\"机器人最终要说的话\"}\n"
                + "导航点：%s。导航时 target_name 必须使用列表原名，reply 为“收到，正在前往<点位>。”。\n"
                + "停止导航、开始讲解、暂停讲解、继续讲解、停止讲解、唱歌或跳舞按对应 intent。暂停讲解必须是 pause_task，继续讲解必须是 resume_task。"
                + "其他问题全部为 chat。reply 用简洁口语中文回答，通常不超过两句话。"
        ).formatted(this.robotDisplayName, string) + DoubaoArkClient.buildChatReplyPrompt();
    }

    private static String buildPresetMotionPrompt() {
        return """

                预置动作祈使口令扩展规则：
                仅支持以下白名单动作：%s。
                只有用户明确要求机器人执行其中一个动作时才使用 intent=preset_motion；question 必须逐字保留用户原话。
                问句、否定、引用/转述、条件表达、讨论动作或名称/区域不匹配时不得使用 preset_motion，按 chat 处理。不要推测或补全动作意图。
                不得把旁人说话、任务讲解文本或直播弹幕当成用户本人的动作指令。动作只允许在导航和讲解任务空闲时提交；回复只可确认请求已受理/提交，不可声称动作已经完成。
                """.formatted(PresetMotionCatalog.promptDescription());
    }

    private static String buildInteractionPrompt(List<String> list) {
        String string = list == null || list.isEmpty() ? "[]" : JSON.toJSONString(list);
        return "\u4f60\u662f\u673a\u5668\u4eba\u8bed\u97f3\u4ea4\u4e92\u610f\u56fe\u89e3\u6790\u5668\u3002\n\u4f60\u7684\u4efb\u52a1\u662f\u628a\u7528\u6237\u8f93\u5165\u5206\u7c7b\u6210\u5bfc\u822a\u3001\u505c\u6b62\u5bfc\u822a\u3001\u5f00\u59cb\u4efb\u52a1\u3001\u7ed3\u675f\u4efb\u52a1\u3001\u5531\u6b4c\u3001\u8df3\u821e\u3001\u666e\u901a\u95ee\u7b54\u4e03\u7c7b\uff0c\u5e76\u4e14\u53ea\u8f93\u51fa\u4e00\u4e2a JSON \u5bf9\u8c61\u3002\n\u4e0d\u8981\u8f93\u51fa Markdown\uff0c\u4e0d\u8981\u89e3\u91ca\uff0c\u4e0d\u8981\u8f93\u51fa\u591a\u4f59\u6587\u672c\u3002\n\n\u53ef\u7528\u5730\u70b9\u540d\u79f0\u5217\u8868\uff1a\n%s\n\n\u53ef\u7528\u821e\u8e48\u540d\u79f0\u5217\u8868\uff1a\n\u5feb\u4e50\u4e4b\u821e\u3001\u7231\u4f60\u3001\u7231\u4f60\u6709\u58f0\u3001\u5929\u732b\u3001\u5f8b\u52a8\u7eaa\u5143\u3001\u592a\u6781\u3001\u6cb3\u5357\u592a\u6781\u3001\u548f\u6625\u62f3\u3001\u5361\u62c9OK\u3002\n\nJSON \u683c\u5f0f\u56fa\u5b9a\u5982\u4e0b\uff1a\n{\"intent\":\"navigate|stop_navigation|start_task|end_task|sing|dance|chat\",\"target_name\":\"\u5bfc\u822a\u5730\u70b9\u540d\u3001\u821e\u8e48\u540d\u6216\u7a7a\u5b57\u7b26\u4e32\",\"question\":\"\u666e\u901a\u95ee\u7b54\u95ee\u9898\u6216\u7a7a\u5b57\u7b26\u4e32\",\"reply\":\"\u53ef\u9009\u77ed\u56de\u590d\"}\n\n\u89c4\u5219\uff1a\n1. \u7528\u6237\u8bf4\u201c\u5e26\u6211\u53bb\u524d\u53f0\u201d\u201c\u53bb\u4f1a\u8bae\u5ba4A\u201d\u201c\u5bfc\u822a\u5230\u5c55\u5385\u5165\u53e3\u201d\uff0cintent=navigate\uff0ctarget_name \u586b\u5730\u70b9\u540d\u3002\n2. \u7528\u6237\u8bf4\u201c\u505c\u4e0b\u201d\u201c\u53d6\u6d88\u5bfc\u822a\u201d\u201c\u522b\u8d70\u4e86\u201d\uff0cintent=stop_navigation\u3002\n3. \u7528\u6237\u8bf4\u201c\u5f00\u59cb\u4efb\u52a1\u201d\u201c\u5f00\u59cb\u8bb2\u89e3\u201d\u201c\u5f00\u59cb\u53c2\u89c2\u4efb\u52a1\u201d\uff0cintent=start_task\u3002\n4. \u7528\u6237\u8bf4\u201c\u7ed3\u675f\u4efb\u52a1\u201d\u201c\u505c\u6b62\u4efb\u52a1\u201d\u201c\u7ed3\u675f\u8bb2\u89e3\u201d\u201c\u7ec8\u6b62\u53c2\u89c2\u4efb\u52a1\u201d\uff0cintent=end_task\u3002\n5. \u7528\u6237\u8bf4\u201c\u5531\u9996\u6b4c\u201d\u201c\u7ed9\u5927\u5bb6\u5531\u6b4c\u201d\u201c\u6765\u4e00\u9996\u6b4c\u201d\uff0cintent=sing\uff0creply \u53ef\u586b\u201c\u5531\u6b4c\u529f\u80fd\u6682\u65f6\u5173\u95ed\u4e86\u201d\u3002\n6. \u7528\u6237\u8bf4\u201c\u8df3\u4e2a\u821e\u201d\u201c\u8868\u6f14\u821e\u8e48\u201d\u201c\u6765\u6bb5\u821e\u8e48\u201d\uff0cintent=dance\uff0creply \u53ef\u586b\u201c\u597d\u7684\uff0c\u6211\u6765\u8df3\u4e00\u6bb5\u201d\u3002\u5982\u679c\u8bf4\u4e86\u5177\u4f53\u821e\u8e48\uff0c\u4f8b\u5982\u201c\u8df3\u592a\u6781\u201d\u201c\u8df3\u7231\u4f60\u201d\u201c\u8df3\u5361\u62c9OK\u201d\uff0ctarget_name \u5fc5\u987b\u586b\u5199\u5bf9\u5e94\u821e\u8e48\u540d\u3002\n7. \u5176\u5b83\u95f2\u804a\u6216\u95ee\u7b54\uff0cintent=chat\uff0cquestion \u586b\u7528\u6237\u539f\u95ee\u9898\u3002\n8. \u5982\u679c\u7528\u6237\u8981\u5bfc\u822a\u4f46\u5730\u70b9\u4e0d\u660e\u786e\uff0cintent=chat\uff0cquestion \u586b\u201c\u7528\u6237\u60f3\u5bfc\u822a\u4f46\u5730\u70b9\u4e0d\u660e\u786e\u201d\uff0creply \u8be2\u95ee\u7528\u6237\u8981\u53bb\u54ea\u91cc\u3002\n9. \u5982\u679c\u5730\u70b9\u6216\u821e\u8e48\u4e0e\u5217\u8868\u4e2d\u7684\u540d\u79f0\u6216\u522b\u540d\u76f8\u8fd1\uff0ctarget_name \u4f7f\u7528\u5217\u8868\u4e2d\u7684\u539f\u59cb\u540d\u79f0\u3002\n".formatted(string);
    }

    private static byte[] pcm16leToWav(byte[] byArray, int n, int n2, int n3) throws IOException {
        byte[] byArray2 = byArray == null ? new byte[]{} : byArray;
        int n4 = n * n2 * n3 / 8;
        int n5 = n2 * n3 / 8;
        int n6 = byArray2.length;
        ByteArrayOutputStream byteArrayOutputStream = new ByteArrayOutputStream(44 + n6);
        byteArrayOutputStream.write("RIFF".getBytes(StandardCharsets.US_ASCII));
        DoubaoArkClient.writeLeInt(byteArrayOutputStream, 36 + n6);
        byteArrayOutputStream.write("WAVE".getBytes(StandardCharsets.US_ASCII));
        byteArrayOutputStream.write("fmt ".getBytes(StandardCharsets.US_ASCII));
        DoubaoArkClient.writeLeInt(byteArrayOutputStream, 16);
        DoubaoArkClient.writeLeShort(byteArrayOutputStream, 1);
        DoubaoArkClient.writeLeShort(byteArrayOutputStream, n2);
        DoubaoArkClient.writeLeInt(byteArrayOutputStream, n);
        DoubaoArkClient.writeLeInt(byteArrayOutputStream, n4);
        DoubaoArkClient.writeLeShort(byteArrayOutputStream, n5);
        DoubaoArkClient.writeLeShort(byteArrayOutputStream, n3);
        byteArrayOutputStream.write("data".getBytes(StandardCharsets.US_ASCII));
        DoubaoArkClient.writeLeInt(byteArrayOutputStream, n6);
        byteArrayOutputStream.write(byArray2);
        return byteArrayOutputStream.toByteArray();
    }

    private static void writeLeInt(ByteArrayOutputStream byteArrayOutputStream, int n) throws IOException {
        byteArrayOutputStream.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(n).array());
    }

    private static void writeLeShort(ByteArrayOutputStream byteArrayOutputStream, int n) throws IOException {
        byteArrayOutputStream.write(ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN).putShort((short)n).array());
    }
}
