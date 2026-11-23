from pathlib import Path
import importlib.util
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("reference_dds_profile", ROOT / "deploy/reference_dds_profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)

TEMPLATE = b'''<dds>
  <profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
    <transport_descriptors>
      <transport_descriptor>
        <transport_id>target_udp</transport_id><type>UDPv4</type>
        <sendBufferSize>10388608</sendBufferSize><receiveBufferSize>10388608</receiveBufferSize>
        <interfaceWhiteList><address>192.0.2.4</address></interfaceWhiteList>
      </transport_descriptor>
      <transport_descriptor>
        <transport_id>target_shm</transport_id><type>SHM</type>
        <segment_size>268435456</segment_size><port_queue_capacity>1024</port_queue_capacity>
      </transport_descriptor>
    </transport_descriptors>
    <participant profile_name="firmware_default" is_default_profile="true">
      <rtps>
        <useBuiltinTransports>false</useBuiltinTransports>
        <userTransports><transport_id>target_shm</transport_id><transport_id>target_udp</transport_id></userTransports>
      </rtps>
    </participant>
  </profiles>
  <log><use_default>FALSE</use_default></log>
</dds>'''


class ReferenceDdsProfileTest(unittest.TestCase):
    def test_isolates_shared_memory_and_preserves_udp_settings(self):
        output = profile.derive(TEMPLATE)
        self.assertEqual(profile.udp_options(output, template=False), {
            "transport_id": "target_udp", "addresses": ["192.0.2.4"],
            "sendBufferSize": "10388608", "receiveBufferSize": "10388608",
        })
        self.assertNotIn(b"target_shm", output)
        self.assertNotIn(b"<log>", output)
        self.assertEqual(profile.derive(output), output)
        profile.check(output)
        with self.assertRaises(ValueError):
            profile.check(output.replace(b"<type>UDPv4</type>", b"<type>UDPv4</type> unexpected"))

    def test_check_rejects_existing_shm_without_vendor_log(self):
        with self.assertRaises(ValueError):
            profile.udp_options(TEMPLATE.replace(b"<log><use_default>FALSE</use_default></log>", b""), template=False)

    def test_rejects_fallback_and_non_default_participant(self):
        for source, replacement in [(b">false<", b">true<"), (b'default_profile="true"', b'default_profile="false"'),
                                    (b"</rtps>", b"<propertiesPolicy/></rtps>"),
                                    (b"</profiles>", b"<subscriber profile_name='other'/></profiles>"),
                                    (b"192.0.2.4", b"0.0.1.999"),
                                    (b"</userTransports>", b"<transport_id>missing</transport_id></userTransports>")]:
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                profile.derive(TEMPLATE.replace(source, replacement))

    def test_rejects_dtd_and_ambiguous_transport_ids(self):
        for data in [b"<!DOCTYPE dds [<!ENTITY custom 'payload'>]>" + TEMPLATE,
                     TEMPLATE.replace(b"target_shm", b"target_udp"), TEMPLATE + b" " * 65536]:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                profile.derive(data)

    def test_service_applies_profile_after_overlays_and_before_python(self):
        run = (ROOT / "deploy/run.sh").read_text(encoding="utf-8")
        section = run.split("  reference-navigation)\n", 1)[1].split("  agent)\n", 1)[0]
        snippets = ['source "$LINGXI_REFERENCE_OVERLAY"', 'reference_dds_profile.py" --check',
                    'unset FASTDDS_DEFAULT_PROFILES_FILE RMW_FASTRTPS_USE_QOS_FROM_XML',
                    'export FASTRTPS_DEFAULT_PROFILES_FILE="$LINGXI_REFERENCE_DDS_PROFILE"',
                    'exec "$LINGXI_REFERENCE_PYTHON"']
        positions = [section.index(s) for s in snippets]
        self.assertEqual(positions, sorted(positions))
        overlay = (ROOT / "deploy/reference-overlay.sh").read_text(encoding="utf-8")
        self.assertNotIn("FASTRTPS_DEFAULT_PROFILES_FILE", overlay)


if __name__ == "__main__":
    unittest.main()
