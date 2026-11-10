"""Build assets/randomizer_data.json from public Dead by Daylight Wiki pages (item add-on types, survivors)."""
import hashlib
import io
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image  # noqa: E402
from src.core.wiki_library import SITE, original_url, public_fetch  # noqa: E402

TYPES = (('flashlight', 'flashlight'), ('med-kit', 'medkit'), ('med kit', 'medkit'), ('toolbox', 'toolbox'), ('map', 'map'), ('key', 'key'), ('fog vial', 'fog_vial'), ('vial', 'fog_vial'))


class Sections(HTMLParser):
    """Rows of the Add-ons page with the h2/h3 heading they sit under."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h2 = self.h3 = '';self.tag = None;self.text = [];self.row = None;self.cell = 0;self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag in ('h2', 'h3'):
            self.tag = tag;self.text = []
        elif tag == 'tr':
            self.row = dict(cells=[''], h2=self.h2, h3=self.h3);self.cell = 0
        elif tag in ('td', 'th') and self.row is not None:
            self.cell += 1;self.row['cells'].append('')

    def handle_endtag(self, tag):
        if tag == self.tag:
            value = ''.join(self.text).strip();self.tag = None
            if tag == 'h2':
                self.h2, self.h3 = value, ''
            else:
                self.h3 = value
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row);self.row = None

    def handle_data(self, data):
        if self.tag:
            self.text.append(data)
        elif self.row is not None:
            self.row['cells'][-1] += data


def item_type(heading):
    low = heading.lower()
    return next((kind for word, kind in TYPES if word in low), None)


def main():
    body = public_fetch(SITE + '/wiki/Add-ons', 16_000_000)[0].decode('utf-8')
    parser = Sections();parser.feed(body)
    addons = {}
    for row in parser.rows:
        if 'survivor' in row['h2'].lower() and len(row['cells']) >= 4 and item_type(row['h3']):
            name = re.sub(r'\s+', ' ', row['cells'][2]).strip()
            if name:
                addons[name] = item_type(row['h3'])
    page = public_fetch(SITE + '/wiki/Survivors', 16_000_000)[0].decode('utf-8')
    found = {}
    for value in re.findall(r'(?:src|data-src)="([^"]*S\d+_[^"]*_Portrait\.[a-z]+[^"]*)"', page):
        url = original_url(value, SITE + '/wiki/Survivors')
        match = url and re.fullmatch(r'S(\d+)_(.+)_Portrait\.[a-z]+', unquote(urlsplit(url).path.rsplit('/', 1)[-1]))
        if match:
            found.setdefault(int(match[1]), (match[2], url))
    survivors = []
    folder = ROOT / 'assets/ui/survivors';folder.mkdir(parents=True, exist_ok=True)
    for number, (raw, url) in sorted(found.items()):
        name = re.sub(r'(?<=[a-z.])(?=[A-Z])', ' ', raw.replace('_', ' ')).strip()
        ident = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
        image = Image.open(io.BytesIO(public_fetch(url)[0])).convert('RGBA');image.thumbnail((192, 192))
        path = folder / f'{ident}.png';image.save(path)
        survivors.append(dict(id=ident, name=name, image=path.relative_to(ROOT).as_posix(), url=url, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    out = dict(schema=1, source=SITE, license='Dead by Daylight Wiki, CC BY-SA', addon_types=addons, survivors=survivors)
    (ROOT / 'assets/randomizer_data.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    from collections import Counter
    print(len(addons), Counter(addons.values()), len(survivors), survivors[-1]['name'])


if __name__ == '__main__':
    main()
