"""Validated Windows VK bindings; no hooks or repeated key-down handling."""
DEFAULT_BINDINGS={'toggle':'F8','stop':'F9'}


def parse_hotkey(text):
    parts=text.upper().replace(' ','').split('+'); modifiers=0
    if not parts or len(set(parts))!=len(parts): raise ValueError('invalid_hotkey')
    for token in parts[:-1]:
        if token not in ('CTRL','ALT','SHIFT'): raise ValueError('invalid_hotkey')
        modifiers|={'CTRL':2,'ALT':1,'SHIFT':4}[token]
    key=parts[-1]
    if key.startswith('F') and key[1:].isdigit() and 1<=int(key[1:])<=24: vk=0x70+int(key[1:])-1
    elif len(key)==1 and key in '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ': vk=ord(key)
    else: raise ValueError('invalid_hotkey')
    return modifiers,vk


def validate_bindings(bindings):
    if not isinstance(bindings,dict) or set(bindings)!=set(DEFAULT_BINDINGS): raise ValueError('invalid_hotkey')
    values={action:parse_hotkey(text) for action,text in bindings.items()}
    if values['toggle']==values['stop'] or values['toggle']==parse_hotkey('Ctrl+Alt+F9'):
        raise ValueError('hotkey_conflict')
    return values


def extra_keys_conflict(bindings,crosshair,details):
    """True when the crosshair or match-details key repeats another shortcut."""
    keys=[bindings['toggle'].upper(),bindings['stop'].upper(),crosshair.upper(),details.upper()]
    return len(set(keys))<len(keys)
