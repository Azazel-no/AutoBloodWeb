import copy
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum


class NodeState(StrEnum):
    AVAILABLE = 'available'
    PURCHASED = 'purchased'
    ABSORBED = 'absorbed'
    LOCKED = 'locked'
    CENTER = 'center'
    UNCERTAIN = 'uncertain'


class NodeType(StrEnum):
    ITEM = 'item'
    ADDON = 'addon'
    OFFERING = 'offering'
    PERK = 'perk'
    BLOODWEB_CENTER = 'bloodweb_center'
    CENTER = 'bloodweb_center'  # Compatibility alias for the graph root.
    PRESTIGE = 'prestige'
    UNKNOWN = 'unknown'

class RecognitionStatus(StrEnum):
    RECOGNIZED='recognized'
    AMBIGUOUS='ambiguous'
    UNKNOWN='unknown'


class ScreenState(StrEnum):
    BLOODWEB = 'bloodweb'
    PRESTIGE = 'prestige'
    PRESTIGE_READY = 'prestige_ready'
    REWARD = 'reward'
    TUTORIAL = 'tutorial'
    REFRESH = 'refresh'
    UNKNOWN = 'unknown'


@dataclass
class IconEntry:
    id: str
    role: str
    category: str
    character: str | None
    image: str
    source: str
    box: list[int]
    rarity: str
    fingerprint: str


@dataclass
class Match:
    icon_id: str | None = None
    candidate_id: str | None = None
    confidence: float = 0.0
    margin: float = 0.0
    candidates: list[tuple[str,float]] = field(default_factory=list)
    reason: str = 'no_glyph'
    rarity: str = 'unknown'
    glyph_score: float = 0.0
    contour_score: float = 0.0
    category: str = 'unknown'
    category_confidence: float = 0.0
    category_candidates: list[str] = field(default_factory=list)
    rarity_confidence: float = 0.0
    rarity_tie_break: bool = False
    glyph_detail_score: float = 0.0
    glyph_detail_tie_break: bool = False
    recognition_status: RecognitionStatus = RecognitionStatus.UNKNOWN
    top10: list[tuple[str,float]] = field(default_factory=list)
    reranked: list[dict] = field(default_factory=list)
    recognition_method: str = 'glyph'


@dataclass
class Node:
    id: int
    x: int
    y: int
    radius: int
    state: NodeState = NodeState.UNCERTAIN
    type: NodeType = NodeType.UNKNOWN
    icon_id: str | None = None
    candidate_id: str | None = None
    confidence: float = 0.0
    state_confidence: float = 0.0
    candidates: list[tuple[str,float]] = field(default_factory=list)
    geometry_confidence: float = 0.0
    icon_margin: float = 0.0
    match_reason: str = 'not_matched'
    rarity: str = 'unknown'
    glyph_score: float = 0.0
    ring: int = 0
    angle: float = 0.0
    content_radius: int = 0
    crop_path: str | None = None
    normalized_path: str | None = None
    contour_score: float = 0.0
    category_hint: str = 'unknown'
    category_confidence: float = 0.0
    category_candidates: list[str] = field(default_factory=list)
    event_offering: bool = False
    event_confidence: float = 0.0
    orange_background: bool = False
    rarity_confidence: float = 0.0
    rarity_tie_break: bool = False
    glyph_detail_score: float = 0.0
    glyph_detail_tie_break: bool = False
    recognition_status: RecognitionStatus = RecognitionStatus.UNKNOWN
    top10: list[tuple[str,float]] = field(default_factory=list)
    reranked: list[dict] = field(default_factory=list)
    recognition_method: str = 'glyph'


@dataclass
class Analysis:
    width: int
    height: int
    screen_state: ScreenState
    nodes: list[Node] = field(default_factory=list)
    edges: list[tuple[int, int]] = field(default_factory=list)
    level: int | None = None
    confidence: float = 0.0
    safe_to_automate: bool = False
    diagnostics: list[str] = field(default_factory=list)
    ok_box: tuple[int, int, int, int] | None = None
    source: str | None = None
    debug_image: str | None = None
    analysis_id: str | None = None
    details: dict = field(default_factory=dict)
    level_confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def simulation_copy(self) -> 'Analysis':
        """Independent nodes for a planner to mutate. Only the declared fields are read: the GUI
        thread attaches live_frame/renderer to a result it receives, and deepcopy of the whole
        object failed with 'dictionary changed size during iteration' (live 2026-11-08 19:26)."""
        values={f.name:getattr(self,f.name) for f in fields(self)}
        values.update(nodes=copy.deepcopy(self.nodes),edges=list(self.edges),diagnostics=list(self.diagnostics),details=dict(self.details))
        return Analysis(**values)
