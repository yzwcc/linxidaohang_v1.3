/*
 * Decompiled with CFR 0.152.
 */
package com.agibot.aiem.nav;

import com.agibot.aiem.nav.AppConfig;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONException;
import com.alibaba.fastjson2.JSONObject;
import io.netty.bootstrap.ServerBootstrap;
import io.netty.channel.Channel;
import io.netty.channel.ChannelFuture;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.ChannelInitializer;
import io.netty.channel.ChannelOption;
import io.netty.channel.EventLoopGroup;
import io.netty.channel.SimpleChannelInboundHandler;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.SocketChannel;
import io.netty.channel.socket.nio.NioServerSocketChannel;
import io.netty.handler.codec.http.HttpObjectAggregator;
import io.netty.handler.codec.http.HttpServerCodec;
import io.netty.handler.codec.http.websocketx.TextWebSocketFrame;
import io.netty.handler.codec.http.websocketx.WebSocketServerProtocolHandler;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

final class LiveDanmuServer
implements AutoCloseable {
    private static final Logger log = LoggerFactory.getLogger(LiveDanmuServer.class);
    private final AppConfig config;
    private final LiveDanmuHandler handler;
    private final Set<String> seenMessageIds = ConcurrentHashMap.newKeySet();
    private final Object rateLimitLock = new Object();
    private final ThreadPoolExecutor workers;
    private EventLoopGroup bossGroup;
    private EventLoopGroup workerGroup;
    private Channel serverChannel;
    private long lastAcceptedAtMillis;

    LiveDanmuServer(AppConfig appConfig, LiveDanmuHandler liveDanmuHandler) {
        this.config = appConfig;
        this.handler = liveDanmuHandler;
        this.workers = new ThreadPoolExecutor(1, 1, 0L, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<Runnable>(appConfig.liveMaxQueueSize), runnable -> {
            Thread thread = new Thread(runnable, "live-danmu-worker");
            thread.setDaemon(true);
            return thread;
        }, new ThreadPoolExecutor.AbortPolicy());
    }

    void start() throws InterruptedException {
        if (!"websocket".equals(this.config.liveInputMode)) {
            throw new IllegalArgumentException("Unsupported live.inputMode=" + this.config.liveInputMode);
        }
        this.bossGroup = new NioEventLoopGroup(1);
        this.workerGroup = new NioEventLoopGroup(1);
        ServerBootstrap serverBootstrap = ((ServerBootstrap)((ServerBootstrap)new ServerBootstrap().group(this.bossGroup, this.workerGroup).channel(NioServerSocketChannel.class)).option(ChannelOption.SO_BACKLOG, 32)).childHandler(new ChannelInitializer<SocketChannel>(){

            @Override
            protected void initChannel(SocketChannel socketChannel) {
                socketChannel.pipeline().addLast(new HttpServerCodec()).addLast(new HttpObjectAggregator(65536)).addLast(new WebSocketServerProtocolHandler("/", null, true)).addLast(new DanmuFrameHandler());
            }
        });
        ChannelFuture channelFuture = serverBootstrap.bind(this.config.liveHost, this.config.livePort).sync();
        this.serverChannel = channelFuture.channel();
        log.info("Live danmu WebSocket server started: ws://{}:{}/", (Object)this.config.liveHost, (Object)this.config.livePort);
    }

    @Override
    public void close() {
        if (this.serverChannel != null) {
            this.serverChannel.close();
        }
        this.workers.shutdownNow();
        if (this.workerGroup != null) {
            this.workerGroup.shutdownGracefully();
        }
        if (this.bossGroup != null) {
            this.bossGroup.shutdownGracefully();
        }
    }

    private IncomingDanmu parse(String string) {
        String string2;
        JSONObject jSONObject;
        try {
            jSONObject = JSON.parseObject(string);
        }
        catch (JSONException jSONException) {
            throw new IllegalArgumentException("invalid_json", jSONException);
        }
        String string3 = LiveDanmuServer.trim(jSONObject.getString("text"));
        if (string3.isBlank()) {
            throw new IllegalArgumentException("empty_text");
        }
        if (string3.length() > this.config.liveMaxTextLength) {
            string3 = string3.substring(0, this.config.liveMaxTextLength);
        }
        String string4 = "live_" + LiveDanmuServer.sanitizeEventPart((string2 = LiveDanmuServer.trim(jSONObject.getString("msgId"))).isBlank() ? UUID.randomUUID().toString() : string2);
        return new IncomingDanmu(string4, LiveDanmuServer.trim(jSONObject.getString("platform")), LiveDanmuServer.trim(jSONObject.getString("roomId")), string2, LiveDanmuServer.trim(jSONObject.getString("userId")), LiveDanmuServer.trim(jSONObject.getString("nickname")), string3, jSONObject.getLongValue("timestamp"));
    }

    /*
     * WARNING - Removed try catching itself - possible behaviour change.
     */
    private boolean shouldAccept(IncomingDanmu incomingDanmu) {
        if (!incomingDanmu.msgId.isBlank() && !this.seenMessageIds.add(incomingDanmu.msgId)) {
            log.info("Live danmu ignored: reason=duplicate_msg_id, msgId={}, text={}", (Object)incomingDanmu.msgId, (Object)LiveDanmuServer.abbreviate(incomingDanmu.text));
            return false;
        }
        Object object = this.rateLimitLock;
        synchronized (object) {
            long l = System.currentTimeMillis();
            if (l - this.lastAcceptedAtMillis < this.config.liveMinIntervalMs) {
                log.info("Live danmu ignored: reason=rate_limited, msgId={}, text={}", (Object)incomingDanmu.msgId, (Object)LiveDanmuServer.abbreviate(incomingDanmu.text));
                return false;
            }
            this.lastAcceptedAtMillis = l;
            return true;
        }
    }

    private void submit(IncomingDanmu incomingDanmu) {
        try {
            this.workers.execute(() -> {
                try {
                    this.handler.onDanmu(incomingDanmu);
                }
                catch (Exception exception) {
                    log.warn("Live danmu handling failed: eventId={}, msgId={}, error={}", incomingDanmu.eventId, incomingDanmu.msgId, exception.getMessage(), exception);
                }
            });
        }
        catch (RejectedExecutionException rejectedExecutionException) {
            log.warn("Live danmu ignored: reason=queue_full, msgId={}, text={}", (Object)incomingDanmu.msgId, (Object)LiveDanmuServer.abbreviate(incomingDanmu.text));
        }
    }

    private static String trim(String string) {
        return string == null ? "" : string.trim();
    }

    private static String sanitizeEventPart(String string) {
        String string2 = string.replaceAll("[^A-Za-z0-9_\\-]", "_");
        return string2.isBlank() ? UUID.randomUUID().toString().replace("-", "") : string2;
    }

    private static String abbreviate(String string) {
        String string2 = string == null ? "" : string.replaceAll("\\s+", " ").trim();
        return string2.length() <= 60 ? string2 : string2.substring(0, 60) + "...";
    }

    @FunctionalInterface
    static interface LiveDanmuHandler {
        public void onDanmu(IncomingDanmu var1) throws Exception;
    }

    record IncomingDanmu(String eventId, String platform, String roomId, String msgId, String userId, String nickname, String text, long timestamp) {
    }

    private final class DanmuFrameHandler
    extends SimpleChannelInboundHandler<TextWebSocketFrame> {
        private DanmuFrameHandler() {
        }

        @Override
        protected void channelRead0(ChannelHandlerContext channelHandlerContext, TextWebSocketFrame textWebSocketFrame) {
            try {
                IncomingDanmu incomingDanmu = LiveDanmuServer.this.parse(textWebSocketFrame.text());
                if (!LiveDanmuServer.this.shouldAccept(incomingDanmu)) {
                    channelHandlerContext.writeAndFlush(new TextWebSocketFrame("{\"ok\":true,\"status\":\"ignored\"}"));
                    return;
                }
                LiveDanmuServer.this.submit(incomingDanmu);
                channelHandlerContext.writeAndFlush(new TextWebSocketFrame("{\"ok\":true,\"status\":\"accepted\",\"eventId\":\"" + incomingDanmu.eventId + "\"}"));
            }
            catch (Exception exception) {
                log.info("Live danmu ignored: reason={}, raw={}", (Object)exception.getMessage(), (Object)LiveDanmuServer.abbreviate(textWebSocketFrame.text()));
                channelHandlerContext.writeAndFlush(new TextWebSocketFrame("{\"ok\":false,\"status\":\"ignored\",\"reason\":\"" + exception.getMessage() + "\"}"));
            }
        }
    }
}
