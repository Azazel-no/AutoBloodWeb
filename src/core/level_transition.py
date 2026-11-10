"""New-level evidence uses fresh geometry and positively identified changes."""
import math
from src.core.models import NodeState,ScreenState
from src.core.scene_memory import layout_changed,same_window

def complete(result):
    nodes=[n for n in result.nodes if n.state!=NodeState.CENTER]
    return result.screen_state==ScreenState.BLOODWEB and bool(nodes) and all(n.state in (NodeState.PURCHASED,NodeState.ABSORBED) for n in nodes)

def final_target(result,node):
    remaining=[n for n in result.nodes if n.state not in (NodeState.CENTER,NodeState.PURCHASED,NodeState.ABSORBED)]
    return len(remaining)==1 and remaining[0].id==node.id and node.state==NodeState.AVAILABLE

def new_level(record,frame,result):
    if result.screen_state!=ScreenState.BLOODWEB or not same_window(record,frame):return False
    if header_transition(record,frame,result):return True
    if layout_changed(record,frame,result):return True
    return known_glyph_transition(record,frame,result)

def header_transition(record,frame,result):
    if result.screen_state!=ScreenState.BLOODWEB or not same_window(record,frame):return False
    old_level=record.get('level')
    if (type(old_level) is int and old_level<50 and result.level==old_level+1 and
            record.get('level_confidence',0)>=.95 and result.level_confidence>=.95 and
            result.nodes and sum(n.state in (NodeState.PURCHASED,NodeState.ABSORBED) for n in result.nodes)<=len(result.nodes)*.2):return True
    return False



def level_header_moved(record,previous,result):
    """Two independent observations read the same next level in the header.

    Used for a pending click whose old point now holds a similar node on the new
    level: the header, not the node, proves the old level is gone.
    """
    old_level=record.get('level')
    if type(old_level) is not int or not previous or previous[1] is result:return False
    observations=(previous[1],result)
    def identified(r):
        # The new map's identity must be proven too, not only its header.
        items=[n for n in r.nodes if n.state!=NodeState.CENTER]
        return bool(items) and sum(n.icon_id is not None for n in items)>=.8*len(items)
    return (all(r.screen_state==ScreenState.BLOODWEB and r.level==old_level+1 and r.level_confidence>=.95 and identified(r)
                for r in observations) and same_window(record,previous[0]))

def pristine_next_level(record,frame,result):
    """A new physical layout and fresh glyphs retire an old action, with no credit."""
    if (not frame.live or frame.probe_only or not frame.window.foreground or
            not header_transition(record,frame,result) or not layout_changed(record,frame,result)):return False
    items=[n for n in result.nodes if n.state!=NodeState.CENTER]
    if not items or any(n.state not in (NodeState.AVAILABLE,NodeState.LOCKED) or
                       n.state_confidence<.75 or n.geometry_confidence<.65 for n in items):return False
    known=[n for n in items if n.icon_id and n.confidence>=.86 and n.contour_score>=.68 and n.icon_margin>=.035]
    return len(known)>=4 and len({n.icon_id for n in known})>=3


def absent_single_on_next_level(record,frame,result):
    """An absent old target plus two fresh next-level proofs may retire input.

    This certifies a transition, never a purchase. A reused target position,
    a group or a native chain still needs its stronger dedicated evidence.
    """
    pending=record.get('pending') or {}
    if (not pending.get('point') or any(pending.get(k) for k in ('chain','batch','automatic','chain_blocked_reason'))
            or not frame.live or frame.probe_only or not frame.window.foreground
            or not header_transition(record,frame,result) or not layout_changed(record,frame,result)):return False
    old=[n for n in record['nodes'] if n['state']!=NodeState.CENTER]
    if not old or sum(n['state'] in (NodeState.PURCHASED,NodeState.ABSORBED) for n in old)<len(old)*.7:return False
    from src.core.purchase_outcome import physical_node
    if physical_node(frame,result,pending['point'],pending.get('radius',20)) is not None:return False
    items=[n for n in result.nodes if n.state!=NodeState.CENTER]
    return bool(items) and all(n.state in (NodeState.AVAILABLE,NodeState.LOCKED) and
        n.state_confidence>=.75 and n.geometry_confidence>=.65 for n in items)


