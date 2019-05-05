# -*- encoding: utf-8 -*-
# Copyright 2013 Red Hat, Inc.
# All Rights Reserved.
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

"""
An :ref:`Audit <audit_definition>` may be launched several times with the same
settings (:ref:`Goal <goal_definition>`, thresholds, ...). Therefore it makes
sense to save those settings in some sort of Audit preset object, which is
known as an :ref:`Audit Template <audit_template_definition>`.

An :ref:`Audit Template <audit_template_definition>` contains at least the
:ref:`Goal <goal_definition>` of the :ref:`Audit <audit_definition>`.

It may also contain some error handling settings indicating whether:

-  :ref:`Watcher Applier <watcher_applier_definition>` stops the
   entire operation
-  :ref:`Watcher Applier <watcher_applier_definition>` performs a rollback

and how many retries should be attempted before failure occurs (also the latter
can be complex: for example the scenario in which there are many first-time
failures on ultimately successful :ref:`Actions <action_definition>`).

Moreover, an :ref:`Audit Template <audit_template_definition>` may contain some
settings related to the level of automation for the
:ref:`Action Plan <action_plan_definition>` that will be generated by the
:ref:`Audit <audit_definition>`.
A flag will indicate whether the :ref:`Action Plan <action_plan_definition>`
will be launched automatically or will need a manual confirmation from the
:ref:`Administrator <administrator_definition>`.
"""

import datetime

import pecan
from pecan import rest
import wsme
from wsme import types as wtypes
import wsmeext.pecan as wsme_pecan

from watcher._i18n import _
from watcher.api.controllers import base
from watcher.api.controllers import link
from watcher.api.controllers.v1 import collection
from watcher.api.controllers.v1 import types
from watcher.api.controllers.v1 import utils as api_utils
from watcher.common import context as context_utils
from watcher.common import exception
from watcher.common import policy
from watcher.common import utils as common_utils
from watcher.decision_engine.loading import default as default_loading
from watcher import objects


def hide_fields_in_newer_versions(obj):
    """This method hides fields that were added in newer API versions.

    Certain node fields were introduced at certain API versions.
    These fields are only made available when the request's API version
    matches or exceeds the versions when these fields were introduced.
    """
    pass


