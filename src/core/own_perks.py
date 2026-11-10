"""The player's own four perk diamonds, bottom right of the match HUD.

They exist only during a match, so they prove the HUD is really on screen, and the catalog
knows each perk's role, so they also tell a killer match from a survivor match. Measured on
the 1920x1080 samples: true perks score 0.84-0.98 in their own catalog and at most 0.83
in the other one, and menus, lobbies and scoreboards have none accepted.
"""
import functools

CENTRES=((188,253),(258,180),(118,180),(188,110))  # Reference pixels from the right and bottom edges.
HALF=56
MIN_RECOGNISED=2
ROLES=('survivor','killer')


def own_rect(width,height,scale=1.):
    """(x, y, w, h) of the four diamonds inside the client; `scale` is DBD's HUD scale."""
    s=height/1080*scale;half=round(HALF*s)
    left=width-round(max(c[0] for c in CENTRES)*s)-half;top=height-round(max(c[1] for c in CENTRES)*s)-half
    right=width-round(min(c[0] for c in CENTRES)*s)+half;bottom=height-round(min(c[1] for c in CENTRES)*s)+half
    return left,top,right-left,bottom-top


def own_tiles(region,width,height,scale=1.):
    """region: pixels of own_rect."""
    s=height/1080*scale;half=round(HALF*s);left,top,_,_=own_rect(width,height,scale);tiles=[]
    for dx,dy in CENTRES:
        cx=width-round(dx*s)-left;cy=height-round(dy*s)-top
        tile=region[max(0,cy-half):cy+half,max(0,cx-half):cx+half,:3]
        tiles.append(tile if tile.shape[:2]==(half*2,half*2) else None)
    return tiles


def count_by_role(tiles,matchers):
    return {role:sum(t is not None and matcher.identify(t).icon_id is not None for t in tiles) for role,matcher in matchers.items()}


def decide(counts):
    """(HUD present, role or None): a role needs two recognised perks and a clear majority."""
    best=max(counts.values(),default=0)
    if best<MIN_RECOGNISED:return False,None
    leaders=[role for role,count in counts.items() if count==best]
    return True,(leaders[0] if len(leaders)==1 else None)


def read_own(region,width,height,matchers,scale=1.):
    return decide(count_by_role(own_tiles(region,width,height,scale),matchers))


def crop_own(image,scale=1.):
    """The own_rect pixels of a full client image, and its width and height."""
    height,width=image.shape[:2];x,y,w,h=own_rect(width,height,scale)
    return image[y:y+h,x:x+w],width,height


@functools.lru_cache(maxsize=2)
def load_matchers(root):
    """Shared: the perk overlay and the DS timer both need them, and they weigh about 200 MB."""
    from src.core.perk_matcher import PerkMatcher
    return {role:PerkMatcher(root,role) for role in ROLES}