def idle_forward_transition(record,frame,result):
    """Retire a resolved scene after manual advancement; credit no purchases.

    A pending action is deliberately excluded. The caller needs two independent
    agreeing captures, each satisfying this certificate, also after a restart.
    """
    old=record.get('level');new=result.level
    if (record.get('pending') or result.screen_state!=ScreenState.BLOODWEB or not same_window(record,frame) or
            not frame.live or frame.probe_only or not frame.window.foreground or
            type(old) is not int or type(new) is not int or not 1<=old<new-1<=49 or new>50 or
            record.get('level_confidence',0)<.95 or result.level_confidence<.95):return False
    items=[n for n in result.nodes if n.state!=NodeState.CENTER]
    if (not items or any(n.state not in (NodeState.AVAILABLE,NodeState.LOCKED) or
                        n.state_confidence<.75 or n.geometry_confidence<.65 for n in items)):
        return False
    known=[n for n in items if n.icon_id and n.confidence>=.86 and n.contour_score>=.68 and n.icon_margin>=.035]
    old_ids={n['icon'] for n in record.get('nodes',[]) if n.get('icon')}
    old_items=[n for n in record.get('nodes',[]) if n['state']!=NodeState.CENTER]
    added=sum(not any(math.dist(old_node['point'],frame.to_screen(n.x,n.y))<max(6,old_node['radius']*.25)
                      for old_node in old_items) for n in items)
    # Larger later webs can reuse every slot of a smaller early web. A true
    # expansion is distinct from the Entity removing a subset of old nodes.
    expanded=len(items)>=max(1,len(old_items))*1.5 and added>=4
    changed_symbols=[]
    for node in known:
        matches=[old_node for old_node in old_items if math.dist(old_node['point'],frame.to_screen(node.x,node.y))<5]
        if len(matches)==1 and matches[0].get('icon') and matches[0]['icon']!=node.icon_id:
            changed_symbols.append(node.icon_id)
    # Later webs can reuse the same radial slots. Four positively reidentified
    # positions with three different new glyphs are an alternative to changed
    # slot geometry, only for an already resolved idle scene and new header.
    reidentified=len(changed_symbols)>=4 and len(set(changed_symbols))>=3
    return (len(known)>=4 and len({n.icon_id for n in known}-old_ids)>=3 and
            sum(n.state==NodeState.AVAILABLE and n.geometry_confidence>=.75 for n in known)>=2 and
            (layout_changed(record,frame,result) or expanded or reidentified))


def pending_automatic_on_next_level(record,frame,result):
    """Retire an interrupted centre operation, never credit its old items."""
    pending=record.get('pending') or {}
    skipped=(result.screen_state==ScreenState.BLOODWEB and same_window(record,frame) and
        type(record.get('level')) is int and type(result.level) is int and
        1<=record['level']<result.level-1<=8 and result.level<=9 and
        record.get('level_confidence',0)>=.95 and result.level_confidence>=.95)
    if (pending.get('automatic') is not True or pending.get('chain') or not frame.live or frame.probe_only or
            not frame.window.foreground or not (header_transition(record,frame,result) or skipped)):return False
    centers=[n for n in record.get('nodes',[]) if n['state']==NodeState.CENTER]
    if len(centers)!=1 or math.dist(centers[0]['point'],pending['point'])>5:return False
    items=[n for n in result.nodes if n.state!=NodeState.CENTER]
    if not items or any(n.state not in (NodeState.AVAILABLE,NodeState.LOCKED) for n in items):return False
    known=[n for n in items if n.icon_id and n.confidence>=.86 and n.contour_score>=.68 and n.icon_margin>=.035]
    if len(known)<2:return False
    if skipped:
        old_ids={n['icon'] for n in record.get('nodes',[]) if n.get('icon')}
        # Manual advancement can skip an unobserved intermediate level. Require
        # a pristine map, changed geometry AND three positively new identities;
        # a header alone or a surviving subset cannot retire the pending centre.
        return (len(known)>=4 and len({n.icon_id for n in known}-old_ids)>=3 and
            all(n.geometry_confidence>=.65 for n in known) and
            sum(n.state==NodeState.AVAILABLE and n.geometry_confidence>=.75 for n in known)>=2 and
            layout_changed(record,frame,result))
    changed=0
    for old in record.get('nodes',[]):
        matches=[n for n in known if math.dist(old['point'],frame.to_screen(n.x,n.y))<max(6,old['radius']*.4)]
        if len(matches)==1 and old.get('icon') and matches[0].icon_id!=old['icon']:changed+=1
    return layout_changed(record,frame,result) or changed>=2

