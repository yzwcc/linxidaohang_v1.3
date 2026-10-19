#!/usr/bin/env python3
"""v3 灵犀导航桥接的兼容启动入口。

旧脚本仍然执行 python_bridge/lingxi_nav_bridge.py；这里保持这个路径可用，
实际逻辑统一转到 robot_local_navigation_v3 包内实现。
"""

from robot_local_navigation_v3.lingxi_nav_bridge import main


if __name__ == "__main__":
    main()
