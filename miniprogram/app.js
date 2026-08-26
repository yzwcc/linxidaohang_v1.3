App({
  globalData: {
    apiBaseUrl: "http://127.0.0.1:3000",
    token: "",
    robotId: "default"
  },
  onLaunch() {
    const token = wx.getStorageSync("token");
    if (token) {
      this.globalData.token = token;
    }
  }
});
