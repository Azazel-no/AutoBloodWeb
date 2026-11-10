"""Classification and limits for in-process recovery around the live loop.

This module decides, it never sends input. Only listed codes are retried, so a
new or unknown failure pauses. Retrying re-reads the game: every Down still has
to pass the existing fresh-frame, focus, geometry and outcome checks.
"""
from collections import deque
import time

RETRY='retry'
WAIT='wait'
PAUSE='pause'

RETRY_CODES=frozenset((
    'bloodweb_not_found','bloodweb_occluded','capture_black_frame','capture_failed','capture_size_mismatch',
    'chain_bp_unproven','chain_fresh_path_unproven','chain_geometry_changed','chain_map_changed',
    'chain_preview_or_price_unproven','chain_price_unproven','chain_two_clean_frames_required',
    'automatic_unlock_unproven','frame_unstable','item_tooltip','next_bloodweb_timeout','no_confident_action',
    'preparation_recovery_limit','probe_capture_failed','purchase_preparation_changed',
    'recovery_timeout','refresh_timeout','roi_invalid','single_standard_price_bound_unproven','stale_frame',
    'target_occluded','unknown_screen','unsafe_node','window_changed',
    # Read-only or before any input; with a pending purchase only the read-only recheck runs.
    'bloodweb_transition_started','live_frame_required','preview_context_unproven','mouse_operation_busy',
    'target_already_attempted','outcome_evidence_required','outcome_context_changed','automatic_unlock_entity'))

# The game is absent or not in front. Nothing can be observed or clicked.
WAIT_CODES=frozenset(('client_area_failed','game_focus_lost','game_minimized','game_not_found',
    'game_process_unverified','monitor_failed','focus_wait_timeout'))

# These details turn an otherwise retryable code into a stop for the user.
PAUSE_DETAILS=frozenset(('geometry_recovery_limit','previous_start_unresolved','reviewed_native_locator_required',
    'saved_chain_contract_block','saved_pending_geometry_requires_evidence','saved_purchase_scene_window',
    'scene_validation_required'))


def classify(code,detail=''):
    if detail in PAUSE_DETAILS:return PAUSE
    if code in WAIT_CODES:return WAIT
    return RETRY if code in RETRY_CODES else PAUSE


class RecoverySupervisor:
    max_attempts=10
    max_same_failures=5
    window_seconds=60.
    no_progress_seconds=300.
    wait_limit_seconds=300.
    countdown_seconds=3.
    base_delay=.5
    max_delay=5.

    def __init__(self,clock=time.monotonic):
        self.clock=clock
        self.attempts=deque()
        self.same_failure=None;self.same_count=0
        self.last_progress_at=clock()
        self.progress=None
        self.readonly_operations=set()
        self.recoveries=0

    def begin(self,progress):
        self.attempts.clear()
        self.same_failure=None;self.same_count=0
        self.last_progress_at=self.clock()
        self.progress=progress

    def note_progress(self,progress):
        if progress!=self.progress:
            self.progress=progress
            self.last_progress_at=self.clock()
            self.attempts.clear()
            self.same_failure=None;self.same_count=0

    def charge(self,progress,failure=None):
        """Count one recovery. Returns the exhausted limit's name, or None."""
        self.note_progress(progress)
        now=self.clock()
        if now-self.last_progress_at>self.no_progress_seconds:return 'recovery_no_progress_limit'
        while self.attempts and now-self.attempts[0]>self.window_seconds:self.attempts.popleft()
        if len(self.attempts)>=self.max_attempts:return 'recovery_attempt_limit'
        if failure is not None:
            # A slow repeating failure stays under the rate limit but will not
            # fix itself. Any confirmed progress or other failure resets this.
            self.same_count=self.same_count+1 if failure==self.same_failure else 1
            self.same_failure=failure
            if self.same_count>self.max_same_failures:return 'recovery_same_failure_limit'
        self.attempts.append(now)
        self.recoveries+=1
        return None

    def exclude_wait(self,seconds):
        """Time spent waiting for the game is not a failure to make progress."""
        self.last_progress_at+=seconds

    def delay(self):
        return min(self.max_delay,self.base_delay*2**max(0,len(self.attempts)-1))

    def first_readonly(self,operation):
        if operation in self.readonly_operations:return False
        self.readonly_operations.add(operation)
        return True
