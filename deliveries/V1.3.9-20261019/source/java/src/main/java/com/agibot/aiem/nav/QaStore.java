/*
 * Decompiled with CFR 0.152.
 */
package com.agibot.aiem.nav;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.attribute.FileAttribute;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;

final class QaStore {
    private final Path path;
    private final Map<String, String> data = new LinkedHashMap<String, String>();

    QaStore(String string) {
        this.path = Path.of(string, new String[0]);
        this.load();
    }

    Optional<String> getAnswer(String string) {
        String string2 = QaStore.normalize(string);
        if (string2.isEmpty()) {
            return Optional.empty();
        }
        String string3 = this.data.get(string2);
        if (string3 != null && !string3.isBlank()) {
            return Optional.of(string3);
        }
        for (Map.Entry<String, String> entry : this.data.entrySet()) {
            String string4;
            String string5 = entry.getKey();
            if (string5.isBlank() || !string2.contains(string5) && !string5.contains(string2) || (string4 = entry.getValue()) == null || string4.isBlank()) continue;
            return Optional.of(string4);
        }
        return Optional.empty();
    }

    void savePair(String string, String string2) {
        String string3;
        String string4 = QaStore.normalize(string);
        String string5 = string3 = string2 == null ? "" : string2.trim();
        if (string4.isEmpty() || string3.isEmpty()) {
            return;
        }
        this.data.put(string4, string3);
        this.save();
    }

    private void load() {
        if (!Files.exists(this.path, new LinkOption[0])) {
            return;
        }
        try {
            String string = Files.readString(this.path, StandardCharsets.UTF_8).trim();
            if (string.isEmpty()) {
                return;
            }
            JSONObject jSONObject = JSON.parseObject(string);
            for (String string2 : jSONObject.keySet()) {
                this.data.put(string2, jSONObject.getString(string2));
            }
        }
        catch (Exception exception) {
            this.data.clear();
        }
    }

    private void save() {
        try {
            Path path = this.path.getParent();
            if (path != null) {
                Files.createDirectories(path, new FileAttribute[0]);
            }
            Files.writeString(this.path, (CharSequence)JSON.toJSONString(this.data), StandardCharsets.UTF_8, new OpenOption[0]);
        }
        catch (IOException iOException) {
            // empty catch block
        }
    }

    private static String normalize(String string) {
        return string == null ? "" : string.trim();
    }
}
