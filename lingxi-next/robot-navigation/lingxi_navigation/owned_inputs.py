"""Read-only topology of one paired saved-map observation/terrain owner."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class OwnedRosInputs:
    namespace: str

    def __post_init__(self):
        if type(self.namespace) is not str or re.fullmatch('lingxi_offline_[a-f0-9]{32}', self.namespace) is None:
            raise ValueError('private_saved_map_ros_namespace_required')

    def profile_options(self):
        root = '/' + self.namespace
        return dict(map_frame='lingxi_saved_map', base_frame='base_link', sensor_frame='lidar_chest_front',
            cloud_topic=root + '/points', odometry_topic=root + '/odometry', terrain_topic=root + '/traversable_map',
            tf_topic=root + '/tf', tf_static_topic=root + '/tf_static')


def descriptor(state, namespace):
    value = dict(schema=1, mode='private_saved_map_inputs', binding=dict(state['binding']),
        generation=state['generation'], namespace=namespace, physicalScaleValidated=False,
        groundSupportValidated=False, navigationReady=False)
    parse_descriptor(value, state)
    return value


def parse_descriptor(value, state):
    fields = {'schema', 'mode', 'binding', 'generation', 'namespace',
              'physicalScaleValidated', 'groundSupportValidated', 'navigationReady'}
    if (type(state) is not dict or type(state.get('binding')) is not dict
            or state['binding'].get('input_mode') != 'live'
            or type(state.get('generation')) is not int or not 0 <= state['generation'] < 2**53
            or type(value) is not dict or set(value) != fields or type(value['schema']) is not int or value['schema'] != 1
            or value['mode'] != 'private_saved_map_inputs' or value['binding'] != state['binding']
            or type(value['generation']) is not int or value['generation'] != state['generation']
            or any(value[key] is not False for key in ('physicalScaleValidated', 'groundSupportValidated', 'navigationReady'))):
        raise ValueError('same_session_saved_map_ros_inputs_required')
    return OwnedRosInputs(value['namespace'])
