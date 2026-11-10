"""Read-only comparison on fresh analyses; never dispatches a ChainPlan."""
from src.core.path_planner import plan_route
from src.core.persistent_route import PersistentRoute
from src.core.chain_plan import propose_chain,priority_rank


class ShadowPlanner:
    def __init__(self):self.route=PersistentRoute()

    def observe(self,frame,result,priorities,mode='smart',excluded=(),memory=None):
        old=plan_route(result,priorities,mode,excluded)
        persistent=self.route.choose(frame,result,priorities,mode,excluded,memory)
        chain=propose_chain(frame,result,persistent,priorities,self.route.map_id,self.route.revision) if self.route.map_id else None
        nodes={n.id:n for n in result.nodes}
        ids=chain.ordered_nodes if chain else []
        return dict(map_id=self.route.map_id,map_revision=self.route.revision,intent='priority' if priorities else 'fast_level',
            old_choice=old.next_node,persistent_choice=persistent.next_node,chain_choice=chain.terminal if chain else None,
            chain_items=[dict(node=i,icon=nodes[i].icon_id,type=nodes[i].type.value,rank=priority_rank(nodes[i],priorities)) for i in ids],
            extra_items=[i for i in ids if priority_rank(nodes[i],priorities)==len(priorities)],
            higher_priority_risk=bool(chain and 'higher_priority_outside_chain' in chain.blockers),
            hypothetical_saved_commands=max(0,len(ids)-1),executable_saved_commands=0,
            reason=persistent.reason,route=self.route.summary(frame,result),chain=chain.summary() if chain else None,
            dispatch=False,center='separate_unverified_candidate' if not priorities else 'excluded_by_priority_intent',
            bulk_auto_bloodweb='separate_unverified_mode')
