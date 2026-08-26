const app = getApp();

function baseUrl() {
  return app.globalData.apiBaseUrl.replace(/\/$/, "");
}

function token() {
  return app.globalData.token || wx.getStorageSync("token") || "";
}

function request({ url, method = "GET", data, responseType }) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${baseUrl()}${url}`,
      method,
      data,
      responseType,
      header: {
        Authorization: token() ? `Bearer ${token()}` : "",
        "content-type": "application/json"
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
          return;
        }
        const message = res.data && res.data.message ? res.data.message : `请求失败 ${res.statusCode}`;
        reject(new Error(message));
      },
      fail(err) {
        reject(new Error(err.errMsg || "网络不可用"));
      }
    });
  });
}

function login() {
  return new Promise((resolve, reject) => {
    wx.login({
      success(result) {
        request({
          url: "/api/auth/login",
          method: "POST",
          data: { code: result.code }
        }).then((payload) => {
          app.globalData.token = payload.token;
          wx.setStorageSync("token", payload.token);
          resolve(payload.token);
        }).catch(reject);
      },
      fail(err) {
        reject(new Error(err.errMsg || "微信登录失败"));
      }
    });
  });
}

function imageUrl(mapName) {
  return `${baseUrl()}/api/robots/default/maps/${encodeURIComponent(mapName)}/image?access_token=${encodeURIComponent(token())}`;
}

module.exports = {
  login,
  request,
  imageUrl,
  status: () => request({ url: "/api/robots/default/status" }),
  navStart: (targetName) => request({ url: "/api/robots/default/nav/start", method: "POST", data: { target_name: targetName } }),
  navStop: () => request({ url: "/api/robots/default/nav/stop", method: "POST" }),
  taskStart: () => request({ url: "/api/robots/default/task/start", method: "POST" }),
  taskPause: () => request({ url: "/api/robots/default/task/pause", method: "POST" }),
  taskResume: () => request({ url: "/api/robots/default/task/resume", method: "POST" }),
  taskStop: () => request({ url: "/api/robots/default/task/stop", method: "POST" }),
  bridgeAction: (action) => request({ url: `/api/robots/default/bridge/${action}`, method: "POST" }),
  relocalizationStatus: () => request({ url: "/api/robots/default/relocalization/status" }),
  autoRelocalize: () => request({ url: "/api/robots/default/relocalization/auto", method: "POST", data: { force_stop: true } }),
  maps: () => request({ url: "/api/robots/default/maps" }),
  map: (mapName) => request({ url: `/api/robots/default/maps/${encodeURIComponent(mapName)}` }),
  savePoint: (mapName, point) => request({ url: `/api/robots/default/maps/${encodeURIComponent(mapName)}/points`, method: "POST", data: point }),
  deletePoint: (mapName, pointId) => request({ url: `/api/robots/default/maps/${encodeURIComponent(mapName)}/points/${encodeURIComponent(pointId)}`, method: "DELETE" }),
  saveTask: (mapName, task) => request({ url: `/api/robots/default/maps/${encodeURIComponent(mapName)}/task`, method: "POST", data: task })
};
