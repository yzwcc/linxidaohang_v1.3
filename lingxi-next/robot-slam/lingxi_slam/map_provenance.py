"""Byte-level sensor/configuration provenance, not geometric calibration evidence."""
import re


def sensor_provenance(value):
    if (type(value) is not dict or set(value) != {
            "schemaVersion", "profileSha256", "timingHypothesis", "configurationSha256"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or value["timingHypothesis"] not in ("header_is_scan_start", "header_is_scan_end")
            or type(value["profileSha256"]) is not str
            or re.fullmatch("[a-f0-9]{64}", value["profileSha256"]) is None):
        raise ValueError("exact_sensor_provenance_required")
    files = value["configurationSha256"]
    if (type(files) is not dict or not 1 <= len(files) <= 32
            or any(type(name) is not str or re.fullmatch("[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name) is None
                   or type(digest) is not str or re.fullmatch("[a-f0-9]{64}", digest) is None
                   for name, digest in files.items())):
        raise ValueError("bounded_configuration_provenance_required")
    return {**value, "configurationSha256": dict(sorted(files.items()))}
