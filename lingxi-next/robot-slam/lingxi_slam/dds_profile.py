"""Select the local DDS file without changing the verified transport contract."""
import os
from pathlib import Path
import stat

VENDOR_PROFILE = "/agibot/software/entry/cfg/ros_dds_configuration.xml"


def selected_profile(default=VENDOR_PROFILE):
    value = os.environ.get("LINGXI_DDS_PROFILE")
    if value is None:
        return Path(default)
    path = Path(value)
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > 1024**2
            or path.stat().st_mode & 0o022):
        raise ValueError("direct_readonly_installed_dds_profile_required")
    return path
