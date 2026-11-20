"""Fence the pinned reference controllers at the AIMDK output boundary."""

from dataclasses import dataclass, field
import logging
import math
import os
from pathlib import Path
import threading
import time
import uuid


LOG = logging.getLogger("lingxi.reference.motion")
TERMINAL_ACTION = {4, 5, 6}  # ROS GoalStatus SUCCEEDED, CANCELED, ABORTED
STOPPED_MC = {2, 4}          # AIMDK STABLE, SAFE
ACTIVE = {"requested", "running", "stopping"}


class TrackedActionClient:
    def __init__(self, delegate, gateway):
        self.delegate = delegate
        self.gateway = gateway

    def wait_for_server(self, **kwargs):
        return self.delegate.wait_for_server(**kwargs)

    def send_goal_async(self, *args, **kwargs):
        with self.gateway.lock:
            task = self.gateway.motion_task
            if not task or task.cancelled.is_set() or task.phase not in ACTIVE:
                raise InterruptedError("reference_goal_revoked")
            future = self.delegate.send_goal_async(*args, **kwargs)
            task.actions[future] = None
            task.nav2_started_ns = time.time_ns()
            return future


@dataclass
class MotionTask:
    task_id: str
    point_id: str
    map_id: str
    goal: dict
    mode: str
    cancelled: threading.Event = field(default_factory=threading.Event)
    dispatched: threading.Event = field(default_factory=threading.Event)
    actions: dict = field(default_factory=dict)
    phase: str = "requested"
    detail: str = "正在启动控制器"
    result: str = "failed"
    acquired: bool = False
    first_output: float | None = None
    nav2_started_ns: int = 0
    stop_stamp: float = 0.
    retirement_running: bool = False
    deadline: float = 0.


