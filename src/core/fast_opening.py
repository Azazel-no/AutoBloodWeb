"""A bounded opening queue, checked against fresh local pixels before every input.

No Hough search or library ranking occurs here. A failed check discards the
entire queue; the caller analyzes the same fresh frame through the full detector.
"""
import copy
import hashlib
import time
from dataclasses import asdict
import cv2
import numpy as np
from src.core.models import Analysis, NodeState, NodeType, ScreenState, RecognitionStatus
from src.core.node_classifier import node_glyph, node_mask, rarity_evidence, glyph
from src.core.node_category import classify_category
from src.core.node_validation import validate_candidates
from src.core.path_planner import plan_route, RoutePlan
from src.core.geometry import purchasable


class OpeningFallback(Exception):
    pass


def outside_score_bound(norms, old_norm, new_norm, difference, old_upper, dot_difference=None):
    """Cauchy bound for Dice scores of templates not locally compared.

The initial full ranking bounds every omitted template by old_upper.
Only template norms are used now, never a whole-library image comparison.
"""
    if not len(norms):return 0.
    norms=np.asarray(norms,dtype=np.float64)
    if (not np.isfinite(norms).all() or np.any(norms < 0)
            or not np.isfinite([old_norm,new_norm,difference]).all()):return 1.
    if dot_difference is None:
        upper=(old_upper*(norms+old_norm+1e-6)+2*np.sqrt(norms)*difference)/(norms+new_norm+1e-6)
    else:
        if old_norm<=0 or new_norm<=0 or not np.isfinite(dot_difference):return 1.
        # The old score also bounds the template's projection on the old
        # feature. Separate that known parallel component from the unknown
        # orthogonal one instead of assuming all of delta aligns with T.
        score=np.asarray(old_upper,dtype=np.float64)
        low=np.maximum(0.,score-2e-5)*(norms+old_norm+1e-6)/2
        high=np.minimum(np.sqrt(norms*old_norm),score*(norms+old_norm+1e-6)/2)
        if not np.isfinite(score).all() or np.any(low > high):return 1.
        optimum=np.sqrt(norms)*(old_norm+dot_difference)/np.sqrt(new_norm)
        projection=np.minimum(high,np.maximum(low,optimum))
        delta_perp=max(0.,difference*difference-dot_difference*dot_difference/old_norm)
        remaining=np.maximum(0.,norms-projection*projection/old_norm)
        numerator=2*(projection*(1+dot_difference/old_norm)+np.sqrt(remaining*delta_perp))
        upper=numerator/(norms+new_norm+1e-6)
    return min(1.,float(np.max(upper))+1e-5)


