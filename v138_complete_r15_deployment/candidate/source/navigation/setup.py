from setuptools import find_packages, setup


package_name = "robot_local_navigation_v3"


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (
            "share/" + package_name + "/scripts",
            [
                "scripts/robot_local_navigation_v3.sh",
                "scripts/start_lingxi_nav_bridge.sh",
            ],
        ),
    ],
    install_requires=["setuptools", "PyYAML"],
    zip_safe=True,
    maintainer="Trae",
    maintainer_email="devnull@example.com",
    description="Self-contained Lingxi HTTP bridge for AimDK local navigation.",
    license="Proprietary",
    entry_points={
        "console_scripts": [
            "lingxi_nav_bridge = robot_local_navigation_v3.lingxi_nav_bridge:main",
            "map_label_server_v3 = robot_local_navigation_v3.map_label_server_v3:main",
            "relocalize_once = robot_local_navigation_v3.relocalize_once:main",
        ],
    },
)