def known_glyph_transition(record,frame,result):
    if result.screen_state!=ScreenState.BLOODWEB or not same_window(record,frame):return False
    old=[n for n in record['nodes'] if n['state']!=NodeState.CENTER]
    new=[n for n in result.nodes if n.state!=NodeState.CENTER]
    if not old or not new:return False
    # Lattice slots can recur on the next level. An Entity subset or one
    # state disagreement is insufficient: require several known new glyphs.
    if sum(n['state'] in (NodeState.PURCHASED,NodeState.ABSORBED) for n in old)<len(old)*.7:return False
    if sum(n.state in (NodeState.PURCHASED,NodeState.ABSORBED) for n in new)>len(new)*.2:return False
    changed=compared=0
    # A late full analysis can lose an owned glyph without invalidating its
    # earlier accepted observation. Use that provenance only with the exact
    # next confident native header, and only for structural transition proof.
    historical=[]
    if (record.get('transition_glyphs') and getattr(frame,'live',False) and not getattr(frame,'probe_only',True)
            and frame.window.foreground and header_transition(record,frame,result)):
        historical=[g for g in record.get('transition_glyphs',[]) if not g.get('conflicted') and g.get('icon')
            and g.get('score',0)>=.86 and g.get('contour',0)>=.68 and g.get('margin',0)>=.035
            and g.get('geometry',0)>=.65 and 0<g.get('stamp',0)<=record.get('captured_at',0)]
    for a in old:
        matches=[b for b in new if math.dist(a['point'],frame.to_screen(b.x,b.y))<max(6,a['radius']*.4)]
        glyph=a.get('icon')
        if not glyph:
            proofs=[g for g in historical if math.dist(g['point'],a['point'])<max(8,a['radius']*.2)]
            if len(proofs)==1:glyph=proofs[0]['icon']
        if len(matches)==1 and glyph and matches[0].icon_id:
            compared+=1;changed+=glyph!=matches[0].icon_id
    return changed>=3 and changed>=compared*.6


def pending_target_on_proven_next_level(record,frame,result):
    """Retire an old pending point; never confirm its purchase.

    Lattice positions and even an unknown glyph may recur on the next web.
    Require the exact next confident header plus independent changes in at
    least three known glyphs of a mostly owned old web, a pristine fresh web,
    and a freshly framed available/locked node at the old point. The caller
    still requires two independent consistent captures before clearing it.
    """
    pending=record.get('pending')
    if not frame.live or frame.probe_only or not frame.window.foreground:return False
    if not pending or not header_transition(record,frame,result):return False
    symbols_changed=known_glyph_transition(record,frame,result)
    # An ordinary Unknown target can recur at one lattice slot even when the
    # rest of the web has independently changed. Reuse the existing stronger
    # pristine-layout certificate, not a relaxed glyph-count threshold.
    old=[n for n in record.get('nodes',[]) if n['state']!=NodeState.CENTER]
    pristine_unknown=(not pending.get('icon') and not any(pending.get(k) for k in ('chain','batch','automatic','chain_blocked_reason'))
        and old and sum(n['state'] in (NodeState.PURCHASED,NodeState.ABSORBED) for n in old)>=len(old)*.7
        and pristine_next_level(record,frame,result))
    terminal=terminal_single_on_next_level(record,frame,result)
    if not(symbols_changed or pristine_unknown or terminal):return False
    if any(n.icon_id and (n.confidence<.86 or n.geometry_confidence<.65) for n in result.nodes):return False
    if any(n.state not in (NodeState.CENTER,NodeState.AVAILABLE,NodeState.LOCKED) for n in result.nodes):return False
    from src.core.purchase_outcome import physical_node
    node=physical_node(frame,result,pending['point'],pending['radius'])
    return bool(node and node.state in (NodeState.AVAILABLE,NodeState.LOCKED)
                and node.state_confidence>=.75 and node.geometry_confidence>=.65)


