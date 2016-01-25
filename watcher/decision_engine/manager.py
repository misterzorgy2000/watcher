# -*- encoding: utf-8 -*-
# Copyright (c) 2015 b<>com
#
# Authors: Jean-Emile DARTOIS <jean-emile.dartois@b-com.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""
This component is responsible for computing a set of potential optimization
:ref:`Actions <action_definition>` in order to fulfill the
:ref:`Goal <goal_definition>` of an :ref:`Audit <audit_definition>`.

It first reads the parameters of the :ref:`Audit <audit_definition>` from the
associated :ref:`Audit Template <audit_template_definition>` and knows the
:ref:`Goal <goal_definition>` to achieve.

It then selects the most appropriate :ref:`Strategy <strategy_definition>`
depending on how Watcher was configured for this :ref:`Goal <goal_definition>`.

The :ref:`Strategy <strategy_definition>` is then executed and generates a set
of :ref:`Actions <action_definition>` which are scheduled in time by the
:ref:`Watcher Planner <watcher_planner_definition>` (i.e., it generates an
:ref:`Action Plan <action_plan_definition>`).

See :doc:`../architecture` for more details on this component.
"""

from oslo_config import cfg
from oslo_log import log

from watcher.common.messaging.messaging_core import MessagingCore
from watcher.decision_engine.messaging.audit_endpoint import AuditEndpoint


LOG = log.getLogger(__name__)
CONF = cfg.CONF

WATCHER_DECISION_ENGINE_OPTS = [
    cfg.StrOpt('topic_control',
               default='watcher.decision.control',
               help='The topic name used for'
                    'control events, this topic '
                    'used for rpc call '),
    cfg.StrOpt('topic_status',
               default='watcher.decision.status',
               help='The topic name used for '
                    'status events, this topic '
                    'is used so as to notify'
                    'the others components '
                    'of the system'),
    cfg.StrOpt('publisher_id',
               default='watcher.decision.api',
               help='The identifier used by watcher '
                    'module on the message broker'),
    cfg.IntOpt('max_workers',
               default=2,
               required=True,
               help='The maximum number of threads that can be used to '
                    'execute strategies',
               ),
]
decision_engine_opt_group = cfg.OptGroup(name='watcher_decision_engine',
                                         title='Defines the parameters of '
                                               'the module decision engine')
CONF.register_group(decision_engine_opt_group)
CONF.register_opts(WATCHER_DECISION_ENGINE_OPTS, decision_engine_opt_group)


class DecisionEngineManager(MessagingCore):
    def __init__(self):
        super(DecisionEngineManager, self).__init__(
            CONF.watcher_decision_engine.publisher_id,
            CONF.watcher_decision_engine.topic_control,
            CONF.watcher_decision_engine.topic_status,
            api_version=self.API_VERSION)
        endpoint = AuditEndpoint(self,
                                 max_workers=CONF.watcher_decision_engine.
                                 max_workers)
        self.topic_control.add_endpoint(endpoint)

    def join(self):
        self.topic_control.join()
        self.topic_status.join()
