# Copyright (c) 2015 OpenStack Foundation
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import mock
from oslo_utils import uuidutils

from neutron.agent.common import utils
from neutron.agent.l3 import dvr_fip_ns
from neutron.agent.l3 import link_local_allocator as lla
from neutron.agent.linux import ip_lib
from neutron.agent.linux import iptables_manager
from neutron.tests import base

_uuid = uuidutils.generate_uuid


class TestDvrFipNs(base.BaseTestCase):
    def setUp(self):
        super(TestDvrFipNs, self).setUp()
        self.conf = mock.Mock()
        self.conf.state_path = '/tmp'
        self.driver = mock.Mock()
        self.driver.DEV_NAME_LEN = 14
        self.net_id = _uuid()
        self.fip_ns = dvr_fip_ns.FipNamespace(self.net_id,
                                              self.conf,
                                              self.driver,
                                              use_ipv6=True)

    def test_subscribe(self):
        is_first = self.fip_ns.subscribe(mock.sentinel.external_net_id)
        self.assertTrue(is_first)

    def test_subscribe_not_first(self):
        self.fip_ns.subscribe(mock.sentinel.external_net_id)
        is_first = self.fip_ns.subscribe(mock.sentinel.external_net_id2)
        self.assertFalse(is_first)

    def test_unsubscribe(self):
        self.fip_ns.subscribe(mock.sentinel.external_net_id)
        is_last = self.fip_ns.unsubscribe(mock.sentinel.external_net_id)
        self.assertTrue(is_last)

    def test_unsubscribe_not_last(self):
        self.fip_ns.subscribe(mock.sentinel.external_net_id)
        self.fip_ns.subscribe(mock.sentinel.external_net_id2)
        is_last = self.fip_ns.unsubscribe(mock.sentinel.external_net_id2)
        self.assertFalse(is_last)

    def test_allocate_rule_priority(self):
        pr = self.fip_ns.allocate_rule_priority('20.0.0.30')
        self.assertIn('20.0.0.30', self.fip_ns._rule_priorities.allocations)
        self.assertNotIn(pr, self.fip_ns._rule_priorities.pool)

    def test_deallocate_rule_priority(self):
        pr = self.fip_ns.allocate_rule_priority('20.0.0.30')
        self.fip_ns.deallocate_rule_priority('20.0.0.30')
        self.assertNotIn('20.0.0.30', self.fip_ns._rule_priorities.allocations)
        self.assertIn(pr, self.fip_ns._rule_priorities.pool)

    @mock.patch.object(ip_lib, 'IPWrapper')
    @mock.patch.object(ip_lib, 'IPDevice')
    @mock.patch.object(ip_lib, 'send_ip_addr_adv_notif')
    @mock.patch.object(ip_lib, 'device_exists')
    def test_gateway_added(self, device_exists, send_adv_notif,
                           IPDevice, IPWrapper):
        subnet_id = _uuid()
        agent_gw_port = {'fixed_ips': [{'ip_address': '20.0.0.30',
                                        'prefixlen': 24,
                                        'subnet_id': subnet_id}],
                         'subnets': [{'id': subnet_id,
                                      'cidr': '20.0.0.0/24',
                                      'gateway_ip': '20.0.0.1'}],
                         'id': _uuid(),
                         'network_id': self.net_id,
                         'mac_address': 'ca:fe:de:ad:be:ef'}

        device_exists.return_value = False
        self.fip_ns._gateway_added(agent_gw_port,
                                   mock.sentinel.interface_name)
        self.assertEqual(1, self.driver.plug.call_count)
        self.assertEqual(1, self.driver.init_l3.call_count)
        send_adv_notif.assert_called_once_with(self.fip_ns.get_name(),
                                               mock.sentinel.interface_name,
                                               '20.0.0.30',
                                               mock.ANY)

    @mock.patch.object(iptables_manager, 'IptablesManager')
    @mock.patch.object(utils, 'execute')
    @mock.patch.object(ip_lib.IpNetnsCommand, 'exists')
    def _test_create(self, old_kernel, exists, execute, IPTables):
        exists.return_value = True
        # There are up to four sysctl calls - two for ip_nonlocal_bind,
        # and two to enable forwarding
        execute.side_effect = [RuntimeError if old_kernel else None,
                               None, None, None]

        self.fip_ns._iptables_manager = IPTables()
        self.fip_ns.create()

        ns_name = self.fip_ns.get_name()

        netns_cmd = ['ip', 'netns', 'exec', ns_name]
        bind_cmd = ['sysctl', '-w', 'net.ipv4.ip_nonlocal_bind=1']
        expected = [mock.call(netns_cmd + bind_cmd, check_exit_code=True,
                              extra_ok_codes=None, log_fail_as_error=False,
                              run_as_root=True)]

        if old_kernel:
            expected.append(mock.call(bind_cmd, check_exit_code=True,
                                      extra_ok_codes=None,
                                      log_fail_as_error=True,
                                      run_as_root=True))

        execute.assert_has_calls(expected)

    def test_create_old_kernel(self):
        self._test_create(True)

    def test_create_new_kernel(self):
        self._test_create(False)

    @mock.patch.object(ip_lib, 'IPWrapper')
    def test_destroy(self, IPWrapper):
        ip_wrapper = IPWrapper()
        dev1 = mock.Mock()
        dev1.name = 'fpr-aaaa'
        dev2 = mock.Mock()
        dev2.name = 'fg-aaaa'
        ip_wrapper.get_devices.return_value = [dev1, dev2]

        with mock.patch.object(self.fip_ns.ip_wrapper_root.netns,
                               'delete') as delete:
            self.fip_ns.delete()
            delete.assert_called_once_with(mock.ANY)

        ext_net_bridge = self.conf.external_network_bridge
        ns_name = self.fip_ns.get_name()
        self.driver.unplug.assert_called_once_with('fg-aaaa',
                                                   bridge=ext_net_bridge,
                                                   prefix='fg-',
                                                   namespace=ns_name)
        ip_wrapper.del_veth.assert_called_once_with('fpr-aaaa')

    @mock.patch.object(ip_lib, 'IPWrapper')
    @mock.patch.object(ip_lib, 'IPDevice')
    @mock.patch.object(ip_lib, 'device_exists')
    def test_create_rtr_2_fip_link(self, device_exists, IPDevice, IPWrapper):
        ri = mock.Mock()
        ri.router_id = _uuid()
        ri.rtr_fip_subnet = None
        ri.ns_name = mock.sentinel.router_ns

        rtr_2_fip_name = self.fip_ns.get_rtr_ext_device_name(ri.router_id)
        fip_2_rtr_name = self.fip_ns.get_int_device_name(ri.router_id)
        fip_ns_name = self.fip_ns.get_name()

        self.fip_ns.local_subnets = allocator = mock.Mock()
        pair = lla.LinkLocalAddressPair('169.254.31.28/31')
        allocator.allocate.return_value = pair
        device_exists.return_value = False
        ip_wrapper = IPWrapper()
        self.conf.network_device_mtu = 2000
        ip_wrapper.add_veth.return_value = (IPDevice(), IPDevice())

        self.fip_ns.create_rtr_2_fip_link(ri)

        ip_wrapper.add_veth.assert_called_with(rtr_2_fip_name,
                                               fip_2_rtr_name,
                                               fip_ns_name)

        device = IPDevice()
        device.link.set_mtu.assert_called_with(2000)
        self.assertEqual(2, device.link.set_mtu.call_count)
        device.route.add_gateway.assert_called_once_with(
            '169.254.31.29', table=16)

    @mock.patch.object(ip_lib, 'IPWrapper')
    @mock.patch.object(ip_lib, 'IPDevice')
    @mock.patch.object(ip_lib, 'device_exists')
    def test_create_rtr_2_fip_link_already_exists(self,
                                                  device_exists,
                                                  IPDevice,
                                                  IPWrapper):
        ri = mock.Mock()
        ri.router_id = _uuid()
        ri.rtr_fip_subnet = None
        device_exists.return_value = True

        self.fip_ns.local_subnets = allocator = mock.Mock()
        pair = lla.LinkLocalAddressPair('169.254.31.28/31')
        allocator.allocate.return_value = pair
        self.fip_ns.create_rtr_2_fip_link(ri)

        ip_wrapper = IPWrapper()
        self.assertFalse(ip_wrapper.add_veth.called)

    @mock.patch.object(ip_lib, 'IPDevice')
    def _test_scan_fip_ports(self, ri, ip_list, IPDevice):
        IPDevice.return_value = device = mock.Mock()
        device.addr.list.return_value = ip_list
        self.fip_ns.get_rtr_ext_device_name = mock.Mock(
            return_value=mock.sentinel.rtr_ext_device_name)
        self.fip_ns.scan_fip_ports(ri)

    @mock.patch.object(ip_lib, 'device_exists')
    def test_scan_fip_ports_restart_fips(self, device_exists):
        device_exists.return_value = True
        ri = mock.Mock()
        ri.dist_fip_count = None
        ri.floating_ips_dict = {}
        ip_list = [{'cidr': '111.2.3.4/32'}, {'cidr': '111.2.3.5/32'}]
        self._test_scan_fip_ports(ri, ip_list)
        self.assertEqual(2, ri.dist_fip_count)

    @mock.patch.object(ip_lib, 'device_exists')
    def test_scan_fip_ports_restart_none(self, device_exists):
        device_exists.return_value = True
        ri = mock.Mock()
        ri.dist_fip_count = None
        ri.floating_ips_dict = {}
        self._test_scan_fip_ports(ri, [])
        self.assertEqual(0, ri.dist_fip_count)

    def test_scan_fip_ports_restart_zero(self):
        ri = mock.Mock()
        ri.dist_fip_count = 0
        self._test_scan_fip_ports(ri, None)
        self.assertEqual(0, ri.dist_fip_count)
