"""Production action-driver startup ownership without ROS or physical output."""
from concurrent.futures import Future
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'robot-navigation'))
from lingxi_navigation.nav2_action import Nav2ActionDriver
from lingxi_navigation.lifecycle_startup import LifecycleStartup
from lingxi_navigation.physical_bridge import Ticket


class Client:
    def __init__(self):
        self.available, self.calls = True, []

    def service_is_ready(self):
        return self.available

    def call_async(self, request):
        future = Future()
        self.calls.append((request, future))
        return future


class ActionClient:
    def __init__(self, *args):
        self.available, self.destroyed = True, False

    def server_is_ready(self):
        return self.available

    def destroy(self):
        self.destroyed = True


class Node:
    def __init__(self):
        self.clients, self.destroyed = {}, []

    def create_client(self, kind, name):
        self.clients[name] = Client()
        return self.clients[name]

    def destroy_client(self, client):
        self.destroyed.append(client)


class StartupRequest:
    STARTUP = 0

    def __init__(self, *, command):
        self.command = command


class ManagedStartupTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.node = 0., Node()
        action = NS(ActionClient=ActionClient)
        navigation = NS(NavigateToPose=NS())
        lifecycle = NS(GetState=NS(Request=object))
        manager = NS(ManageLifecycleNodes=NS(Request=StartupRequest))
        modules = {'rclpy': NS(action=action), 'rclpy.action': action,
            'nav2_msgs': NS(action=navigation, srv=manager), 'nav2_msgs.action': navigation,
            'nav2_msgs.srv': manager, 'lifecycle_msgs': NS(srv=lifecycle), 'lifecycle_msgs.srv': lifecycle}
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.driver = Nav2ActionDriver(self.node, 'map', clock=lambda: self.clock, managed_startup=True)
        self.addCleanup(self.driver.close)

    def reply(self, state):
        for client in self.driver.clients.values():
            client.calls[-1][1].set_result(NS(current_state=NS(id=state)))

    def discover(self):
        self.driver.poll()
        self.reply(1)
        self.clock = .2
        self.driver.poll()
        self.assertEqual([], self.driver.manager.calls)
        self.reply(1)
        self.clock = .4
        self.driver.poll()

    def test_production_driver_requires_official_ack_not_active_state_alone(self):
        self.discover()
        self.assertEqual(1, len(self.driver.manager.calls))
        self.assertEqual(StartupRequest.STARTUP, self.driver.manager.calls[0][0].command)
        self.reply(3)
        self.clock = .6
        self.driver.poll()
        self.assertFalse(self.driver.ready())
        with self.assertRaisesRegex(RuntimeError, 'current_nav2_lifecycle'):
            self.driver.start(Ticket('test', 'a' * 32), dict(x=1., y=0., yaw=0.))
        self.driver.manager.calls[0][1].set_result(NS(success=True))
        self.driver.poll()
        self.assertTrue(self.driver.ready())
        self.assertEqual((), self.driver.events())
        self.assertTrue(self.driver.ready(), 'reading events must not close startup ownership')
        self.clock = 1.3
        self.assertFalse(self.driver.ready())

    def test_startup_refusal_faults_without_replay(self):
        self.discover()
        self.driver.manager.calls[0][1].set_result(NS(success=False))
        with self.assertRaisesRegex(RuntimeError, 'nav2_lifecycle_startup_failed:lifecycle_startup_refused'):
            self.driver.poll()
        self.assertFalse(self.driver.ready())
        self.assertEqual(1, len(self.driver.manager.calls))

    def test_close_cancels_pending_queries_and_manager_and_fences_late_readiness(self):
        self.discover()
        manager = self.driver.manager
        pending = [client.calls[-1][1] for client in self.driver.clients.values()]
        startup = manager.calls[0][1]
        self.driver.close()
        self.assertTrue(startup.cancelled())
        self.assertTrue(all(future.cancelled() for future in pending))
        self.assertEqual(5, len(self.node.destroyed))
        self.assertTrue(self.driver.client.destroyed)
        self.driver.poll()
        self.driver.close()
        self.assertFalse(self.driver.ready())
        self.assertEqual(1, len(manager.calls))

    def test_unmanaged_existing_action_client_remains_passive(self):
        node = Node()
        driver = Nav2ActionDriver(node, 'map', clock=lambda: self.clock)
        self.addCleanup(driver.close)
        self.assertIsNone(driver.startup)
        self.assertEqual(4, len(node.clients))
        driver.poll()
        for client in driver.clients.values():
            client.calls[0][1].set_result(NS(current_state=NS(id=3)))
        driver.poll()
        self.assertTrue(driver.ready())
        self.assertNotIn('lifecycle_manager/manage_nodes', node.clients)

    def test_invalid_startup_configuration_never_creates_control_clients(self):
        node = Node()
        with self.assertRaises(ValueError):
            Nav2ActionDriver(node, 'map', managed_startup=1)
        self.assertEqual({}, node.clients)
        for count, age in ((3, .6), (5, .6), (4, True), (4, float('nan')), (4, 1.1)):
            with self.subTest(count=count, age=age), self.assertRaises(ValueError):
                LifecycleStartup([Client() for _ in range(count)], Client(), object, object, maximum_age=age)


if __name__ == '__main__':
    unittest.main()