class AuditTemplatePostType(wtypes.Base):
    _ctx = context_utils.make_context()

    name = wtypes.wsattr(wtypes.text, mandatory=True)
    """Name of this audit template"""

    description = wtypes.wsattr(wtypes.text, mandatory=False)
    """Short description of this audit template"""

    goal = wtypes.wsattr(wtypes.text, mandatory=True)
    """Goal UUID or name of the audit template"""

    strategy = wtypes.wsattr(wtypes.text, mandatory=False)
    """Strategy UUID or name of the audit template"""

    scope = wtypes.wsattr(types.jsontype, mandatory=False, default=[])
    """Audit Scope"""

    def as_audit_template(self):
        return AuditTemplate(
            name=self.name,
            description=self.description,
            goal_id=self.goal,  # Dirty trick ...
            goal=self.goal,
            strategy_id=self.strategy,  # Dirty trick ...
            strategy_uuid=self.strategy,
            scope=self.scope,
        )

    @staticmethod
    def _build_schema():
        SCHEMA = {
            "$schema": "http://json-schema.org/draft-04/schema#",
            "type": "array",
            "items": {
                "type": "object",
                "properties": AuditTemplatePostType._get_schemas(),
                "additionalProperties": False
            }
        }
        return SCHEMA

    @staticmethod
    def _get_schemas():
        collectors = default_loading.ClusterDataModelCollectorLoader(
            ).list_available()
        schemas = {k: c.SCHEMA for k, c
                   in collectors.items() if hasattr(c, "SCHEMA")}
        return schemas

    @staticmethod
    def validate(audit_template):
        available_goals = objects.Goal.list(AuditTemplatePostType._ctx)
        available_goal_uuids_map = {g.uuid: g for g in available_goals}
        available_goal_names_map = {g.name: g for g in available_goals}
        if audit_template.goal in available_goal_uuids_map:
            goal = available_goal_uuids_map[audit_template.goal]
        elif audit_template.goal in available_goal_names_map:
            goal = available_goal_names_map[audit_template.goal]
        else:
            raise exception.InvalidGoal(goal=audit_template.goal)

        if audit_template.scope:
            common_utils.Draft4Validator(
                AuditTemplatePostType._build_schema()
                ).validate(audit_template.scope)

            include_host_aggregates = False
            exclude_host_aggregates = False
            for rule in audit_template.scope[0]['compute']:
                if 'host_aggregates' in rule:
                    include_host_aggregates = True
                elif 'exclude' in rule:
                    for resource in rule['exclude']:
                        if 'host_aggregates' in resource:
                            exclude_host_aggregates = True
            if include_host_aggregates and exclude_host_aggregates:
                raise exception.Invalid(
                    message=_(
                        "host_aggregates can't be "
                        "included and excluded together"))

        if audit_template.strategy:
            available_strategies = objects.Strategy.list(
                AuditTemplatePostType._ctx)
            available_strategies_map = {
                s.uuid: s for s in available_strategies}
            if audit_template.strategy not in available_strategies_map:
                raise exception.InvalidStrategy(
                    strategy=audit_template.strategy)

            strategy = available_strategies_map[audit_template.strategy]
            # Check that the strategy we indicate is actually related to the
            # specified goal
            if strategy.goal_id != goal.id:
                choices = ["'%s' (%s)" % (s.uuid, s.name)
                           for s in available_strategies]
                raise exception.InvalidStrategy(
                    message=_(
                        "'%(strategy)s' strategy does relate to the "
                        "'%(goal)s' goal. Possible choices: %(choices)s")
                    % dict(strategy=strategy.name, goal=goal.name,
                           choices=", ".join(choices)))
            audit_template.strategy = strategy.uuid

        # We force the UUID so that we do not need to query the DB with the
        # name afterwards
        audit_template.goal = goal.uuid

        return audit_template


class AuditTemplatePatchType(types.JsonPatchType):

    _ctx = context_utils.make_context()

    @staticmethod
    def mandatory_attrs():
        return []

    @staticmethod
    def validate(patch):
        if patch.path == "/goal" and patch.op != "remove":
            AuditTemplatePatchType._validate_goal(patch)
        elif patch.path == "/goal" and patch.op == "remove":
            raise exception.OperationNotPermitted(
                _("Cannot remove 'goal' attribute "
                  "from an audit template"))
        if patch.path == "/strategy":
            AuditTemplatePatchType._validate_strategy(patch)
        return types.JsonPatchType.validate(patch)

    @staticmethod
    def _validate_goal(patch):
        patch.path = "/goal_id"
        goal = patch.value

        if goal:
            available_goals = objects.Goal.list(
                AuditTemplatePatchType._ctx)
            available_goal_uuids_map = {g.uuid: g for g in available_goals}
            available_goal_names_map = {g.name: g for g in available_goals}
            if goal in available_goal_uuids_map:
                patch.value = available_goal_uuids_map[goal].id
            elif goal in available_goal_names_map:
                patch.value = available_goal_names_map[goal].id
            else:
                raise exception.InvalidGoal(goal=goal)

    @staticmethod
    def _validate_strategy(patch):
        patch.path = "/strategy_id"
        strategy = patch.value
        if strategy:
            available_strategies = objects.Strategy.list(
                AuditTemplatePatchType._ctx)
            available_strategy_uuids_map = {
                s.uuid: s for s in available_strategies}
            available_strategy_names_map = {
                s.name: s for s in available_strategies}
            if strategy in available_strategy_uuids_map:
                patch.value = available_strategy_uuids_map[strategy].id
            elif strategy in available_strategy_names_map:
                patch.value = available_strategy_names_map[strategy].id
            else:
                raise exception.InvalidStrategy(strategy=strategy)


