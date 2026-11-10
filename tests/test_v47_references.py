import json,hashlib,math
from src.core.bloodweb_detector import BloodwebDetector
from src.utils.images import read_image

def test_new_user_files_and_selected_glyphs(root,library):
    truth=json.loads((root/'tests/fixtures/v47/reference_annotations.json').read_text(encoding='utf-8'))
    assert len(truth['screens'])==8
    for screen in truth['screens']:
        path=root/screen['source'];assert hashlib.sha256(path.read_bytes()).hexdigest()==screen['sha256']
        result=BloodwebDetector(library,screen['role'],character=screen['character'] or 'blight').analyze(read_image(path),str(path))
        used=set()
        for x,y,state in screen['nodes']:
            candidates=[n for n in result.nodes if n.id not in used and math.dist((x,y),(n.x,n.y))<20]
            assert candidates,(path,x,y)
            node=min(candidates,key=lambda n:math.dist((x,y),(n.x,n.y)));used.add(node.id);assert node.state==state,(path,x,y,node.state,state)
        assert len(used)==len(result.nodes)
        for x,y,ident in screen['icons']:
            node=min(result.nodes,key=lambda n:math.dist((x,y),(n.x,n.y)))
            assert node.icon_id==ident and node.confidence>=.86 and node.icon_margin>=.035 and node.contour_score>=.68
