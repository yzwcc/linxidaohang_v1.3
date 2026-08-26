type LooseObject = Record<string, any>;

export type StartupSnapshotInput = {
  deploymentMode: "cloud" | "robot";
  connectionMode: "direct" | "tunnel";
  proxyHost: string;
  targetHost: string;
  robot: LooseObject | null;
  relocalization: LooseObject | null;
  javaAgent: LooseObject | null;
  javaAgentReachable: boolean;
  javaAgentError?: string;
  targetIdentity: LooseObject | null;
};

export function buildStartupSnapshot(input: StartupSnapshotInput) {
  const { robot, relocalization, javaAgent, targetIdentity } = input;
  const tunnel = {
    ssh: input.javaAgentReachable
      ? { ok: true, detail: "机器人运维 SSH 可用" }
      : { ok: false, detail: "机器人运维 SSH 不可用", error: input.javaAgentError || "状态读取失败" },
    bridge: robot?.bridge_online
      ? { ok: true, detail: "导航桥接已响应" }
      : { ok: false, detail: "导航桥接未响应", error: robot?.errors?.nav_status || "服务未连接" },
    mapWeb: robot?.map_web_online
      ? { ok: true, detail: "地图服务已响应" }
      : { ok: false, detail: "地图服务未响应", error: robot?.errors?.maps || "服务未连接" },
    targetIdentity,
    proxyHost: input.proxyHost,
    targetHost: input.targetHost
  };
  const navStatus = robot?.nav_status || {};
  const hasLiveLocalization = typeof navStatus.localization?.ok === "boolean";
  const localizationOk = hasLiveLocalization
    ? Boolean(navStatus.localization.ok)
    : relocalization?.result?.status === "success";
  const localizationDetail = hasLiveLocalization
    ? (navStatus.localization.message || (localizationOk ? "当前定位数据正常" : "暂未收到当前定位数据"))
    : (relocalization?.result?.message || "正式导航前建议完成重定位");

  const checklist = [
    {
      key: "network_tunnel",
      label: input.deploymentMode === "robot"
        ? "机器人本机服务"
        : (input.connectionMode === "tunnel" ? "公网运维通道" : "机器人网络"),
      ok: Boolean(
        (input.connectionMode !== "tunnel" || targetIdentity?.ok)
        && tunnel.ssh.ok
        && (tunnel.bridge.ok || robot?.bridge_online)
        && (tunnel.mapWeb.ok || robot?.map_web_online)
      ),
      detail: [
        input.deploymentMode === "robot"
          ? "控制台与机器人服务均运行在本机"
          : input.connectionMode === "tunnel"
            ? `目标机器人 ${input.targetHost || "未填写"}，云端代理 ${input.proxyHost}`
            : `直连机器人 ${input.proxyHost}`,
        ...(input.connectionMode === "tunnel"
          ? [`目标核验：${targetIdentity?.ok ? "一致" : targetIdentity?.detail || "未完成"}`]
          : []),
        `SSH：${tunnel.ssh.ok ? "正常" : tunnel.ssh.error || tunnel.ssh.detail}`,
        `导航：${tunnel.bridge.ok || robot?.bridge_online ? "正常" : tunnel.bridge.error || tunnel.bridge.detail}`,
        `地图：${tunnel.mapWeb.ok || robot?.map_web_online ? "正常" : tunnel.mapWeb.error || tunnel.mapWeb.detail}`
      ].join("；")
    },
    {
      key: "java_agent",
      label: "语音服务模块",
      ok: Boolean(javaAgent?.running && javaAgent?.navBaseUrlMatchesRobot),
      detail: javaAgent?.running
        ? (javaAgent.navBaseUrlMatchesRobot ? "语音服务已运行，并已指向当前机器人" : `语音服务已运行，但桥接地址应为 ${javaAgent.expectedNavBaseUrl}`)
        : "需要启动语音服务"
    },
    {
      key: "lingxi_platform",
      label: "灵心平台连接",
      ok: Boolean(javaAgent?.running && javaAgent?.lingxiConnected && javaAgent?.robotOnline),
      detail: javaAgent?.running
        ? (!javaAgent?.lingxiConnected
            ? (javaAgent?.lastAuthState || "正在等待灵心平台认证成功")
            : (javaAgent?.robotOnline ? "灵心鉴权成功，机器人语音回调已在线" : "灵心已鉴权，正在等待机器人语音回调上线"))
        : "请先启动语音服务"
    },
    {
      key: "robot_voice_bridge",
      label: "机器人本机语音服务",
      ok: Boolean(robot?.voice_gateway_online),
      detail: robot?.voice_gateway_online ? "机器人本机语音桥接已在线" : "机器人本机语音桥接尚未连接"
    },
    {
      key: "robot_bridge",
      label: "机器人导航模块",
      ok: Boolean(robot?.bridge_online && robot?.map_web_online),
      detail: robot?.bridge_online && robot?.map_web_online ? "导航服务和地图服务已在线" : "需要启动机器人导航模块"
    },
    {
      key: "map_points",
      label: "地图与导航点",
      ok: Boolean(navStatus.active_map_name && robot?.points?.point_names?.length),
      detail: navStatus.active_map_name ? `当前地图 ${navStatus.active_map_name}` : "需要确认当前地图和导航点"
    },
    { key: "relocalization", label: "现场重定位", ok: localizationOk, detail: localizationDetail }
  ];

  return {
    ok: true,
    ready: checklist.filter((item) => item.key !== "relocalization").every((item) => item.ok),
    navigation_ready: checklist.every((item) => item.ok),
    checklist,
    robot,
    relocalization,
    java_agent: javaAgent,
    tunnel
  };
}
