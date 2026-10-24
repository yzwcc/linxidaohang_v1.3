import { AsyncCache } from "./asyncCache.js";
import { requestBuffer, requestJson } from "./http.js";
export function createRobotClient(config) {
    const bridgeBase = `http://${config.robot.host}:${config.robot.bridgePort}`;
    const webBase = `http://${config.robot.host}:${config.robot.webPort}`;
    const gatewayBase = robotGatewayBaseUrl(config);
    const mapRootQuery = `map_root=${encodeURIComponent(config.robot.mapRoot)}`;
    const mapCaches = new Map();
    const imageCaches = new Map();
    const mapsCache = new AsyncCache(() => requestJson(`${webBase}/api/discover_maps?${mapRootQuery}`, { timeoutMs: 2500 }), 15_000, 5 * 60_000);
    const statusCache = new AsyncCache(async () => {
        const [navStatus, points, maps, gatewayHealth] = await Promise.allSettled([
            requestJson(`${bridgeBase}/nav/status`, { timeoutMs: 1800 }),
            requestJson(`${bridgeBase}/points`, { timeoutMs: 1800 }),
            mapsCache.get(),
            requestJson(`${gatewayBase}/gateway/health`, { timeoutMs: 1800 })
        ]);
        const gatewayHealthValue = gatewayHealth.status === "fulfilled" ? gatewayHealth.value : null;
        const gatewayServices = gatewayHealthValue?.services;
        return {
            robot_id: config.robot.id,
            bridge_url: bridgeBase,
            map_web_url: webBase,
            bridge_online: navStatus.status === "fulfilled",
            map_web_online: maps.status === "fulfilled",
            voice_gateway_online: gatewayHealth.status === "fulfilled" && gatewayServices?.voice?.ok === true,
            navigation_gateway_online: gatewayHealth.status === "fulfilled" && gatewayServices?.navigation?.ok === true,
            nav_status: navStatus.status === "fulfilled" ? navStatus.value : null,
            points: points.status === "fulfilled" ? points.value : null,
            maps: maps.status === "fulfilled" ? maps.value : null,
            gateway_health: gatewayHealthValue,
            errors: {
                nav_status: navStatus.status === "rejected" ? errorMessage(navStatus.reason) : null,
                points: points.status === "rejected" ? errorMessage(points.reason) : null,
                maps: maps.status === "rejected" ? errorMessage(maps.reason) : null,
                gateway_health: gatewayHealth.status === "rejected" ? errorMessage(gatewayHealth.reason) : null
            }
        };
    }, 2_000, 15_000);
    const linkcraftResourcesCache = new AsyncCache(() => requestJson(`${bridgeBase}/linkcraft/resources`, { timeoutMs: 5000 }), 10_000, 2 * 60_000);
    function mapCache(mapName) {
        let cache = mapCaches.get(mapName);
        if (!cache) {
            cache = new AsyncCache(() => requestJson(`${webBase}/api/map?${mapRootQuery}&map_name=${encodeURIComponent(mapName)}`, { timeoutMs: 5000 }), 10_000, 2 * 60_000);
            mapCaches.set(mapName, cache);
        }
        return cache;
    }
    function imageCache(mapName) {
        let cache = imageCaches.get(mapName);
        if (!cache) {
            cache = new AsyncCache(() => requestBuffer(`${webBase}/api/map_image?${mapRootQuery}&map_name=${encodeURIComponent(mapName)}`, 10_000), 60_000, 10 * 60_000);
            imageCaches.set(mapName, cache);
        }
        return cache;
    }
    function invalidateMap(mapName) {
        mapsCache.invalidate();
        statusCache.invalidate();
        if (mapName) {
            mapCaches.get(mapName)?.invalidate();
            imageCaches.get(mapName)?.invalidate();
        }
    }
    async function mutation(request, mapName) {
        const value = await request();
        statusCache.invalidate();
        if (mapName)
            invalidateMap(mapName);
        return value;
    }
    return {
        invalidateStatus() {
            statusCache.invalidate();
        },
        status(force = false) {
            return statusCache.get(force);
        },
        navStart(targetName) {
            return mutation(() => requestJson(`${bridgeBase}/nav/start`, {
                method: "POST",
                body: JSON.stringify({ event_id: `mp_${Date.now()}`, target_name: targetName })
            }));
        },
        navStop() {
            return mutation(() => requestJson(`${bridgeBase}/nav/stop`, {
                method: "POST",
                body: JSON.stringify({ event_id: `mp_stop_${Date.now()}` })
            }));
        },
        taskStart() {
            return mutation(() => requestJson(`${bridgeBase}/task/start`, {
                method: "POST",
                body: JSON.stringify({ event_id: `mp_task_start_${Date.now()}` })
            }));
        },
        taskPause() {
            return mutation(() => requestJson(`${bridgeBase}/task/pause`, {
                method: "POST",
                body: JSON.stringify({ event_id: `mp_task_pause_${Date.now()}` })
            }));
        },
        taskResume() {
            return mutation(() => requestJson(`${bridgeBase}/task/resume`, {
                method: "POST",
                body: JSON.stringify({ event_id: `mp_task_resume_${Date.now()}` })
            }));
        },
        taskStop() {
            return mutation(() => requestJson(`${bridgeBase}/task/stop`, {
                method: "POST",
                body: JSON.stringify({ event_id: `mp_task_stop_${Date.now()}` })
            }));
        },
        speak(text, traceId) {
            return requestJson(`${bridgeBase}/tts/speak`, {
                method: "POST",
                body: JSON.stringify({
                    text,
                    trace_id: traceId || `web_tts_${Date.now()}`,
                    interrupt: false,
                    force: true
                })
            });
        },
        setActiveMap(mapName) {
            return mutation(() => requestJson(`${bridgeBase}/nav/map`, {
                method: "POST",
                body: JSON.stringify({ active_map_name: mapName })
            }), mapName);
        },
        reloadNavigation() {
            return mutation(() => requestJson(`${bridgeBase}/nav/reload`, {
                method: "POST",
                body: JSON.stringify({})
            }));
        },
        currentLocalization() {
            return requestJson(`${bridgeBase}/localization/current`, { timeoutMs: 2500 });
        },
        maps(force = false) {
            return mapsCache.get(force);
        },
        linkcraftResources(force = false) {
            return linkcraftResourcesCache.get(force);
        },
        map(mapName, force = false) {
            return mapCache(mapName).get(force);
        },
        mapImage(mapName, force = false) {
            return imageCache(mapName).get(force);
        },
        savePoint(point, mapName) {
            return mutation(() => requestJson(`${webBase}/api/points`, {
                method: "POST",
                body: JSON.stringify(point)
            }), mapName);
        },
        deletePoint(pointId, mapName) {
            return mutation(() => requestJson(`${webBase}/api/points/${encodeURIComponent(pointId)}`, {
                method: "DELETE"
            }), mapName);
        },
        saveTask(task, mapName) {
            return mutation(() => requestJson(`${webBase}/api/tour-task`, {
                method: "POST",
                body: JSON.stringify(task)
            }), mapName);
        },
        pathSafety() {
            return requestJson(`${webBase}/api/settings`, { timeoutMs: 5000 });
        },
        savePathSafety(pathSafety) {
            return mutation(() => requestJson(`${webBase}/api/settings`, {
                method: "POST",
                body: JSON.stringify({ path_safety: pathSafety }),
                timeoutMs: 5000
            }));
        },
        relocalizationStatus() {
            return requestJson(`${webBase}/api/relocalization/status`, { timeoutMs: 2500 });
        },
        startRelocalization(payload) {
            return requestJson(`${webBase}/api/relocalization/start`, {
                method: "POST",
                body: JSON.stringify(payload),
                timeoutMs: 5000
            });
        }
    };
}
export function robotGatewayBaseUrl(config) {
    return `http://${config.robot.host}:${config.robot.robotJavaAgentBridgePort}`;
}
function errorMessage(error) {
    return error instanceof Error ? error.message : String(error);
}
