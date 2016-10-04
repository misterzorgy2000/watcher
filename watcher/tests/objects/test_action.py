# Copyright 2015 OpenStack Foundation
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

import mock

from watcher.common import exception
from watcher.db.sqlalchemy import api as db_api
from watcher import objects
from watcher.tests.db import base
from watcher.tests.db import utils


class TestActionObject(base.DbTestCase):

    action_plan_id = 2

    scenarios = [
        ('non_eager', dict(
            eager=False,
            fake_action=utils.get_test_action(
                action_plan_id=action_plan_id))),
        ('eager_with_non_eager_load', dict(
            eager=True,
            fake_action=utils.get_test_action(
                action_plan_id=action_plan_id))),
        ('eager_with_eager_load', dict(
            eager=True,
            fake_action=utils.get_test_action(
                action_plan_id=action_plan_id,
                action_plan=utils.get_test_action_plan(id=action_plan_id)))),
    ]

    def setUp(self):
        super(TestActionObject, self).setUp()
        self.fake_action_plan = utils.create_test_action_plan(
            id=self.action_plan_id)

    def eager_action_assert(self, action):
        if self.eager:
            self.assertIsNotNone(action.action_plan)
            fields_to_check = set(
                super(objects.ActionPlan, objects.ActionPlan).fields
            ).symmetric_difference(objects.ActionPlan.fields)
            db_data = {
                k: v for k, v in self.fake_action_plan.as_dict().items()
                if k in fields_to_check}
            object_data = {
                k: v for k, v in action.action_plan.as_dict().items()
                if k in fields_to_check}
            self.assertEqual(db_data, object_data)

    @mock.patch.object(db_api.Connection, 'get_action_by_id')
    def test_get_by_id(self, mock_get_action):
        mock_get_action.return_value = self.fake_action
        action_id = self.fake_action['id']
        action = objects.Action.get(self.context, action_id, eager=self.eager)
        mock_get_action.assert_called_once_with(
            self.context, action_id, eager=self.eager)
        self.assertEqual(self.context, action._context)
        self.eager_action_assert(action)

    @mock.patch.object(db_api.Connection, 'get_action_by_uuid')
    def test_get_by_uuid(self, mock_get_action):
        mock_get_action.return_value = self.fake_action
        uuid = self.fake_action['uuid']
        action = objects.Action.get(self.context, uuid, eager=self.eager)
        mock_get_action.assert_called_once_with(
            self.context, uuid, eager=self.eager)
        self.assertEqual(self.context, action._context)

    def test_get_bad_id_and_uuid(self):
        self.assertRaises(exception.InvalidIdentity,
                          objects.Action.get, self.context, 'not-a-uuid',
                          eager=self.eager)

    @mock.patch.object(db_api.Connection, 'get_action_list')
    def test_list(self, mock_get_list):
        mock_get_list.return_value = [self.fake_action]
        actions = objects.Action.list(self.context, eager=self.eager)
        self.assertEqual(1, mock_get_list.call_count)
        self.assertEqual(1, len(actions))
        self.assertIsInstance(actions[0], objects.Action)
        self.assertEqual(self.context, actions[0]._context)
        for action in actions:
            self.eager_action_assert(action)

    @mock.patch.object(db_api.Connection, 'update_action')
    @mock.patch.object(db_api.Connection, 'get_action_by_uuid')
    def test_save(self, mock_get_action, mock_update_action):
        mock_get_action.return_value = self.fake_action
        uuid = self.fake_action['uuid']
        action = objects.Action.get_by_uuid(
            self.context, uuid, eager=self.eager)
        action.state = objects.action.State.SUCCEEDED
        action.save()

        mock_get_action.assert_called_once_with(
            self.context, uuid, eager=self.eager)
        mock_update_action.assert_called_once_with(
            uuid, {'state': objects.action.State.SUCCEEDED})
        self.assertEqual(self.context, action._context)

    @mock.patch.object(db_api.Connection, 'get_action_by_uuid')
    def test_refresh(self, mock_get_action):
        returns = [dict(self.fake_action, state="first state"),
                   dict(self.fake_action, state="second state")]
        mock_get_action.side_effect = returns
        uuid = self.fake_action['uuid']
        expected = [mock.call(self.context, uuid, eager=self.eager),
                    mock.call(self.context, uuid, eager=self.eager)]
        action = objects.Action.get(self.context, uuid, eager=self.eager)
        self.assertEqual("first state", action.state)
        action.refresh(eager=self.eager)
        self.assertEqual("second state", action.state)
        self.assertEqual(expected, mock_get_action.call_args_list)
        self.assertEqual(self.context, action._context)
        self.eager_action_assert(action)


class TestCreateDeleteActionObject(base.DbTestCase):

    def setUp(self):
        super(TestCreateDeleteActionObject, self).setUp()
        self.fake_strategy = utils.create_test_strategy(name="DUMMY")
        self.fake_audit = utils.create_test_audit()
        self.fake_action_plan = utils.create_test_action_plan()
        self.fake_action = utils.get_test_action()

    @mock.patch.object(db_api.Connection, 'create_action')
    def test_create(self, mock_create_action):
        mock_create_action.return_value = self.fake_action
        action = objects.Action(self.context, **self.fake_action)
        action.create()

        mock_create_action.assert_called_once_with(self.fake_action)
        self.assertEqual(self.context, action._context)

    @mock.patch.object(db_api.Connection, 'update_action')
    @mock.patch.object(db_api.Connection, 'soft_delete_action')
    @mock.patch.object(db_api.Connection, 'get_action_by_uuid')
    def test_soft_delete(self, mock_get_action,
                         mock_soft_delete_action, mock_update_action):
        mock_get_action.return_value = self.fake_action
        uuid = self.fake_action['uuid']
        action = objects.Action.get_by_uuid(self.context, uuid)
        action.soft_delete()
        mock_get_action.assert_called_once_with(
            self.context, uuid, eager=False)
        mock_soft_delete_action.assert_called_once_with(uuid)
        mock_update_action.assert_called_once_with(
            uuid, {'state': objects.action.State.DELETED})
        self.assertEqual(self.context, action._context)

    @mock.patch.object(db_api.Connection, 'destroy_action')
    @mock.patch.object(db_api.Connection, 'get_action_by_uuid')
    def test_destroy(self, mock_get_action, mock_destroy_action):
        mock_get_action.return_value = self.fake_action
        uuid = self.fake_action['uuid']
        action = objects.Action.get_by_uuid(self.context, uuid)
        action.destroy()

        mock_get_action.assert_called_once_with(
            self.context, uuid, eager=False)
        mock_destroy_action.assert_called_once_with(uuid)
        self.assertEqual(self.context, action._context)
