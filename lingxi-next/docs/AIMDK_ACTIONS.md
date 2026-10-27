# AIMDK Actions and Resources

This adapter targets the AIMDK X2 v1.1.0 interfaces in the supplied ARM64
artifacts. It calls typed ROS services on the robot; browser clients cannot
choose a ROS service, motion enum, resource key, metadata string, or slave IP.

## Preset motions

Only these presets are exposed:

| Label | `McPresetMotion.value` | `McControlArea.value` |
|---|---:|---:|
| 举手 | 1001 | 2 (right hand) |
| 挥手 | 1002 | 2 (right hand) |
| 握手 | 1003 | 2 (right hand) |
| 飞吻 | 1004 | 2 (right hand) |
| 点头 | 4001 | 4 (head) |
| 摇头 | 4002 | 4 (head) |

Before dispatch, the bridge registers `node.preset_motion` with
`SetMcInputSource` using the vendor sample's ADD-then-ENABLE behavior. A
service timeout is never retried. Motion requests set `interrupt=false`,
`play_timestamp=0`, and an empty `ani_path`; no joint or locomotion interface is
exposed.

The service result must have a positive `task_id`. It is treated as accepted
when `header.code == 0`, or when the vendor's documented `RUNNING (400)` state
confirms that execution has started. `GetMcPresetMotionState` must return the
matching task ID. Only state value `SUCCESS (1)` is treated as complete.
`RUNNING (400)`, `PENDING (200)`, and `CREATED (300)` remain busy. Other states,
transport timeouts, or an unavailable state service are ambiguous: no second
motion is sent, the bridge blocks new mapping/navigation/actions, and an
operator must inspect the robot and explicitly clear the interlock in the
authenticated console. Interlock state is persisted in a mode-0600 record, so a
process restart cannot silently clear it.

Independent voice commands return after AIMDK accepts the request. Tour steps
wait for the matching terminal state before continuing. The bridge never
interrupts a previous preset motion to start a new one.

## Expressions

Expressions are fixed labels mapped to `PlayEmoji.emotion_id`. The bridge uses
`mode=EMOTION_MODE_ONCE` and priority `10`. It reports acceptance only when
`success == true`. A timeout is not retried because the expression may already
have started.

## LinkCraft resources

The bridge refreshes the robot's `GetRobotResources` list in the background.
Only resources with a valid key and current version from a successful response
are exposed. The client receives a short opaque ID derived from the key and
version; the raw key and version remain inside the robot bridge. The ID is
version-bound and expires from the usable cache after 60 seconds.

`ExecuteActionResource` receives only that discovered key/version, an empty
slave list, and fixed metadata. The vendor sample classifies a resource key
containing `onnx` as `BODY_MONTION`; other keys use `ARM_MONTION`. The adapter
requires the generic response code `header.header.code == 0` and the documented
`header.message == "Play success"`. AIMDK documentation explicitly says not to
use `header.status.value` as the success test.

The service has no playback-completion field or task-state query. Results are
therefore recorded as “accepted”, never as “playback completed”. After a
successful response, mapping, navigation, and further actions remain interlocked
until an operator confirms the robot has stopped. This state is persisted so
restarting the adapter cannot clear the interlock. To avoid the tour runner
issuing later physical navigation while this result is ambiguous, a LinkCraft
resource is allowed only once, on the final step of a single-pass tour. It is
sent after that step's narration; the tour then ends with the completion
limitation in its status detail.

## Target-robot checks

Before enabling this capability on a physical X2, confirm that the service
types are present, service names match the running AIMDK build, input-source
registration is permitted, and the selected preset is valid for the robot's
current posture. Verify the operator remote/physical emergency stop first.
Exercise each intended enum and LinkCraft resource in a clear, supervised area,
check its response and observed motion, then record the robot/AIMDK build and
result in `ACCEPTANCE.md`.

ROS mock tests validate request fields, allowlists, discovery, response
semantics, state matching, timeout handling, and no-retry behavior. They do not
prove that any motion or resource is supported or safe on a particular robot.