class FastOpening:
    def __init__(self):
        self.active = False
        self.seen = set()
        self.full_scans = self.quick_checks = self.identity_reuses = 0
        self.aborts = {}
        self.local_verifications=0

    @staticmethod
    def context(frame, detector):
        return (frame.window.signature, frame.roi, frame.image.shape,
                frame.level, detector.role, detector.character)

    @staticmethod
    def parts(frame, node, detector):
        r = node.content_radius or node.radius
        patch = frame.image[node.y-r:node.y+r+1, node.x-r:node.x+r+1]
        if patch.shape[:2] != (2*r+1, 2*r+1):
            raise OpeningFallback('crop_changed')
        category = classify_category(patch, r)
        perk = (detector._is_perk(patch, r) and category.category != 'offering'
                and detector._perk_marker(patch, r))
        if perk:
            category.category, category.confidence, category.candidates = 'perk', .95, ['perk']
            # The tile is a diamond. A circular ink mask includes animated
            # scenery outside its sides, especially near the lower web edge.
            yy,xx=np.ogrid[:patch.shape[0],:patch.shape[1]]
            mask=((np.abs(xx-r)+np.abs(yy-r))<r*.88).astype(np.uint8)*255
            feature=glyph(patch,mask,remove_badge=True)
        else:feature=node_glyph(patch,r)
        if feature is None:raise OpeningFallback('glyph_missing')
        rarity, certainty = rarity_evidence(patch, node_mask(r, patch.shape[:2]))
        key = (rarity, None if perk else certainty >= .7, category.category,
               tuple(category.candidates), category.confidence >= .95, perk)
        return feature,key,category,patch

    @staticmethod
    def evidence(frame,node,detector):
        feature,key,_,_=FastOpening.parts(frame,node,detector)
        # Fine and gradient descriptors are functions of this exact normalized
        # feature. Background RGB changes do not supply additional evidence.
        return hashlib.sha256(feature.tobytes()+repr(key).encode()).digest()

    def verify_local(self,frame,node,detector):
        feature,key,category,patch=self.parts(frame,node,detector)
        old_feature,old_key=self.features[node.id]
        owned=node.state==NodeState.PURCHASED
        category_uncertain=False
        if key!=old_key and not owned:
            # Uncertain background shape must not exclude a known library
            # category. Keep rarity/perk evidence unchanged and require a new
            # independently accepted glyph/global margin below; no ID reuse.
            entry=detector.matcher.by_id.get(node.icon_id) if node.icon_id else None
            category_uncertain=bool(entry and key[:2]==old_key[:2] and not key[-1] and not old_key[-1]
                and category.category=='unknown' and entry.category[:-1] in category.candidates)
            if not category_uncertain:raise OpeningFallback('glyph_category_or_rarity_changed')
        if node.type==NodeType.PERK and key[-1] and old_key[-1]:
            old=cv2.GaussianBlur(old_feature,(3,3),.7).astype(np.float32)/255
            fresh=cv2.GaussianBlur(feature,(3,3),.7).astype(np.float32)/255
            temporal=2*float(np.sum(old*fresh))/(float(np.sum(old*old)+np.sum(fresh*fresh))+1e-6)
            if temporal<detector.matcher.threshold or detector.matcher.contour_agreement(old,fresh)<.68:
                raise OpeningFallback('glyph_category_or_rarity_changed')
            self.local_updates[node.id]=(None,None)
            self.certificates.append(dict(node=node.id,fresh_perk_shape=True,temporal_glyph=temporal,icon_id=None))
            return
        if node.icon_id and node.recognition_method not in ('glyph','rarity_tie'):
            raise OpeningFallback('glyph_category_or_rarity_changed')
        ranking=node.top10
        if not ranking:raise OpeningFallback('glyph_category_or_rarity_changed')
        matcher=detector.matcher
        old=cv2.GaussianBlur(old_feature,(3,3),.7).astype(np.float32).ravel()/255
        fresh=cv2.GaussianBlur(feature,(3,3),.7).astype(np.float32).ravel()/255
        old_norm,new_norm=float(np.dot(old,old)),float(np.dot(fresh,fresh))
        difference=float(np.linalg.norm(fresh-old))
        omitted=np.ones(len(matcher.norms),bool);scored=[]
        for ident,_ in ranking:
            index=next(i for i,(e,_) in enumerate(matcher.features) if e.id==ident)
            start=matcher.template_offsets[index]
            end=matcher.template_offsets[index+1] if index+1<len(matcher.features) else len(matcher.templates)
            omitted[start:end]=False
            scores=2*np.einsum('ij,j->i',matcher.templates[start:end],fresh,optimize=False)/(matcher.norms[start:end]+new_norm+1e-6)
            best=start+int(scores.argmax());value=float(scores.max())
            contour=matcher.contour_agreement(fresh.reshape(64,64),matcher.templates[best].reshape(64,64))
            scored.append((value,ident,contour))
        scored.sort(reverse=True)
        initial=getattr(node,'_opening_overlaps',None)
        if initial is None or len(initial)!=len(matcher.norms):raise OpeningFallback('glyph_category_or_rarity_changed')
        upper=outside_score_bound(matcher.norms[omitted],old_norm,new_norm,difference,initial[omitted]+1e-5,float(np.dot(old,fresh-old)))
        best,ident,contour=scored[0]
        margin=best-max(scored[1][0] if len(scored)>1 else 0.,upper)
        certificate=dict(node=node.id,initial_candidates=[i for i,_ in ranking],outside_upper=upper,
                         local_best=best,global_margin_lower=margin,contour=contour)
        if category_uncertain:certificate['fresh_category_uncertain']=True
        self.last_certificate=certificate
        if node.icon_id:
            # A fresh primary decision must independently pass the normal
            # score/contour and global separation, even for an old colour tie.
            accepted=(ident==node.icon_id and best>=matcher.threshold and margin>=matcher.min_margin and contour>=.68)
            if accepted:
                node.confidence=node.glyph_score=round(best,4)
                node.icon_margin=round(margin,4);node.contour_score=round(contour,4)
                node.recognition_method='glyph';node.rarity_tie_break=False
            elif owned:
                conflict=(ident!=node.icon_id and best>=matcher.threshold and margin>=matcher.min_margin and contour>=.68)
                temporal=2*float(np.dot(old,fresh))/(old_norm+new_norm+1e-6)
                temporal_contour=matcher.contour_agreement(old.reshape(64,64),fresh.reshape(64,64))
                if conflict or temporal<matcher.threshold or temporal_contour<.68:raise OpeningFallback('glyph_category_or_rarity_changed')
                # Purchased colour is an observed state, not item rarity. A
                # fresh inconclusive identity becomes Unknown, never a reused ID.
                node.icon_id=None;node.type=NodeType.UNKNOWN;node.confidence=0.;node.glyph_score=0.
                node.icon_margin=0.;node.recognition_status=RecognitionStatus.UNKNOWN
                node.match_reason='owned_identity_unconfirmed'
                certificate.update(identity_cleared=True,temporal_glyph=temporal,temporal_contour=temporal_contour)
            else:raise OpeningFallback('glyph_category_or_rarity_changed')
        else:
            # Never promote Unknown. Prove that the full algorithm cannot
            # accept an omitted candidate or rescue a local one by gradient.
            low=max(best,upper)<matcher.threshold
            negative=(best>upper and margin>=matcher.min_margin and contour<.68 and upper<matcher.threshold)
            ambiguity=False
            if best>upper and upper<matcher.threshold and best-scored[1][0]<matcher.min_margin:
                # The two certainly ranked leading candidates are genuinely
                # close. Verify that neither the colour nor fine-glyph stage
                # can resolve them; no outside candidate can enter the fine
                # shortlist when its certified upper is below its boundary.
                relevant=[v for v in scored if matcher.by_id[v[1]].rarity==key[0]] if key[1] else []
                # Failing to prove a colour acceptance is not evidence of a
                # rejection. Use the two actual local leaders to bound its
                # maximum possible gap; otherwise fall back to the full read.
                color_rejected=not key[1]
                if relevant:
                    rs,ri,rc=relevant[0]
                    color_rejected=(rs>upper and (best-rs>matcher.min_margin
                        or (len(relevant)>1 and rs-relevant[1][0]<matcher.min_margin)))
                fine_safe=False
                if color_rejected and upper<best-matcher.min_margin:
                    sharp=feature.astype(np.float32).ravel()/255;norm=float(np.dot(sharp,sharp));fine=[]
                    for score,other,_ in scored:
                        if score<best-matcher.min_margin:continue
                        i=next(i for i,(e,_) in enumerate(matcher.features) if e.id==other)
                        start=matcher.template_offsets[i];end=matcher.template_offsets[i+1] if i+1<len(matcher.features) else len(matcher.templates)
                        values=2*np.einsum('ij,j->i',matcher.detail_templates[start:end],sharp,optimize=False)/(matcher.detail_norms[start:end]+norm+1e-6)
                        winner=start+int(values.argmax())
                        fine.append((float(values.max()),other,matcher.contour_agreement(sharp.reshape(64,64),matcher.detail_templates[winner].reshape(64,64))))
                    fine.sort(reverse=True)
                    fs,fi,fc=fine[0];fg=fs-fine[1][0] if len(fine)>1 else fs
                    fine_safe=not(fs>=matcher.threshold and fg>=matcher.min_margin and fc>=.68)
                negative=color_rejected and fine_safe
                ambiguity=negative
            if negative:
                descriptor=matcher.gradient_descriptor(feature)
                gradients={other:max(float(np.dot(descriptor,matcher.gradient_descriptor(r))) for r in matcher.references[other]) for _,other,_ in scored}
                certain={other for score,other,_ in scored if score>upper+1e-5}
                for score,other,c in scored:
                    if score<matcher.threshold or c<.68 or gradients[other]<.92:continue
                    # A competitor certainly in the true current Top-10 can
                    # only reduce this candidate's descriptor gap. Omitted
                    # candidates cannot be accepted: their primary is <.86.
                    if not any(j!=other and gradients[other]-gradients[j]<.08-1e-5 for j in certain):
                        negative=False;break
                certificate['gradient_scores']=gradients
                certificate['certain_top10']=sorted(certain)
            anonymous=False
            if not (low or negative):
                temporal=2*float(np.dot(old,fresh))/(old_norm+new_norm+1e-6)
                temporal_contour=matcher.contour_agreement(old.reshape(64,64),fresh.reshape(64,64))
                anonymous=(node.type==NodeType.UNKNOWN and key==old_key
                           and temporal>=matcher.threshold and temporal_contour>=.68)
                if not anonymous:raise OpeningFallback('glyph_category_or_rarity_changed')
                # Shape continuity is not an identity decision or proof that
                # the full matcher would reject every candidate. Keep no ID
                # and no stale global ranking; the initial Top-10 stays only
                # in the queue's private proof, never promoted from memory.
                node.icon_id=node.candidate_id=None;node.candidates=[];node.top10=[];node.reranked=[]
                certificate.update(anonymous_shape_only=True,temporal_glyph=temporal,
                                   temporal_contour=temporal_contour,identity_accepted=False)
            node.recognition_status=RecognitionStatus.AMBIGUOUS if ambiguity else RecognitionStatus.UNKNOWN
            node.confidence=node.glyph_score=round(best,4)
            node.icon_margin=round(max(0.,margin),4);node.contour_score=round(contour,4)
            node.match_reason='local_identity_unconfirmed' if anonymous else 'ambiguous_candidates' if ambiguity else 'low_glyph_score' if low else 'contour_mismatch'
        node.category_hint=category.category;node.category_confidence=category.confidence
        node.category_candidates=list(category.candidates)
        _,fingerprint,_,previous=detector.memory.identity(node.x,node.y,patch,node.content_radius or node.radius,category)
        node.rarity=key[0]
        self.local_updates[node.id]=(fingerprint,previous)
        self.certificates.append(certificate)

    def cancel(self, reason):
        if self.active:
            self.aborts[reason] = self.aborts.get(reason, 0) + 1
        self.active = False

    def refine_initial(self,frame,result,detector):
        """Clip oversized glyph crops once, inside the initial full analysis.

        Hough's outer halo can have a larger radius than the tile's ink. That
        halo changes with scenery and purchase colour. No later full ranking
        is allowed to compensate for it in the compact path.
        """
        if not hasattr(detector,'matcher'):return True
        from dataclasses import fields,replace
        from src.core.models import Match
        started=time.perf_counter();misses=detector.matcher.cache_misses
        # The central emblem can have a larger animated halo than item tiles.
        # Derive the crop ceiling from independently detected unowned circles
        # as well, instead of including scenery around every small glyph.
        radii=[n.radius for n in result.nodes if n.state in (NodeState.AVAILABLE,NodeState.LOCKED)]
        # Available gold rims can be smaller than the locked tile's full rim;
        # the upper quartile avoids shrinking all crops to those small rims.
        node_radius=float(np.quantile(radii,.75)) if radii else result.details['center'][2]
        # Keep the established crop for ordinary centre/item proportions.
        # Only an independently oversized centre halo needs this correction.
        radius=node_radius if result.details['center'][2]>node_radius*1.15 else result.details['center'][2]
        cap=round(radius*.90)
        result.details['opening_crop_ceiling']=dict(center_radius=result.details['center'][2],
            unowned_radius_q75=node_radius,content_radius=cap)
        refined=[];matches={}
        for original in result.nodes:
            n=copy.deepcopy(original)
            if n.state in (NodeState.CENTER,NodeState.ABSORBED) or n.content_radius<=cap:
                refined.append(n);continue
            n.content_radius=cap
            try:feature,key,category,patch=self.parts(frame,n,detector)
            except OpeningFallback:return False
            if n.type==NodeType.PERK:
                # Perks have no catalogue ID. Keep a smaller initial crop
                # only when its fresh diamond/marker and rarity still agree.
                if not key[-1] or key[0]!=original.rarity:n=copy.deepcopy(original)
                refined.append(n);continue
            match=detector.matcher.match(patch,cap,prepared_feature=feature)
            if original.icon_id and match.icon_id!=original.icon_id:
                refined.append(copy.deepcopy(original));continue
            for field in fields(Match):
                if hasattr(n,field.name):setattr(n,field.name,copy.deepcopy(getattr(match,field.name)))
            n.icon_margin=match.margin;n.match_reason=match.reason
            n._opening_overlaps=getattr(match,'_opening_overlaps',None)
            n.type=NodeType(detector.matcher.by_id[match.icon_id].category[:-1]) if match.icon_id else NodeType.UNKNOWN
            n.category_hint=category.category;n.category_confidence=category.confidence;n.category_candidates=category.candidates
            refined.append(n);matches[n.id]=(match,patch,category)
        result.nodes=refined
        for record in detector.memory.records:
            node=next(n for n in refined if (n.x,n.y)==tuple(record['point']))
            if node.id not in matches:continue
            match,patch,category=matches[node.id]
            _,fingerprint,_,_=detector.memory.identity(node.x,node.y,patch,node.content_radius,category)
            record.update(fingerprint=fingerprint,observations=1,match=copy.deepcopy(match),node=asdict(node),last_confirmed_at=None)
        result.details['bloodweb_memory']=detector.memory.summary()
        result.details['full_matcher_calls']=result.details.get('full_matcher_calls',0)+detector.matcher.cache_misses-misses
        result.details.setdefault('timings',{})['opening_initial_refinement']=time.perf_counter()-started
        result.details['opening_refined_crops']=sorted(matches)
        return True

    def begin(self, frame, result, detector, priorities, mode, actions):
        persistent=getattr(self,'persistent',False)
        context = self.context(frame, detector)
        if context in self.seen or result.screen_state != ScreenState.BLOODWEB:
            return False
        if (not frame.live or frame.probe_only or result.level is None
                or result.level_confidence < .95 or not result.edges
                or any(n.state in ((NodeState.UNCERTAIN,) if persistent else (NodeState.PURCHASED, NodeState.ABSORBED, NodeState.UNCERTAIN))
                       for n in result.nodes)):
            return False
        self.seen.add(context)
        if not self.refine_initial(frame,result,detector):return False
        if persistent:
            self.route.rebuild(result,priorities,mode,())
            queue=copy.deepcopy(self.route.queue)
        else:
            simulation = result.simulation_copy()
            queue = []
            for _ in range(5):
                plan = plan_route(simulation, priorities, mode)
                if plan.next_node is None:break
                queue.append(plan)
                owned = next(n for n in simulation.nodes if n.id == plan.next_node)
                owned.state = NodeState.PURCHASED
                neighbors = {b if a == owned.id else a for a,b in simulation.edges if owned.id in (a,b)}
                for n in simulation.nodes:
                    if n.id in neighbors and n.state == NodeState.LOCKED:
                        n.state, n.state_confidence = NodeState.AVAILABLE, .9
        if not queue:
            return False
        try:
            proofs = {n.id:self.evidence(frame,n,detector) for n in result.nodes if n.state not in (NodeState.CENTER,NodeState.ABSORBED)}
        except OpeningFallback:
            return False
        self.context_key, self.priorities, self.mode = context, tuple(priorities), mode
        self.nodes, self.edges = copy.deepcopy(result.nodes), list(result.edges)
        self.proofs, self.queue, self.start_actions = proofs, queue, actions
        self.features={n.id:self.parts(frame,n,detector)[:2] for n in result.nodes if n.state not in (NodeState.CENTER,NodeState.ABSORBED)}
        self.center = tuple(result.details['center'])
        self.stamp = frame.captured_at
        self.generation = detector.memory.generation
        self.last_frame=frame;self.memory=detector.memory
        self.active = True
        return True

    def analyze(self, frame, detector, priorities, mode, actions):
        persistent=getattr(self,'persistent',False)
        from src.core.bloodweb_detector import find_center, ring_fractions, fresh_gold_radius,same_capture_center
        if self.context(frame, detector) != self.context_key:
            raise OpeningFallback('window_roi_level_or_role_changed')
        if frame.probe_only or frame.level_confidence < .95 or frame.captured_at <= self.stamp:
            raise OpeningFallback('fresh_header_required')
        if tuple(priorities) != self.priorities or mode != self.mode:
            raise OpeningFallback('priorities_changed')
        if detector.memory.generation != self.generation:
            raise OpeningFallback('detector_invalidated')
        if not persistent and actions - self.start_actions >= len(self.queue):
            raise OpeningFallback('opening_complete')
        started = time.perf_counter()
        image = frame.image
        from src.core.frame_pipeline import visuals_for
        context=visuals_for(frame)
        center=context.center_for(frame) if context else None
        if center is not None:context.stats['center_reuses']=context.stats.get('center_reuses',0)+1
        else:center=find_center(image, detector.library.root, self.center, local_only=True)
        native=same_capture_center(frame)
        if center is not None and native is not None:
            if np.hypot(center[0]-native[0],center[1]-native[1])>max(5,native[2]*.25):
                raise OpeningFallback('center_or_overlay_changed')
            center=(center[0],center[1],native[2])
        if center != self.center or detector._tooltip_regions(image, center[2], center[:2]):
            raise OpeningFallback('center_or_overlay_changed')
        hsv, gray = cv2.cvtColor(image, cv2.COLOR_BGR2HSV), cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        nodes = copy.deepcopy(self.nodes)
        self.local_updates={};self.certificates=[];self.refreshed_matches={};self.frame_library_matches=0
        confirmed = self.observed_red if persistent else {p.next_node for p in self.queue[:actions-self.start_actions]}
        allowed = self.expected_purchases if persistent else confirmed | {self.queue[actions-self.start_actions].next_node}
        for n in nodes:
            if n.state == NodeState.CENTER:
                continue
            self.last_state_node=n.id
            n.radius=fresh_gold_radius(hsv,n.x,n.y,n.radius,center[2])
            red, gold = ring_fractions(hsv, n.x, n.y, n.radius)
            if n.id in allowed:
                # Purchased rings shrink. Search only this expected target's
                # current concentric rim, not the whole screen or new centres.
                votes=[]
                for scale in (.65,.75,.85,.95,1.):
                    radius=round(n.radius*scale)
                    fraction,_=ring_fractions(hsv,n.x,n.y,radius)
                    votes.append((fraction,radius))
                fraction,radius=max(votes)
                if fraction>.42:
                    red=fraction;n.radius=radius
                    n.content_radius=max(radius,round(center[2]*.90))
            patch = image[n.y-n.radius:n.y+n.radius+1, n.x-n.radius:n.x+n.radius+1]
            mask = node_mask(n.radius, patch.shape[:2])
            interior = float(np.percentile(cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY)[mask>0],95))
            if red > .42:
                n.state, n.state_confidence = NodeState.PURCHASED, .93
                if n.id not in allowed:
                    raise OpeningFallback('unexpected_purchase')
            elif gold > .42:
                n.state, n.state_confidence = NodeState.AVAILABLE, .90
            elif detector._dark_rim(gray,n.x,n.y,n.radius) > .40 or (persistent and n.id in self.absorbed_nodes):
                # An absorbed node never becomes buyable again. Its dark rim
                # flickers while the Entity animates, which is not a change.
                if not persistent:raise OpeningFallback('entity_observed')
                n.state,n.state_confidence=NodeState.ABSORBED,.78
                # An obscured glyph supplies no current identity evidence.
                n.icon_id=n.candidate_id=None;n.type=NodeType.UNKNOWN
                n.confidence=n.glyph_score=0.;n.recognition_status=RecognitionStatus.UNKNOWN
                n.candidates=[];n.top10=[];n.match_reason='absorbed_identity_unconfirmed'
            elif interior > 75:
                n.state, n.state_confidence = NodeState.LOCKED, .78
            else:
                raise OpeningFallback('uncertain_state')
            if n.id in confirmed and n.state != NodeState.PURCHASED:
                raise OpeningFallback('purchased_state_contradiction')
            if persistent and n.id in self.absorbed_nodes and n.state!=NodeState.ABSORBED:
                raise OpeningFallback('absorbed_state_contradiction')
            if persistent and n.state==NodeState.ABSORBED:
                continue
            if self.evidence(frame,n,detector) != self.proofs[n.id]:
                self.last_checked_node=n.id
                self.last_checked_state=n.state.value
                self.verify_local(frame,n,detector)
            n.geometry_confidence = round(min(.95,.55+max(red,gold)*.2+min(1,interior/180)*.15),3)
        # Refresh connections from current pixels. A vanished line between two
        # owned roots cannot unlock anything; retain the freshly missing edge.
        edges = detector._edges(image,nodes,allowed_pairs=None if persistent else set(self.edges))
        if persistent:
            by_id={n.id:n for n in nodes}
            missing=set(self.edges)-set(edges)
            if any(by_id[a].state!=NodeState.ABSORBED and by_id[b].state!=NodeState.ABSORBED
                   and not all(by_id[i].state in (NodeState.CENTER,NodeState.PURCHASED) for i in (a,b)) for a,b in missing):
                raise OpeningFallback('connection_changed')
        if not persistent and set(edges) != set(self.edges):
            raise OpeningFallback('connection_changed')
        circles = [(n.x,n.y,n.radius) for n in nodes if n.state != NodeState.CENTER]
        verified, audit = validate_candidates(gray,circles,center[:2],edges,hsv=hsv)
        # Owned rings shrink and darken after purchase; only nodes that can still
        # be bought need a validated frame.
        open_circles={(n.x,n.y,n.radius) for n in nodes if n.state in (NodeState.AVAILABLE,NodeState.LOCKED)}
        if not open_circles<=set(verified):
            raise OpeningFallback('node_frame_changed')
        result = Analysis(image.shape[1],image.shape[0],ScreenState.BLOODWEB,nodes,edges,
                          level=frame.level,level_confidence=frame.level_confidence,source='live_game')
        result.confidence = round(float(np.mean([n.state_confidence for n in nodes])),3)
        self.stamp = frame.captured_at;self.last_frame=frame
        self.quick_checks += 1
        self.identity_reuses += len(circles)-len(self.local_updates)
        self.local_verifications+=len(self.local_updates)
        result.details = dict(analysis_mode='PERSISTENT_MAP' if persistent else 'FAST_OPENING',center=list(center),node_validation=audit,
            full_matcher_calls=self.frame_library_matches,local_library_matches=self.frame_library_matches,
            local_matcher_calls=len(self.local_updates),local_certificates=self.certificates,
            identity_cache_hits=len(circles)-len(self.local_updates),opening_queue=[p.next_node for p in self.queue],
            timings=dict(opening_local_check=time.perf_counter()-started))
        result.diagnostics = ['fresh_local_evidence','static_read_only']
        now=time.monotonic()
        for record in detector.memory.records:
            node=next((n for n in nodes if (n.x,n.y)==tuple(record['point'])),None)
            if node is None:raise OpeningFallback('memory_geometry_changed')
            record['node']=asdict(node)
            record['neighbors']=[b if a==node.id else a for a,b in edges if node.id in (a,b)]
            if persistent and node.state==NodeState.ABSORBED:
                from src.core.models import Match
                record.update(match=Match(reason='absorbed_identity_unconfirmed'),fingerprint=None,observations=0,last_confirmed_at=None,capture_stamp=frame.captured_at)
                continue
            if node.id in self.local_updates:
                from dataclasses import replace
                fingerprint,_=self.local_updates[node.id]
                record['observations']=record['observations']+1 if fingerprint==record['fingerprint'] else 1
                record['fingerprint']=fingerprint
                record['match']=replace(record['match'],icon_id=node.icon_id,confidence=node.confidence,
                    glyph_score=node.glyph_score,margin=node.icon_margin,contour_score=node.contour_score,
                    recognition_status=node.recognition_status,recognition_method=node.recognition_method)
                if node.id in self.refreshed_matches:record['match']=copy.deepcopy(self.refreshed_matches[node.id])
            else:record['observations']+=int(frame.captured_at>record['capture_stamp'])
            record['capture_stamp']=frame.captured_at
            record['last_confirmed_at']=now if node.icon_id else None
        detector.memory.graph=list(edges)
        if persistent:
            self.edges=list(edges)
            self.observed_red.update(n.id for n in nodes if n.state==NodeState.PURCHASED)
            self.absorbed_nodes.update(n.id for n in nodes if n.state==NodeState.ABSORBED)
        for node in nodes:
            if node.id not in self.refreshed_matches:continue
            self.nodes=[copy.deepcopy(node) if n.id==node.id else n for n in self.nodes]
            self.features[node.id]=self.parts(frame,node,detector)[:2]
            self.proofs[node.id]=self.evidence(frame,node,detector)
        result.details['bloodweb_memory']=detector.memory.summary()
        return result

    def plan(self, result, actions, excluded):
        index = actions - self.start_actions
        if index >= len(self.queue):
            raise OpeningFallback('opening_complete')
        plan = copy.deepcopy(self.queue[index])
        node = next(n for n in result.nodes if n.id == plan.next_node)
        # During confirmation the current target is already red/attempted.
        # Return the next precomputed action for warm_start only when available.
        if node.state == NodeState.PURCHASED:
            index += 1
            if index == len(self.queue):
                return RoutePlan(reason='opening_confirmation')
            plan = copy.deepcopy(self.queue[index])
            node = next(n for n in result.nodes if n.id == plan.next_node)
        if node.id in excluded or not purchasable(node,result.nodes,result.edges):
            # A waiting confirmation can legitimately see the attempted target
            # still available; it supplies no next action or retry permission.
            if index == actions-self.start_actions and node.id in excluded:
                return RoutePlan(reason='opening_confirmation')
            raise OpeningFallback('next_target_unavailable')
        plan.reason = 'FAST_OPENING:' + plan.reason
        return plan

    def summary(self):
        return dict(full_scans=self.full_scans,quick_checks=self.quick_checks,
                    identity_reuses=self.identity_reuses,local_verifications=self.local_verifications,aborts=dict(self.aborts),active=self.active)