def terminal_single_on_next_level(record,frame,result):
    """Retire a final single from two pristine new maps, never credit purchase.

    A newly Unknown glyph at the old slot cannot identify the old item. The
    independent certificate is the completed old map plus a changed physical
    layout, exact next header, reset states and four accepted new-map glyphs.
    """
    pending=record.get('pending') or {}
    if (not pending.get('point') or any(pending.get(k) for k in ('chain','batch','automatic','prestige','chain_blocked_reason'))
            or result.details.get('confirmation_only') or getattr(frame,'confirmation_only',False)
            or not pristine_next_level(record,frame,result)):
        return False
    old=[n for n in record.get('nodes',[]) if n['state']!=NodeState.CENTER]
    remaining=[n for n in old if n['state'] not in (NodeState.PURCHASED,NodeState.ABSORBED)]
    return (len(old)>=5 and len(remaining)==1 and remaining[0]['state']==NodeState.AVAILABLE
            and math.dist(remaining[0]['point'],pending['point'])<5
            and not(remaining[0].get('icon') and pending.get('icon') and remaining[0]['icon']!=pending['icon']))

def restarted_incomplete_scene(record,frame,result):
    """Independent evidence for a manually advanced web after process restart.

    Old background detections can prevent the old scene from appearing mostly
    owned. Require changed layout AND several positively changed formerly owned
    glyphs, a fresh confident header and reset states; geometry alone cannot pass.
    This retires a scene, never confirms its old purchase.
    """
    if (result.screen_state!=ScreenState.BLOODWEB or not same_window(record,frame) or
            type(result.level) is not int or not 1<=result.level<=50 or result.level_confidence<.95 or
            not layout_changed(record,frame,result)):return False
    if record.get('level')==result.level and record.get('level_confidence',0)>=.95:return False
    new=[n for n in result.nodes if n.state!=NodeState.CENTER]
    if not new or sum(n.state in (NodeState.PURCHASED,NodeState.ABSORBED) for n in new)>len(new)*.2:return False
    changed=compared=0
    for old in record.get('nodes',[]):
        if old['state'] not in (NodeState.PURCHASED,NodeState.ABSORBED) or not old.get('icon'):continue
        matches=[n for n in new if math.dist(old['point'],frame.to_screen(n.x,n.y))<max(6,old['radius']*.4)]
        if len(matches)!=1:continue
        node=matches[0]
        if not node.icon_id or node.confidence<.86 or node.geometry_confidence<.65:continue
        compared+=1
        changed+=node.state in (NodeState.AVAILABLE,NodeState.LOCKED) and old['icon']!=node.icon_id
    if changed<3 or changed<compared*.6:return False
    pending=record.get('pending')
    if pending:
        matches=[n for n in new if math.dist(pending['point'],frame.to_screen(n.x,n.y))<max(6,pending['radius']*.4)]
        if matches and (len(matches)!=1 or not pending.get('icon') or not matches[0].icon_id or
                        matches[0].icon_id==pending['icon'] or matches[0].state not in (NodeState.AVAILABLE,NodeState.LOCKED)):return False
    return True
