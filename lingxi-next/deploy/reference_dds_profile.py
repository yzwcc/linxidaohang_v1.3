"""Derive and check the reference service's dedicated UDPv4-only Fast DDS profile."""

import argparse
import ipaddress
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET


NAMESPACE = "http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles"
MAX_BYTES = 64 * 1024


def require(condition):
    if not condition:
        raise ValueError("unsupported_reference_dds_profile")


def local(tag):
    return tag.rsplit("}", 1)[-1]


def fields(element, *, required, optional=()):
    names = [local(child.tag) for child in element]
    require(len(names) == len(set(names)) and set(required) <= set(names) <= set(required) | set(optional))
    return dict(zip(names, element))


def leaf(element):
    require(not element.attrib and not len(element) and element.text is not None)
    return element.text.strip()


def udp_options(data, *, template):
    require(0 < len(data) <= MAX_BYTES and b"\x00" not in data and not re.search(br"<!\s*(?:DOCTYPE|ENTITY)", data, re.I))
    root = ET.fromstring(data)
    require(root.tag == "dds" and not root.attrib)
    profiles = fields(root, required=("profiles",), optional=("log",) if template else ())["profiles"]
    namespace = "{" + NAMESPACE + "}"
    require(profiles.tag == namespace + "profiles" and all(el.tag.startswith(namespace) for el in profiles.iter()))
    members = fields(profiles, required=("transport_descriptors", "participant"))
    transports, participant = members["transport_descriptors"], members["participant"]
    require(set(participant.attrib) == {"profile_name", "is_default_profile"}
            and participant.attrib["is_default_profile"] == "true"
            and re.fullmatch(r"[\w.-]{1,64}", participant.attrib["profile_name"], re.ASCII))
    rtps = fields(participant, required=("rtps",))["rtps"]
    settings = fields(rtps, required=("useBuiltinTransports", "userTransports"))
    require(leaf(settings["useBuiltinTransports"]) == "false")
    declared = {}
    udp = None
    for transport in transports:
        require(local(transport.tag) == "transport_descriptor" and not transport.attrib)
        nodes = fields(transport, required=("transport_id", "type"),
                       optional=("sendBufferSize", "receiveBufferSize", "interfaceWhiteList",
                                 "segment_size", "port_queue_capacity"))
        identity, kind = leaf(nodes["transport_id"]), leaf(nodes["type"])
        require(re.fullmatch(r"[\w.-]{1,64}", identity, re.ASCII) and identity not in declared)
        require(kind in ({"UDPv4", "SHM"} if template else {"UDPv4"}) and kind not in declared.values())
        declared[identity] = kind
        if kind == "UDPv4":
            require(set(nodes) <= {"transport_id", "type", "sendBufferSize", "receiveBufferSize", "interfaceWhiteList"})
            udp = {"transport_id": identity}
            for key in ("sendBufferSize", "receiveBufferSize"):
                if key in nodes:
                    value = leaf(nodes[key])
                    require(re.fullmatch(r"[0-9]{1,10}", value) and int(value) <= 2**32 - 1)
                    udp[key] = value
            if "interfaceWhiteList" in nodes:
                addresses = nodes["interfaceWhiteList"]
                require(not addresses.attrib and len(addresses) > 0)
                require(all(local(address.tag) == "address" for address in addresses))
                udp["addresses"] = [str(ipaddress.IPv4Address(leaf(address))) for address in addresses]
        else:
            require(set(nodes) <= {"transport_id", "type", "segment_size", "port_queue_capacity"})
    selected = settings["userTransports"]
    require(udp is not None and all(local(child.tag) == "transport_id" for child in selected))
    identifiers = [leaf(child) for child in selected]
    require(len(identifiers) == len(declared) and set(identifiers) == set(declared))
    # Generic or non-default XML properties could change what Fast DDS actually uses.
    require(all(not el.attrib for el in profiles.iter() if el is not participant))
    return udp


def derive(data):
    udp = udp_options(data, template=True)
    root = ET.Element("dds")
    profiles = ET.SubElement(root, "profiles", xmlns=NAMESPACE)
    descriptor = ET.SubElement(ET.SubElement(profiles, "transport_descriptors"), "transport_descriptor")
    for key in ("transport_id", "type", "sendBufferSize", "receiveBufferSize"):
        if key == "type" or key in udp:
            ET.SubElement(descriptor, key).text = "UDPv4" if key == "type" else udp[key]
    if "addresses" in udp:
        whitelist = ET.SubElement(descriptor, "interfaceWhiteList")
        for address in udp["addresses"]:
            ET.SubElement(whitelist, "address").text = address
    participant = ET.SubElement(profiles, "participant", profile_name="lingxi_reference_udp4", is_default_profile="true")
    rtps = ET.SubElement(participant, "rtps")
    ET.SubElement(rtps, "useBuiltinTransports").text = "false"
    ET.SubElement(ET.SubElement(rtps, "userTransports"), "transport_id").text = udp["transport_id"]
    ET.indent(root, space="  ")
    result = ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"
    udp_options(result, template=False)
    return result


def read_profile(path, *, installed=False):
    require(path.is_absolute())
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_BYTES)
        require(not installed or (info.st_uid == 0 and not info.st_mode & 0o022))
        return stream.read(MAX_BYTES + 1)


def check(data):
    udp_options(data, template=False)
    # Only start Fast DDS with the exact XML this tool generates, not another
    # parser's potentially different interpretation of a hand-edited file.
    require(data == derive(data))


def main():
    import grp

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", type=Path, help="Validate the installed product-only profile")
    mode.add_argument("--check-env", type=Path, help="Validate the profile selected by robot.env before switching services")
    mode.add_argument("--source", type=Path, help="Read the target's existing Fast DDS profile as a template")
    parser.add_argument("--output", type=Path, help="New file in a root-owned private directory")
    args = parser.parse_args()
    if args.check is not None or args.check_env is not None:
        if args.output is not None:
            parser.error("check modes do not accept --output")
        selected = args.check
        if args.check_env is not None:
            records = args.check_env.read_text(encoding="utf-8").splitlines()
            paths = [line.partition("=")[2] for line in records if line.startswith("LINGXI_REFERENCE_DDS_PROFILE=")]
            require(len(paths) == 1 and re.fullmatch(r"/[A-Za-z0-9_./-]+", paths[0]))
            selected = Path(paths[0])
        check(read_profile(selected, installed=True))
        return
    if args.output is None or not args.output.is_absolute() or os.geteuid() != 0:
        parser.error("--source requires root and an absolute --output path")
    parent = args.output.parent
    info = parent.stat()
    require(parent.resolve(strict=True) == parent and stat.S_ISDIR(info.st_mode)
            and info.st_uid == 0 and not info.st_mode & 0o027)
    data = derive(read_profile(args.source))
    gid = grp.getgrnam("lingxi-next").gr_gid
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchown(stream.fileno(), 0, gid)
        os.fchmod(stream.fileno(), 0o640)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    print("Created a separate, UDPv4-only reference profile; existing profiles unchanged.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, ET.ParseError):
        raise SystemExit("Reference DDS profile rejected. Use a new private product path and a supported target profile.") from None
