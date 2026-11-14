import copy
import json
import time


class Preferences:
    def __init__(self,path,entries):
        self.path=path
        self.original=path.read_text(encoding='utf-8') if path.is_file() else None
        try: self.data=json.loads(self.original or '{}')
        except ValueError: self.data={}
        if not isinstance(self.data,dict): self.data={}
        self._scenes_at_read=copy.deepcopy(self.data.get('purchase_scenes',{}))
        self.roles={e.id:e.role for e in entries}
        self.characters={e.id:e.character for e in entries}
        self.priorities={}
        stored=self.data.get('priorities',{})
        if not isinstance(stored,dict): stored={}
        for role in ('survivor','killer'):
            values=stored.get(role,self.data.get('selected',[]))
            if not isinstance(values,list): values=[]
            self.priorities[role]=list(dict.fromkeys(i for i in values if isinstance(i,str) and (self.roles.get(i)==role or i=='any_perk')))
        self.killer_priorities={}
        contexts=self.data.get('killer_priorities',{})
        if not isinstance(contexts,dict): contexts={}
        for character,values in contexts.items():
            if isinstance(character,str) and isinstance(values,list):
                self.killer_priorities[character]=list(dict.fromkeys(i for i in values if isinstance(i,str) and (self.roles.get(i)=='killer' or i=='any_perk') and self.characters.get(i) in (None,character)))
        # Keep the retired virtual goal in provenance, without replacing it
        # with unrelated concrete items or losing the rest of the user's order.
        if 'event_offering_orange' in str(stored)+str(contexts)+str(self.data.get('selected',[])):
            self.data.setdefault('retired_priority_targets',{})['event_offering_orange']=dict(priorities=stored,killer_priorities=contexts,selected=self.data.get('selected',[]))
        stored_character=self.data.get('character','blight')
        if not isinstance(stored_character,str): stored_character='blight'
        self.killer_priorities.setdefault('blight',list(self.priorities['killer']))
        self.character=stored_character
        self.priorities['killer']=self.killer_priorities.setdefault(stored_character,[])
        self.settings=self.data.get('settings',{})
        if not isinstance(self.settings,dict): self.settings={}
        value=self.settings.get('hold_seconds',.65)
        import math
        if not isinstance(value,(int,float)) or not math.isfinite(value) or not .05<=value<=5: value=.65
        self.settings['hold_seconds']=value
        if self.settings.get('purchase_mode') not in ('click','hold','repeat'): self.settings['purchase_mode']='hold'
        self.settings['dry_run']=self.settings.get('dry_run',True) is not False
        from src.core.hotkey_config import DEFAULT_BINDINGS,validate_bindings
        try: validate_bindings(self.settings.get('hotkeys',{}))
        except (ValueError,AttributeError): self.settings['hotkeys']=dict(DEFAULT_BINDINGS)
        delay=self.settings.get('startup_delay',3)
        self.settings['startup_delay']=delay if type(delay) is int and 3<=delay<=10 else 3
        timeout=self.settings.get('refresh_timeout',15)
        early=self.settings.get('fast_early_levels',True)
        self.settings['fast_early_levels']=early if type(early) is bool else False
        opening=self.settings.get('fast_opening',True)
        self.settings['fast_opening']=opening if type(opening) is bool else False
        persistent=self.settings.get('persistent_route',True)
        self.settings['persistent_route']=persistent if type(persistent) is bool else False
        for key in ('experimental_persistent','shadow_planner','burst_mid_level'):
            self.settings[key]=self.settings.get(key) is True
        gap=self.settings.get('burst_gap',.55)
        self.settings['burst_gap']=gap if type(gap) in (int,float) and .25<=gap<=1 else .55
        self.settings['physical_f9_check']=self.settings.get('physical_f9_check') is True
        self.settings['native_chain']=self.settings.get('native_chain') is True
        # Off until single purchases are proven reliable. Later re-enabling sticks.
        if not self.settings.get('burst_mid_level_default_v1'):
            # Fastest variant with no pauses in the live comparison of 2026-11-09.
            self.settings['burst_mid_level']=True;self.settings['burst_mid_level_default_v1']=True
        if not self.settings.get('native_chain_reset_v1'):
            self.settings['native_chain']=False;self.settings['native_chain_reset_v1']=True
        self.settings['turbo_burst']=self.settings.get('turbo_burst',True) is True
        self.settings['developer_mode']=self.settings.get('developer_mode') is True
        self.settings['match_overlay']=self.settings.get('match_overlay') is True
        key=self.settings.get('overlay_key')
        self.settings['overlay_key']=key if isinstance(key,str) and key.strip() else 'TAB'
        if self.settings.get('overlay_size') not in ('normal','large'):self.settings['overlay_size']='large'
        self.settings['ds_timer']=self.settings.get('ds_timer') is True
        self.settings['recording_friendly']=self.settings.get('recording_friendly') is True
        expired=self.settings.get('ds_expired_seconds',15)
        self.settings['ds_expired_seconds']=expired if type(expired) in (int,float) and 10<=expired<=20 else 15
        levels=self.settings.get('max_levels',0)
        self.settings['max_levels']=levels if type(levels) is int and 0<=levels<=999 else 0
        self.settings['center_auto_unavailable']=self.settings.get('center_auto_unavailable') is True
        self.settings['refresh_timeout']=min(timeout,15) if type(timeout) in (int,float) and 2<=timeout<=120 else 15
        self.settings['detailed_diagnostics']=self.settings.get('detailed_diagnostics') is True
        if self.settings.get('run_mode') not in ('test','continuous'):self.settings['run_mode']='continuous'
        randomizer=self.settings.get('randomizer');self.settings['randomizer']=randomizer if isinstance(randomizer,dict) else {}
        cross=self.settings.get('crosshair');cross=cross if isinstance(cross,dict) else {}
        from src.core.crosshair import DEFAULT_KEY,PRESETS
        self.settings['crosshair']=dict(enabled=cross.get('enabled') is True,preset=cross.get('preset') if cross.get('preset') in PRESETS else 'classic_gap',
            key=cross.get('key') if isinstance(cross.get('key'),str) else DEFAULT_KEY,overrides=cross.get('overrides') if isinstance(cross.get('overrides'),dict) else {})
        from src.core.onevone import clean_settings as onevone_settings
        self.settings['onevone']=onevone_settings(self.settings.get('onevone'))
        from src.core.map_names import clean_settings as map_settings
        self.settings['map_overlay']=map_settings(self.settings.get('map_overlay'))
        from src.core.memes import clean_settings as meme_settings
        self.settings['memes']=meme_settings(self.settings.get('memes'))
        scale=self.settings.get('overlay_scale',100);self.settings['overlay_scale']=scale if type(scale) is int and 50<=scale<=200 else 100
        if self.settings.get('overlay_position') not in ('left','right'):self.settings['overlay_position']='left'
        if self.settings.get('priority_mode') not in ('top_first','smart','strict') or not self.settings.get('priority_top_first_v1'):
            # One behaviour for everyone; the old modes stay reachable in developer mode.
            self.settings['priority_mode']='top_first';self.settings['priority_top_first_v1']=True

    def saved_calibration(self,allow_purchase_failure=False):
        """Restore purchase method only; historical coordinates never arm input."""
        import copy,math
        receipt=self.data.get('calibration')
        if not isinstance(receipt,dict) or receipt.get('verified') is not True or receipt.get('user_confirmed') is not True:
            return None
        duration=receipt.get('hold_seconds')
        blocked=receipt.get('blocked_reason')
        if ((blocked and not (allow_purchase_failure and blocked=='purchase_not_confirmed')) or receipt.get('mode')!=self.settings['purchase_mode'] or
                not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration!=self.settings['hold_seconds']):
            return None
        for field in ('before','after'):
            value=receipt.get(field)
            if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):return None
        if receipt['before']==receipt['after']:return None
        return copy.deepcopy(receipt)

    def recovery_calibration(self):
        receipt=self.saved_calibration(allow_purchase_failure=True)
        return receipt if receipt and receipt.get('blocked_reason')=='purchase_not_confirmed' else None

    def refresh_purchase_scenes(self):
        """Keep external evidence when this GUI has not edited that scene.

        This only reloads persisted evidence. It does not authorize input or
        replace the worker's two fresh observations at Start.
        """
        try: latest=json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError: return
        external=latest.get('purchase_scenes',{})
        local=self.data.get('purchase_scenes',{})
        if not isinstance(external,dict) or not isinstance(local,dict):
            raise ValueError('invalid_purchase_scenes')
        merged=copy.deepcopy(external)
        missing=object()
        for key in set(local)|set(self._scenes_at_read):
            if local.get(key,missing)!=self._scenes_at_read.get(key,missing):
                if key in local:merged[key]=copy.deepcopy(local[key])
                else:merged.pop(key,None)
        if merged or 'purchase_scenes' in self.data or 'purchase_scenes' in latest:
            self.data['purchase_scenes']=merged
        self._scenes_at_read=copy.deepcopy(external)

    def save(self,language,role,calibration=None):
        self.refresh_purchase_scenes()
        self.killer_priorities[self.character]=self.priorities['killer']
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.original and self.data.get('schema')!=2:
            backup=self.path.with_name('preferences.v1.backup.json')
            if not backup.exists(): backup.write_text(self.original,encoding='utf-8')
        self.data.update(schema=2,language=language,role=role,character=self.character,killer_priorities=self.killer_priorities,priorities=self.priorities,settings=self.settings)
        if calibration is not None: self.data['calibration']=calibration
        temporary=self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding='utf-8')
        for attempt in range(7):
            try:
                temporary.replace(self.path)
                break
            except PermissionError:
                # A Windows reader may briefly deny FILE_SHARE_DELETE. Keep
                # the previous complete profile until atomic replace succeeds.
                if attempt==6:raise
                time.sleep(.025)
        self._scenes_at_read=copy.deepcopy(self.data.get('purchase_scenes',{}))

    def fill_recommended(self,recommended):
        """Once per killer, and only while that list is empty: never touches a list the user set."""
        done=self.settings.setdefault('recommended_filled',[])
        for character,ids in recommended.items():
            if character in done:continue
            done.append(character)
            current=self.killer_priorities.setdefault(character,[])
            if not current:current.extend(i for i in ids if i=='any_perk' or self.roles.get(i)=='killer')

    def activate_character(self,character):
        if hasattr(self,'character') and hasattr(self,'killer_priorities') and self.killer_priorities:
            self.killer_priorities[self.character]=self.priorities['killer']
        self.character=character
        self.priorities['killer']=self.killer_priorities.setdefault(character,[])
