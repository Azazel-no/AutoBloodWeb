import numpy as np
from src.core.resources import ResourceReader
from src.utils.images import read_image


def test_resource_digits_from_original_user_reference(root):
    reader=ResourceReader(root)
    image=read_image(root/'references/bloodweb/bloodweb_prestige_reward_ok.png')
    assert reader.decode(image[58:81,1420:1478])==9570
    assert reader.decode(image[58:81,1518:1583])==25770
    assert reader.decode(image[58:81,1620:1720])==3712232
    assert reader.read(image)==3712232
    assert reader.read(np.zeros_like(image)) is None


def test_zero_points_stops_without_input():
    from tests.test_automation import Device,engine,web
    from src.core.automation import State
    device=Device([web()]); capture=device.capture
    def zero():
        frame=capture(); frame.bloodpoints=0; return frame
    device.capture=zero
    e=engine(device,dry_run=False,bloodpoint_checks=True); e.run()
    assert e.state==State.INSUFFICIENT_BP and not device.presses
