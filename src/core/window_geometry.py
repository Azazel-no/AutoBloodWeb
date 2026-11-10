"""Rebind observations to current physical client pixels; never authorize input."""
import copy
import math
from dataclasses import asdict
from src.core.models import NodeState, ScreenState


class WindowGeometryManager:
    @staticmethod
    def project(record, window):
        """A coordinate hypothesis only. Fresh visual evidence must validate it."""
        old = record['window']['client']
        new = window.client
        if min(old['width'], old['height'], new.width, new.height) <= 0:
            raise ValueError('invalid_client_geometry')
        sx, sy = new.width / old['width'], new.height / old['height']
        def point(p):
            return [new.x + (p[0] - old['x']) * sx, new.y + (p[1] - old['y']) * sy]
        projected = copy.deepcopy(record)
        projected['window'] = asdict(window)
        projected['attempted'] = [point(p) for p in record.get('attempted', [])]
        for node in projected.get('nodes', []):
            node['point'] = point(node['point'])
            node['radius'] *= min(sx, sy)
        # Pending receipts, operation IDs and their physical evidence stay
        # untouched. A projection can never reconcile a paid operation.
        projected.pop('transition_observation', None)
        if old != asdict(new):
            projected['transition_glyphs'] = []
        return projected

    @staticmethod
    def resolved_scene_matches(record, frame, result):
        """A resolved same-level map may survive a new HWND/monitor/DPI."""
        if (record.get('pending') or not frame.live or frame.probe_only or not frame.window.foreground
                or result.screen_state != ScreenState.BLOODWEB or type(result.level) is not int
                or result.level != record.get('level') or result.level_confidence < .95
                or record.get('level_confidence', 0) < .95 or result.details.get('confirmation_only')):
            return False
        old = record.get('nodes', [])
        if not old or len(old) != len(result.nodes):
            return False
        matched = set()
        known = 0
        for previous in old:
            candidates = [n for n in result.nodes if math.dist(previous['point'], frame.to_screen(n.x, n.y))
                          < max(5, previous['radius'] * .2)]
            if len(candidates) != 1 or candidates[0].id in matched:
                return False
            node = candidates[0]
            matched.add(node.id)
            if node.geometry_confidence < .65 or node.state_confidence < .8:
                return False
            if previous['state'] == NodeState.CENTER:
                if node.state != NodeState.CENTER:
                    return False
                continue
            if node.state not in (NodeState.AVAILABLE, NodeState.LOCKED, NodeState.PURCHASED, NodeState.ABSORBED):
                return False
            if previous['state'] in (NodeState.PURCHASED, NodeState.ABSORBED) and node.state != previous['state']:
                return False
            if previous.get('icon') and node.icon_id:
                if (node.icon_id != previous['icon'] or node.confidence < .86
                        or node.contour_score < .68 or node.icon_margin < .035):
                    return False
                known += 1
        if known < 3:
            return False
        for p in record.get('attempted', []):
            found = [n for n in result.nodes if math.dist(p, frame.to_screen(n.x, n.y)) < max(5, n.radius * .2)]
            if len(found) != 1 or found[0].state not in (NodeState.PURCHASED, NodeState.ABSORBED):
                return False
        return True