class AuditTemplate(base.APIBase):
    """API representation of a audit template.

    This class enforces type checking and value constraints, and converts
    between the internal object model and the API representation of an
    audit template.
    """

    _goal_uuid = None
    _goal_name = None

    _strategy_uuid = None
    _strategy_name = None

    def _get_goal(self, value):
        if value == wtypes.Unset:
            return None
        goal = None
        try:
            if (common_utils.is_uuid_like(value) or
                    common_utils.is_int_like(value)):
                goal = objects.Goal.get(
                    pecan.request.context, value)
            else:
                goal = objects.Goal.get_by_name(
                    pecan.request.context, value)
        except exception.GoalNotFound:
            pass
        if goal:
            self.goal_id = goal.id
        return goal

    def _get_strategy(self, value):
        if value == wtypes.Unset:
            return None
        strategy = None
        try:
            if (common_utils.is_uuid_like(value) or
                    common_utils.is_int_like(value)):
                strategy = objects.Strategy.get(
                    pecan.request.context, value)
            else:
                strategy = objects.Strategy.get_by_name(
                    pecan.request.context, value)
        except exception.StrategyNotFound:
            pass
        if strategy:
            self.strategy_id = strategy.id
        return strategy

    def _get_goal_uuid(self):
        return self._goal_uuid

    def _set_goal_uuid(self, value):
        if value and self._goal_uuid != value:
            self._goal_uuid = None
            goal = self._get_goal(value)
            if goal:
                self._goal_uuid = goal.uuid

    def _get_strategy_uuid(self):
        return self._strategy_uuid

    def _set_strategy_uuid(self, value):
        if value and self._strategy_uuid != value:
            self._strategy_uuid = None
            strategy = self._get_strategy(value)
            if strategy:
                self._strategy_uuid = strategy.uuid

    def _get_goal_name(self):
        return self._goal_name

    def _set_goal_name(self, value):
        if value and self._goal_name != value:
            self._goal_name = None
            goal = self._get_goal(value)
            if goal:
                self._goal_name = goal.name

    def _get_strategy_name(self):
        return self._strategy_name

    def _set_strategy_name(self, value):
        if value and self._strategy_name != value:
            self._strategy_name = None
            strategy = self._get_strategy(value)
            if strategy:
                self._strategy_name = strategy.name

    uuid = wtypes.wsattr(types.uuid, readonly=True)
    """Unique UUID for this audit template"""

    name = wtypes.text
    """Name of this audit template"""

    description = wtypes.wsattr(wtypes.text, mandatory=False)
    """Short description of this audit template"""

    goal_uuid = wtypes.wsproperty(
        wtypes.text, _get_goal_uuid, _set_goal_uuid, mandatory=True)
    """Goal UUID the audit template refers to"""

    goal_name = wtypes.wsproperty(
        wtypes.text, _get_goal_name, _set_goal_name, mandatory=False)
    """The name of the goal this audit template refers to"""

    strategy_uuid = wtypes.wsproperty(
        wtypes.text, _get_strategy_uuid, _set_strategy_uuid, mandatory=False)
    """Strategy UUID the audit template refers to"""

    strategy_name = wtypes.wsproperty(
        wtypes.text, _get_strategy_name, _set_strategy_name, mandatory=False)
    """The name of the strategy this audit template refers to"""

    audits = wtypes.wsattr([link.Link], readonly=True)
    """Links to the collection of audits contained in this audit template"""

    links = wtypes.wsattr([link.Link], readonly=True)
    """A list containing a self link and associated audit template links"""

    scope = wtypes.wsattr(types.jsontype, mandatory=False)
    """Audit Scope"""

    def __init__(self, **kwargs):
        super(AuditTemplate, self).__init__()
        self.fields = []
        fields = list(objects.AuditTemplate.fields)

        for k in fields:
            # Skip fields we do not expose.
            if not hasattr(self, k):
                continue
            self.fields.append(k)
            setattr(self, k, kwargs.get(k, wtypes.Unset))

        self.fields.append('goal_id')
        self.fields.append('strategy_id')
        setattr(self, 'strategy_id', kwargs.get('strategy_id', wtypes.Unset))

        # goal_uuid & strategy_uuid are not part of
        # objects.AuditTemplate.fields because they're API-only attributes.
        self.fields.append('goal_uuid')
        self.fields.append('goal_name')
        self.fields.append('strategy_uuid')
        self.fields.append('strategy_name')
        setattr(self, 'goal_uuid', kwargs.get('goal_id', wtypes.Unset))
        setattr(self, 'goal_name', kwargs.get('goal_id', wtypes.Unset))
        setattr(self, 'strategy_uuid',
                kwargs.get('strategy_id', wtypes.Unset))
        setattr(self, 'strategy_name',
                kwargs.get('strategy_id', wtypes.Unset))

    @staticmethod
    def _convert_with_links(audit_template, url, expand=True):
        if not expand:
            audit_template.unset_fields_except(
                ['uuid', 'name', 'goal_uuid', 'goal_name',
                 'scope', 'strategy_uuid', 'strategy_name'])

        # The numeric ID should not be exposed to
        # the user, it's internal only.
        audit_template.goal_id = wtypes.Unset
        audit_template.strategy_id = wtypes.Unset

        audit_template.links = [link.Link.make_link('self', url,
                                                    'audit_templates',
                                                    audit_template.uuid),
                                link.Link.make_link('bookmark', url,
                                                    'audit_templates',
                                                    audit_template.uuid,
                                                    bookmark=True)]
        return audit_template

    @classmethod
    def convert_with_links(cls, rpc_audit_template, expand=True):
        audit_template = AuditTemplate(**rpc_audit_template.as_dict())
        hide_fields_in_newer_versions(audit_template)
        return cls._convert_with_links(audit_template, pecan.request.host_url,
                                       expand)

    @classmethod
    def sample(cls, expand=True):
        sample = cls(uuid='27e3153e-d5bf-4b7e-b517-fb518e17f34c',
                     name='My Audit Template',
                     description='Description of my audit template',
                     goal_uuid='83e44733-b640-40e2-8d8a-7dd3be7134e6',
                     strategy_uuid='367d826e-b6a4-4b70-bc44-c3f6fe1c9986',
                     created_at=datetime.datetime.utcnow(),
                     deleted_at=None,
                     updated_at=datetime.datetime.utcnow(),
                     scope=[],)
        return cls._convert_with_links(sample, 'http://localhost:9322', expand)


