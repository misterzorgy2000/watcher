# -*- encoding: utf-8 -*-
# Copyright (c) 2015 b<>com
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import unicode_literals
from six.moves.socketserver import BaseServer


import types
from wsgiref import simple_server

from mock import Mock
from mock import patch
from oslo_config import cfg
from pecan.testing import load_test_app
from watcher.api import config as api_config
from watcher.cmd import api
from watcher.tests.base import BaseTestCase


class TestApi(BaseTestCase):
    def setUp(self):
        super(TestApi, self).setUp()

        self.conf = cfg.CONF
        self._parse_cli_opts = self.conf._parse_cli_opts

        def _fake_parse(self, args=[]):
            return cfg.ConfigOpts._parse_cli_opts(self, [])

        _fake_parse_method = types.MethodType(_fake_parse, self.conf)
        self.conf._parse_cli_opts = _fake_parse_method

    def tearDown(self):
        super(TestApi, self).tearDown()
        self.conf._parse_cli_opts = self._parse_cli_opts

    @patch("watcher.api.app.pecan.make_app")
    @patch.object(BaseServer, "serve_forever", Mock())
    @patch.object(simple_server, "make_server")
    def test_run_api_app(self, m_make, m_make_app):
        m_make_app.return_value = load_test_app(config=api_config.PECAN_CONFIG)
        api.main()
        self.assertEqual(1, m_make.call_count)

    @patch("watcher.api.app.pecan.make_app")
    @patch.object(BaseServer, "serve_forever", Mock())
    @patch.object(simple_server, "make_server")
    def test_run_api_app_serve_specific_address(self, m_make, m_make_app):
        cfg.CONF.set_default("host", "localhost", group="api")
        m_make_app.return_value = load_test_app(config=api_config.PECAN_CONFIG)
        api.main()
        self.assertEqual(1, m_make.call_count)
