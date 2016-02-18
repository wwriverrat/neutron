# Copyright 2012 OpenStack Foundation
# All Rights Reserved.
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
import itertools

import netaddr
import six
from tempest_lib.common.utils import data_utils
from tempest_lib import exceptions as lib_exc

from neutron.tests.api import base
from neutron.tests.tempest.common import custom_matchers
from neutron.tests.tempest import config
from neutron.tests.tempest import test

CONF = config.CONF

# TODO Overall:
#   - Are the class helper methods below useful?
#     If so, can we import/inherit from neutron/tests/api/test_networks.py?
#   - resource_setup checks if the extension is enabled
#     How do we ensure this test is run with it enabled?
#     Is it some script or do we make up a "q-ipavailabity enabled" thing?
#   - When adding to these tests, add idempotent_id to all tests by following:
#     http://docs.openstack.org/developer/tempest/HACKING.html#test-identification-with-idempotent-id
#   Ref: http://docs.openstack.org/developer/tempest/HACKING.html


class NetworkIPAvailabilityIpV4TestJSON(base.BaseNetworkTest):

    """
    Tests the following operations in the network_ip_availability plugin for:

    TODO(wwriverrat): Finish this

    """

    @classmethod
    def resource_setup(cls):
        super(NetworkIPAvailabilityIpV4TestJSON, cls).resource_setup()

        if not test.is_extension_enabled('network-ip-availability', 'network'):
            msg = "network-ip-availability not enabled."
            raise cls.skipException(msg)

        # TODO (wwriverrat) Can this be used to establish baseline?
        # TODO ref: neutron/tests/api/test_networks.py
        cls.network = cls.create_network()
        cls.name = cls.network['name']
        cls.subnet = cls._create_subnet_with_last_subnet_block(cls.network,
                                                               cls._ip_version)
        cls.cidr = cls.subnet['cidr']
        cls._subnet_data = {6: {'gateway':
                                str(cls._get_gateway_from_tempest_conf(6)),
                                'allocation_pools':
                                cls._get_allocation_pools_from_gateway(6),
                                'dns_nameservers': ['2001:4860:4860::8844',
                                                    '2001:4860:4860::8888'],
                                'host_routes': [{'destination': '2001::/64',
                                                 'nexthop': '2003::1'}],
                                'new_host_routes': [{'destination':
                                                     '2001::/64',
                                                     'nexthop': '2005::1'}],
                                'new_dns_nameservers':
                                ['2001:4860:4860::7744',
                                 '2001:4860:4860::7888']},
                            4: {'gateway':
                                str(cls._get_gateway_from_tempest_conf(4)),
                                'allocation_pools':
                                cls._get_allocation_pools_from_gateway(4),
                                'dns_nameservers': ['8.8.4.4', '8.8.8.8'],
                                'host_routes': [{'destination': '10.20.0.0/32',
                                                 'nexthop': '10.100.1.1'}],
                                'new_host_routes': [{'destination':
                                                     '10.20.0.0/32',
                                                     'nexthop':
                                                     '10.100.1.2'}],
                                'new_dns_nameservers': ['7.8.8.8', '7.8.4.4']}}

    @classmethod
    def _create_subnet_with_last_subnet_block(cls, network, ip_version):
        """Derive last subnet CIDR block from tenant CIDR and
           create the subnet with that derived CIDR
        """
        if ip_version == 4:
            cidr = netaddr.IPNetwork(CONF.network.tenant_network_cidr)
            mask_bits = CONF.network.tenant_network_mask_bits
        elif ip_version == 6:
            cidr = netaddr.IPNetwork(CONF.network.tenant_network_v6_cidr)
            mask_bits = CONF.network.tenant_network_v6_mask_bits

        subnet_cidr = list(cidr.subnet(mask_bits))[-1]
        gateway_ip = str(netaddr.IPAddress(subnet_cidr) + 1)
        return cls.create_subnet(network, gateway=gateway_ip,
                                 cidr=subnet_cidr, mask_bits=mask_bits)

    @classmethod
    def _get_gateway_from_tempest_conf(cls, ip_version):
        """Return first subnet gateway for configured CIDR """
        if ip_version == 4:
            cidr = netaddr.IPNetwork(CONF.network.tenant_network_cidr)
            mask_bits = CONF.network.tenant_network_mask_bits
        elif ip_version == 6:
            cidr = netaddr.IPNetwork(CONF.network.tenant_network_v6_cidr)
            mask_bits = CONF.network.tenant_network_v6_mask_bits

        if mask_bits >= cidr.prefixlen:
            return netaddr.IPAddress(cidr) + 1
        else:
            for subnet in cidr.subnet(mask_bits):
                return netaddr.IPAddress(subnet) + 1

    @classmethod
    def _get_allocation_pools_from_gateway(cls, ip_version):
        """Return allocation range for subnet of given gateway"""
        gateway = cls._get_gateway_from_tempest_conf(ip_version)
        return [{'start': str(gateway + 2), 'end': str(gateway + 3)}]

    def subnet_dict(self, include_keys):
        """Return a subnet dict which has include_keys and their corresponding
           value from self._subnet_data
        """
        return dict((key, self._subnet_data[self._ip_version][key])
                    for key in include_keys)

    def _compare_resource_attrs(self, actual, expected):
        exclude_keys = set(actual).symmetric_difference(expected)
        self.assertThat(actual, custom_matchers.MatchesDictExceptForKeys(
                        expected, exclude_keys))

    def _delete_network(self, network):
        # Deleting network also deletes its subnets if exists
        self.client.delete_network(network['id'])
        if network in self.networks:
            self.networks.remove(network)
        for subnet in self.subnets:
            if subnet['network_id'] == network['id']:
                self.subnets.remove(subnet)

    def _create_verify_delete_subnet(self, cidr=None, mask_bits=None,
                                     **kwargs):
        network = self.create_network()
        net_id = network['id']
        gateway = kwargs.pop('gateway', None)
        subnet = self.create_subnet(network, gateway, cidr, mask_bits,
                                    **kwargs)
        compare_args_full = dict(gateway_ip=gateway, cidr=cidr,
                                 mask_bits=mask_bits, **kwargs)
        compare_args = dict((k, v) for k, v in six.iteritems(compare_args_full)
                            if v is not None)

        if 'dns_nameservers' in set(subnet).intersection(compare_args):
            self.assertEqual(sorted(compare_args['dns_nameservers']),
                             sorted(subnet['dns_nameservers']))
            del subnet['dns_nameservers'], compare_args['dns_nameservers']

        self._compare_resource_attrs(subnet, compare_args)
        self.client.delete_network(net_id)
        self.networks.pop()
        self.subnets.pop()

    @test.attr(type='smoke')
    def test_network_avail_ip_v4_network_initial(self):
        # Verify we get availability from the networks setup in resource_setup
        # TODO Implement this test
        pass


class NetworkIPAvailabilityIpV6TestJSON(NetworkIPAvailabilityIpV4TestJSON):
    _ip_version = 6

    @test.attr(type='smoke')
    def test_network_avail_ip_v4_network_initial(self):
        # Verify we get availability from the networks setup in resource_setup
        # TODO Implement this test
        pass
