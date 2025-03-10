from ros.lib.models import Rule, RhAccount, System, db, PerformanceProfile
from ros.lib.utils import is_valid_uuid, identity
from ros.lib.config import INSTANCE_PRICE_UNIT
from flask_restful import Resource, abort, fields, marshal_with
from flask import request

ROSSUMMARY = dict(
    OPTIMIZED='System is OPTIMIZED',
    MEMORY_OVERSIZED='Memory utilization is very low',
    MEMORY_UNDERSIZED='Memory utilization is too high',
    MEMORY_UNDERSIZED_BY_PRESSURE='System is suffering from memory pressure',
    CPU_OVERSIZED='CPU utilization is very low',
    CPU_UNDERSIZED='CPU utilization is too high',
    CPU_UNDERSIZED_BY_PRESSURE='System is suffering from CPU pressure',
    IO_OVERSIZED='I/O utilization is very low',
    IO_UNDERSIZED='I/O utilization is too high',
    IO_UNDERSIZED_BY_PRESSURE='System is suffering from IO pressure',
    IDLE='System is IDLE',
)


class RecommendationsApi(Resource):

    recommendation_fields = {
        'rule_id':  fields.String,
        'description': fields.String,
        'reason': fields.String,
        'resolution': fields.String,
        'condition': fields.String,
        'detected_issues': fields.String,
        'suggested_instances': fields.String,
        'current_instance': fields.String
    }

    meta_fields = {
        'count': fields.Integer
    }

    data_fields = {
        'inventory_id': fields.String,
        'meta': fields.Nested(meta_fields),
        'data': fields.List(fields.Nested(recommendation_fields))
    }

    @marshal_with(data_fields)
    def get(self, host_id):
        if not is_valid_uuid(host_id):
            abort(404, message='Invalid host_id, Id should be in form of UUID4')

        ident = identity(request)['identity']

        filter_description = request.args.get('description')

        tenant_query = db.session.query(RhAccount.id).filter(RhAccount.org_id == ident['org_id']).subquery()
        system = db.session.query(System) \
            .filter(System.tenant_id.in_(tenant_query)).filter(System.inventory_id == host_id).first()

        if not system:
            abort(404, message="host with id {} doesn't exist"
                  .format(host_id))

        profile = PerformanceProfile.query.filter_by(system_id=system.id).first()
        if not profile:
            abort(
                404,
                message="No records for host with id {} doesn't exist".format(host_id))

        rule_hits = profile.rule_hit_details
        recommendations_list = []
        rules_columns = ['rule_id', 'description', 'reason', 'resolution', 'condition']
        if rule_hits:
            for rule_hit in rule_hits:
                if filter_description:
                    rule_data = db.session.query(Rule).filter(Rule.rule_id == rule_hit['rule_id'])\
                                .filter(Rule.description.ilike(f'%{filter_description}%')).first()
                else:
                    rule_data = db.session.query(Rule).filter(Rule.rule_id == rule_hit['rule_id']).first()
                if rule_data:
                    rule_dict = rule_data.__dict__
                    if system.cloud_provider is None:
                        rule_dict['reason'] = rule_dict['reason'].replace("cloud_provider.upper()", "cloud_provider")
                    recommendation = {}
                    rule_hit_details = rule_hit.get('details')
                    candidates = rule_hit_details.get('candidates')
                    states = rule_hit_details.get('states')
                    summaries = [
                        ROSSUMMARY[state] for substates in states.values()
                        for state in substates
                        if ROSSUMMARY.get(state) is not None
                    ]
                    if rule_hit.get("key") == 'INSTANCE_IDLE':
                        summaries = None
                    current_instance = f'{rule_hit_details.get("instance_type")} ' + \
                        f'({rule_hit_details.get("price")} {INSTANCE_PRICE_UNIT})'
                    newline = '\n'
                    for skey in rules_columns:
                        formatted_candidates = []
                        if summaries is not None:
                            recommendation['detected_issues'] = newline.join(summaries)
                        for candidate in candidates[0:3]:
                            formatted_candidates.append(f'{candidate[0]} ({candidate[1]} {INSTANCE_PRICE_UNIT})')
                        recommendation['suggested_instances'] = newline.join(formatted_candidates)
                        recommendation['current_instance'] = current_instance
                        recommendation[skey] = eval("f'{}'".format(rule_dict[skey]))
                    recommendations_list.append(recommendation)
        return {
                  'inventory_id': system.inventory_id,
                  'data': recommendations_list,
                  'meta': {'count': len(recommendations_list)}
            }
