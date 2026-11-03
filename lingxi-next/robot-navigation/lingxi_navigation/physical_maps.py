"""Saved-map selections for the production Nav2 owner, not a localization seed."""
from .localization_guard import LocalizationGuard, LocalizationMonitor
from .nav2_producer import Nav2Generation, Nav2MapSelection, load_configuration, load_profile
from .physical_ros import digest_footprint


def read_map_inputs(map_activator, map_id, footprint, profile_file, profile_options, *, input_mode='explicit'):
    if (input_mode not in ('explicit', 'owned-localization') or type(profile_options) is not dict
            or input_mode == 'owned-localization' and profile_options):
        raise ValueError('exclusive_navigation_input_mode_required')
    identity = map_activator.evidence(map_id)
    binding = map_activator.localization_binding(map_id)
    if digest_footprint(footprint) != identity.footprintSha256:
        raise RuntimeError('active_map_footprint_mismatch')
    if input_mode == 'owned-localization':
        return identity, binding, load_configuration(profile_file, identity)
    profile = load_profile(profile_file, identity, **profile_options)
    if (profile.map_frame, profile.base_frame, profile.sensor_frame) != (
            'lingxi_saved_map', 'base_link', 'lidar_chest_front'):
        raise RuntimeError('same_localization_projection_frames_required')
    return identity, binding, profile


def load_map_selection(map_activator, map_id, *, footprint, profile_file, profile_options,
                       localization_endpoint, run_root, executor, output, operation_gate,
                       activate=False, input_mode='explicit'):
    identity, binding, configuration = read_map_inputs(map_activator, map_id, footprint, profile_file,
                                                       profile_options, input_mode=input_mode)
    owned = input_mode == 'owned-localization'
    monitor = LocalizationMonitor(localization_endpoint, identity, binding, **({'require_inputs': True} if owned else {}))
    localization = LocalizationGuard(monitor)

    def factory(ticket):
        if (map_activator.evidence(map_id) != identity
                or map_activator.localization_binding(map_id) != binding
                or digest_footprint(footprint) != identity.footprintSha256):
            raise RuntimeError('saved_map_evidence_changed_before_nav2_preparation')
        sample = localization.current() if owned else None
        if owned and (sample is None or sample.ros_inputs is None):
            raise RuntimeError('activated_owned_localization_inputs_required')
        profile = configuration.bind(**sample.ros_inputs.profile_options()) if owned else configuration
        generation = Nav2Generation(profile, ticket, run_root, footprint, executor,
                                    operation_gate=operation_gate)
        try:
            current = localization.current() if owned else None
            if owned and (current is None or (current.token, current.revision) != (sample.token, sample.revision)):
                raise RuntimeError('owned_localization_inputs_changed_during_preparation')
            generation.connect(output, profile.limits, validate_observation=localization.validate_observation)
            return generation
        except BaseException:
            generation.close()
            raise

    try:
        if activate:
            monitor.refresh()
            localization.activate_map(map_id, None)
        monitor.start()
        return Nav2MapSelection(identity, localization, factory)
    except BaseException:
        monitor.close()
        raise
