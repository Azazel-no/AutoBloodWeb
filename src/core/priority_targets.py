"""Abstract UI goals are separate from actual game asset IDs."""
import hashlib
from src.core.models import IconEntry

EVENT_TARGET='event_offering_orange'
PERK_TARGET='any_perk'


def perk_target_entry(root,role):
    image='assets/ui/targets/any_perk.svg'
    return IconEntry(PERK_TARGET,role,'perks',None,image,image,[],'virtual',
        hashlib.sha256((root/image).read_bytes()).hexdigest())


def perk_eligible(node):
    from src.core.models import NodeType
    return node.type==NodeType.PERK and node.category_hint=='perk' and node.category_confidence>=.95 and node.geometry_confidence>=.65


def event_target_entry(root,role):
    image='assets/ui/targets/event_offering_orange.svg'
    return IconEntry(EVENT_TARGET,role,'offerings',None,image,image,[],'event',
        hashlib.sha256((root/image).read_bytes()).hexdigest())


def event_eligible(node):
    from src.core.models import NodeType
    return node.event_offering and node.event_confidence>=.95 and node.category_hint=='offering' and node.type not in (NodeType.PERK,NodeType.ITEM,NodeType.ADDON)