def gateway_type(base):
    from aimdk_msgs.msg import McCommonState
    from nav2_msgs.msg import Costmap
    from rclpy.qos import qos_profile_sensor_data
    from robot_nav_tasks.localization_timing import localization_timing_fresh
    from robot_nav_tasks.navigation_motion import measured_motion

    class GuardedGateway(base):
        def __init__(self, settings):
            self.motion_task = None
            self.mc_sample = None
            self.local_costmap_stamp = None
            self.physical_motion_sent = False
            self.motion_session = uuid.uuid4().hex
            # Each process owns a unique source. A restart cannot remove or reuse its predecessor's source.
            self.input_name = "lingxi-ref-" + self.motion_session[:12]
            self.idle_sources = set(settings["lingxi_motion_idle_sources"])
            self.motion_record = Path(settings["data_dir"]) / "motion-in-progress"
            if self.motion_record.exists():
                raise RuntimeError("previous_reference_task_requires_field_stop_verification")
            super().__init__({**settings, "source_name": self.input_name, "goal_max_corrections": 0})

        def start(self):
            super().start()
            self.nav_client = TrackedActionClient(self.nav_client, self)
            self.mc_subscription = self.node.create_subscription(
                McCommonState, "/aima/mc/common/state", self._common_state, qos_profile_sensor_data,
                callback_group=self.group)
            self.local_costmap_subscription = self.node.create_subscription(
                Costmap, "/nav_tasks/local_costmap/costmap_raw", self._local_costmap,
                qos_profile_sensor_data, callback_group=self.group)

        def _local_costmap(self, message):
            header = message.header
            if header.frame_id != "odom" or not message.data:
                return
            stamp = header.stamp.sec + header.stamp.nanosec / 1e9
            with self.lock:
                if -0.05 <= time.time() - stamp <= 1.0:
                    self.local_costmap_stamp = stamp

        def _common_state(self, message):
            stamp = message.header.stamp.sec + message.header.stamp.nanosec / 1e9
            with self.lock:
                prior = self.mc_sample
                if not -0.05 <= time.time() - stamp <= .35 or (prior and stamp <= prior[0]):
                    return
                self.mc_sample = (stamp, time.monotonic(), message.input_source.name, int(message.fsm_state.current_state))

        def _fresh_mc(self):
            sample = self.mc_sample
            return sample if sample and time.monotonic() - sample[1] <= .35 and -0.05 <= time.time() - sample[0] <= .35 else None

        def _location_ready(self):
            # The upstream point controller can fall back to LIO without a recent map correction.
            frame = self.navigation_frame
            if (frame is None or self.nav_base_pose is None or self.map_from_odom is None
                    or self.accepted_stamp is None or self.accepted_received_at is None):
                return False
            return (frame["stamp"] >= self.accepted_stamp - .05 and localization_timing_fresh(
                time.time() - frame["stamp"], time.monotonic() - self.navigation_frame_received,
                self.accepted_measurement_age, time.monotonic() - self.accepted_received_at,
                time.time() - self.accepted_stamp, self.settings["pose_timeout_s"],
                self.settings["localization_timeout_s"], self.settings["alignment_update_timeout_s"]))

        def _motion_evidence(self):
            observation = self.last_observation
            frame = self.navigation_frame
            costmap = self.costmap_message
            if costmap is None or costmap.header.frame_id != "map":
                return False
            stamp = costmap.header.stamp.sec + costmap.header.stamp.nanosec / 1e9
            return (not self.fatal_error and self._location_ready() and self.footprint is not None
                    and observation is not None and frame is not None and observation["run_id"] == frame["run_id"]
                    and -0.05 <= time.time() - observation["stamp"] <= .35
                    and -0.05 <= time.time() - stamp <= 1.0
                    and 0 <= time.monotonic() - self.costmap_received_at <= 1.0
                    and self.mc_action in ("STAND_DEFAULT", "LOCOMOTION_DEFAULT")
                    and time.monotonic() - self.mc_action_received <= 2.5)

        def _permit_output(self, velocity):
            task = self.motion_task
            if not task or task.phase != "running" or task.cancelled.is_set():
                return False
            mc = self._fresh_mc()
            now = time.monotonic()
            within_limits = (all(map(math.isfinite, velocity)) and 0 <= velocity[0] <= self.settings["max_forward_velocity"]
                             and abs(velocity[1]) < 1e-8 and abs(velocity[2]) <= self.settings["max_angular_velocity"])
            if not within_limits or not self._motion_evidence() or not mc:
                self.revoke(task, "定位、障碍、速度或 AIMDK 状态未确认")
                return False
            if task.mode == "nav2" and (not self.local_costmap_stamp or time.time() - self.local_costmap_stamp > 1.0):
                self.revoke(task, "Nav2 局部代价地图已失效")
                return False
            if mc[2] == self.input_name:
                task.acquired = True
            elif (task.acquired or mc[2] not in self.idle_sources or mc[3] not in STOPPED_MC
                  or (task.first_output is not None and now - task.first_output > .25)):
                self.revoke(task, "AIMDK 控制权发生变化")
                return False
            if task.first_output is None:
                task.first_output = now
            self.physical_motion_sent = True
            return True

        def _publish_mc(self, velocity):
            if not any(velocity) or self._permit_output(velocity):
                super()._publish_mc(velocity)

        def _publish_point(self, session, velocity):
            if not any(velocity) or self._permit_output(velocity):
                super()._publish_point(session, velocity)

        def _on_velocity(self, message, info):
            with self.lock:
                task = self.motion_task
                now = time.time_ns()
                stamp = info.get("source_timestamp", 0)
                if not task or task.mode != "nav2" or not task.nav2_started_ns <= stamp <= now or now - stamp > 200_000_000:
                    return
                publishers = self.node.get_publishers_info_by_topic("/nav_tasks/cmd_vel")
                gid = info.get("publisher_gid")
                # Humble may not expose publisher_gid in the callback metadata. Check the graph either way.
                if (len(publishers) != 1 or publishers[0].node_name != "velocity_smoother"
                        or publishers[0].node_namespace != "/nav_tasks"
                        or (gid is not None and bytes(gid) != bytes(publishers[0].endpoint_gid))):
                    self.revoke(task, "Nav2 速度发布者身份未确认")
                    return
                super()._on_velocity(message)

        def _on_raw_velocity(self, message):
            # Raw controller data may refine the smoother command but never drives the robot itself.
            values = [message.linear.x, message.linear.y, message.angular.z]
            with self.lock:
                self.raw_command = {"time": time.time(), "velocity": values} if all(map(math.isfinite, values)) else None

        def _watchdog(self):
            with self.lock:
                task = self.motion_task
                if task and task.phase in {"requested", "running"} and time.monotonic() >= task.deadline:
                    self.revoke(task, "导航执行超时")
                if task and task.phase == "running":
                    mc = self._fresh_mc()
                    if not self._motion_evidence() or not mc or mc[2] not in {self.input_name, *self.idle_sources}:
                        self.revoke(task, "实时定位、障碍或 AIMDK 控制状态丢失")
                super()._watchdog()

        def output_idle(self):
            task = self.motion_task
            return task is None or task.phase in {"idle", "finished", "failed"} and not task.retirement_running

        def ready(self):
            mc = self._fresh_mc()
            return bool(mc and mc[2] in self.idle_sources and mc[3] == 2 and self._motion_evidence()
                        and self.source_set.service_is_ready() and self.source_get.service_is_ready())

        def submit(self, task):
            with self.lock:
                if self._navigation_busy() or not self.ready():
                    raise RuntimeError("reference_motion_admission_failed")
                with self.motion_record.open("x", encoding="ascii") as record:
                    record.write(self.motion_session + "\n")
                    record.flush()
                    os.fsync(record.fileno())
                self.motion_task = task
                task.deadline = time.monotonic() + 150
                self.navigation_mode = task.mode

            def notify(event, feedback):
                with self.lock:
                    if self.motion_task is not task or task.phase not in {"requested", "running"}:
                        return
                    if event in {"accepted", "executing"}:
                        task.phase = "running"
                        task.detail = str(feedback.get("reason") or "正在导航")[:200]
                    else:
                        self.revoke(task, str(feedback.get("reason") or "正在核验停稳")[:200],
                                    result="finished" if event == "succeeded" else "failed")

            def run():
                try:
                    self.navigate(dict(task.goal), notify, task.cancelled)
                except Exception:
                    LOG.exception("Reference controller submission failed")
                    self.revoke(task, "控制器启动失败")
                finally:
                    task.dispatched.set()

            threading.Thread(target=run, daemon=True, name="reference-task").start()

        def close(self):
            with self.lock:
                task = self.motion_task
                if task and task.phase in ACTIVE:
                    self.revoke(task, "导航服务正在停止")
            if task:
                deadline = time.monotonic() + 17
                while task.retirement_running and time.monotonic() < deadline:
                    time.sleep(.05)
            super().close()

        def revoke(self, task, detail, result="failed"):
            with self.lock:
                if self.motion_task is not task or task.phase not in ACTIVE:
                    return
                if task.phase != "stopping":
                    task.cancelled.set()
                    task.stop_stamp = time.time()
                    task.phase, task.detail, task.result = "stopping", detail, result
                    self._stop_motion()
                if not task.retirement_running:
                    task.retirement_running = True
                    threading.Thread(target=self._retire, args=(task,), daemon=True, name="reference-stop").start()

        def _actions_terminal(self, task):
            for pending, result in list(task.actions.items()):
                if not pending.done():
                    return False
                handle = pending.result()
                if not handle.accepted:
                    continue
                if result is None:
                    result = task.actions[pending] = handle.get_result_async()
                    handle.cancel_goal_async()
                if not result.done() or int(result.result().status) not in TERMINAL_ACTION:
                    return False
            return True

        def _retire(self, task):
            stable_since = None
            deadline = time.monotonic() + 16
            try:
                while time.monotonic() < deadline:
                    with self.lock:
                        actions_terminal = task.dispatched.is_set() and self._actions_terminal(task)
                        if actions_terminal and self._navigation_busy():
                            self._finish_navigation(self.goal_epoch, "cancelled", {"reason": task.detail})
                        mc = self._fresh_mc()
                        motion = measured_motion(self.motion_history)
                        if (actions_terminal and not self._navigation_busy() and self._odometry_fresh() and mc
                                and mc[0] > task.stop_stamp and mc[3] in STOPPED_MC
                                and self.motion_history and self.motion_history[-1][0] > task.stop_stamp + .5
                                and motion and motion["linear_mps"] <= .02 and motion["angular_rps"] <= .02):
                            stable_since = stable_since if stable_since is not None else time.monotonic()
                            if time.monotonic() - stable_since >= .8:
                                self.motion_record.unlink()
                                task.phase = task.result
                                if task.phase == "finished":
                                    task.detail = "已到达并确认停稳"
                                return
                        else:
                            stable_since = None
                    time.sleep(.05)
            except Exception:
                LOG.exception("Unable to confirm reference controller retirement")
            finally:
                with self.lock:
                    task.retirement_running = False
                    if task.phase == "stopping":
                        task.detail = "停稳或控制器收尾未确认，请现场核对并重试停止"

    return GuardedGateway