class AuditTemplateCollection(collection.Collection):
    """API representation of a collection of audit templates."""

    audit_templates = [AuditTemplate]
    """A list containing audit templates objects"""

    def __init__(self, **kwargs):
        super(AuditTemplateCollection, self).__init__()
        self._type = 'audit_templates'

    @staticmethod
    def convert_with_links(rpc_audit_templates, limit, url=None, expand=False,
                           **kwargs):
        at_collection = AuditTemplateCollection()
        at_collection.audit_templates = [
            AuditTemplate.convert_with_links(p, expand)
            for p in rpc_audit_templates]
        at_collection.next = at_collection.get_next(limit, url=url, **kwargs)
        return at_collection

    @classmethod
    def sample(cls):
        sample = cls()
        sample.audit_templates = [AuditTemplate.sample(expand=False)]
        return sample


class AuditTemplatesController(rest.RestController):
    """REST controller for AuditTemplates."""
    def __init__(self):
        super(AuditTemplatesController, self).__init__()

    from_audit_templates = False
    """A flag to indicate if the requests to this controller are coming
    from the top-level resource AuditTemplates."""

    _custom_actions = {
        'detail': ['GET'],
    }

    def _get_audit_templates_collection(self, filters, marker, limit,
                                        sort_key, sort_dir, expand=False,
                                        resource_url=None):
        additional_fields = ["goal_uuid", "goal_name", "strategy_uuid",
                             "strategy_name"]

        api_utils.validate_sort_key(
            sort_key, list(objects.AuditTemplate.fields) + additional_fields)
        api_utils.validate_search_filters(
            filters, list(objects.AuditTemplate.fields) + additional_fields)
        limit = api_utils.validate_limit(limit)
        api_utils.validate_sort_dir(sort_dir)

        marker_obj = None
        if marker:
            marker_obj = objects.AuditTemplate.get_by_uuid(
                pecan.request.context,
                marker)

        need_api_sort = api_utils.check_need_api_sort(sort_key,
                                                      additional_fields)
        sort_db_key = (sort_key if not need_api_sort
                       else None)

        audit_templates = objects.AuditTemplate.list(
            pecan.request.context, filters, limit, marker_obj,
            sort_key=sort_db_key, sort_dir=sort_dir)

        audit_templates_collection = \
            AuditTemplateCollection.convert_with_links(
                audit_templates, limit, url=resource_url, expand=expand,
                sort_key=sort_key, sort_dir=sort_dir)

        if need_api_sort:
            api_utils.make_api_sort(
                audit_templates_collection.audit_templates, sort_key,
                sort_dir)

        return audit_templates_collection

    @wsme_pecan.wsexpose(AuditTemplateCollection, wtypes.text, wtypes.text,
                         types.uuid, int, wtypes.text, wtypes.text)
    def get_all(self, goal=None, strategy=None, marker=None,
                limit=None, sort_key='id', sort_dir='asc'):
        """Retrieve a list of audit templates.

        :param goal: goal UUID or name to filter by
        :param strategy: strategy UUID or name to filter by
        :param marker: pagination marker for large data sets.
        :param limit: maximum number of resources to return in a single result.
        :param sort_key: column to sort results by. Default: id.
        :param sort_dir: direction to sort. "asc" or "desc". Default: asc.
        """
        context = pecan.request.context
        policy.enforce(context, 'audit_template:get_all',
                       action='audit_template:get_all')
        filters = {}
        if goal:
            if common_utils.is_uuid_like(goal):
                filters['goal_uuid'] = goal
            else:
                filters['goal_name'] = goal

        if strategy:
            if common_utils.is_uuid_like(strategy):
                filters['strategy_uuid'] = strategy
            else:
                filters['strategy_name'] = strategy

        return self._get_audit_templates_collection(
            filters, marker, limit, sort_key, sort_dir)

    @wsme_pecan.wsexpose(AuditTemplateCollection, wtypes.text, wtypes.text,
                         types.uuid, int, wtypes.text, wtypes.text)
    def detail(self, goal=None, strategy=None, marker=None,
               limit=None, sort_key='id', sort_dir='asc'):
        """Retrieve a list of audit templates with detail.

        :param goal: goal UUID or name to filter by
        :param strategy: strategy UUID or name to filter by
        :param marker: pagination marker for large data sets.
        :param limit: maximum number of resources to return in a single result.
        :param sort_key: column to sort results by. Default: id.
        :param sort_dir: direction to sort. "asc" or "desc". Default: asc.
        """
        context = pecan.request.context
        policy.enforce(context, 'audit_template:detail',
                       action='audit_template:detail')

        # NOTE(lucasagomes): /detail should only work agaist collections
        parent = pecan.request.path.split('/')[:-1][-1]
        if parent != "audit_templates":
            raise exception.HTTPNotFound

        filters = {}
        if goal:
            if common_utils.is_uuid_like(goal):
                filters['goal_uuid'] = goal
            else:
                filters['goal_name'] = goal

        if strategy:
            if common_utils.is_uuid_like(strategy):
                filters['strategy_uuid'] = strategy
            else:
                filters['strategy_name'] = strategy

        expand = True
        resource_url = '/'.join(['audit_templates', 'detail'])
        return self._get_audit_templates_collection(filters, marker, limit,
                                                    sort_key, sort_dir, expand,
                                                    resource_url)

    @wsme_pecan.wsexpose(AuditTemplate, wtypes.text)
    def get_one(self, audit_template):
        """Retrieve information about the given audit template.

        :param audit_template: UUID or name of an audit template.
        """
        if self.from_audit_templates:
            raise exception.OperationNotPermitted

        context = pecan.request.context
        rpc_audit_template = api_utils.get_resource('AuditTemplate',
                                                    audit_template)
        policy.enforce(context, 'audit_template:get', rpc_audit_template,
                       action='audit_template:get')

        return AuditTemplate.convert_with_links(rpc_audit_template)

    @wsme.validate(types.uuid, AuditTemplatePostType)
    @wsme_pecan.wsexpose(AuditTemplate, body=AuditTemplatePostType,
                         status_code=201)
    def post(self, audit_template_postdata):
        """Create a new audit template.

        :param audit_template_postdata: the audit template POST data
                                        from the request body.
        """
        if self.from_audit_templates:
            raise exception.OperationNotPermitted

        context = pecan.request.context
        policy.enforce(context, 'audit_template:create',
                       action='audit_template:create')

        context = pecan.request.context
        audit_template = audit_template_postdata.as_audit_template()
        audit_template_dict = audit_template.as_dict()
        new_audit_template = objects.AuditTemplate(context,
                                                   **audit_template_dict)
        new_audit_template.create()

        # Set the HTTP Location Header
        pecan.response.location = link.build_url(
            'audit_templates', new_audit_template.uuid)
        return AuditTemplate.convert_with_links(new_audit_template)

    @wsme.validate(types.uuid, [AuditTemplatePatchType])
    @wsme_pecan.wsexpose(AuditTemplate, wtypes.text,
                         body=[AuditTemplatePatchType])
    def patch(self, audit_template, patch):
        """Update an existing audit template.

        :param template_uuid: UUID of a audit template.
        :param patch: a json PATCH document to apply to this audit template.
        """
        if self.from_audit_templates:
            raise exception.OperationNotPermitted

        context = pecan.request.context
        audit_template_to_update = api_utils.get_resource('AuditTemplate',
                                                          audit_template)
        policy.enforce(context, 'audit_template:update',
                       audit_template_to_update,
                       action='audit_template:update')

        if common_utils.is_uuid_like(audit_template):
            audit_template_to_update = objects.AuditTemplate.get_by_uuid(
                pecan.request.context,
                audit_template)
        else:
            audit_template_to_update = objects.AuditTemplate.get_by_name(
                pecan.request.context,
                audit_template)

        try:
            audit_template_dict = audit_template_to_update.as_dict()
            audit_template = AuditTemplate(**api_utils.apply_jsonpatch(
                audit_template_dict, patch))
        except api_utils.JSONPATCH_EXCEPTIONS as e:
            raise exception.PatchError(patch=patch, reason=e)

        # Update only the fields that have changed
        for field in objects.AuditTemplate.fields:
            try:
                patch_val = getattr(audit_template, field)
            except AttributeError:
                # Ignore fields that aren't exposed in the API
                continue
            if patch_val == wtypes.Unset:
                patch_val = None
            if audit_template_to_update[field] != patch_val:
                audit_template_to_update[field] = patch_val

        audit_template_to_update.save()
        return AuditTemplate.convert_with_links(audit_template_to_update)

    @wsme_pecan.wsexpose(None, wtypes.text, status_code=204)
    def delete(self, audit_template):
        """Delete a audit template.

        :param template_uuid: UUID or name of an audit template.
        """
        context = pecan.request.context
        audit_template_to_delete = api_utils.get_resource('AuditTemplate',
                                                          audit_template)
        policy.enforce(context, 'audit_template:delete',
                       audit_template_to_delete,
                       action='audit_template:delete')

        audit_template_to_delete.soft_delete()
