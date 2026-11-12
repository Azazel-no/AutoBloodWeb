"""The startup notice. Raise NOTICE_VERSION when its content changes: it is shown again."""
NOTICE_VERSION=4
NOTICE_SECTIONS=('notice_does','notice_needs','notice_problems','notice_risk','notice_crosshair','notice_ui_scale')
PAIRS={
 'notice_ui_scale_title':('Масштаб UI и HUD','UI and HUD scale'),
 'notice_ui_scale':('AutoBloodWeb лучше всего работает при масштабе UI и HUD 100 % или 90 % в настройках Dead by Daylight. Другие размеры могут не распознаваться.','AutoBloodWeb works best with UI and HUD scale at 100% or 90% in Dead by Daylight settings. Other sizes may not be recognised.'),
 'notice_crosshair_title':('Прицел','Crosshair'),
 'notice_crosshair':('Вкладка «Прицел» выключена по умолчанию. Такой оверлей Behaviour может считать нечестным преимуществом: используйте на свой риск.','The Crosshair tab is off by default. Behaviour may treat such an overlay as an unfair advantage: use it at your own risk.'),
 'notice_does_title':('Что делает программа','What the app does'),
 'notice_does':('• Прокачивает Кровавую паутину по вашим приоритетам (перки и т. д.), использует автопокупку через центр, автопрестиж и «Остановиться через N уровней».\n• В матче за выжившего показывает перки союзников (нажмите Tab).\n• За убийцу показывает таймер Решающего удара, если он включён.\n• Может запускаться вместе с DBD и работать из трея.',
  '• Levels the Bloodweb by your priorities (perks etc.), with the centre auto-purchase, auto-prestige and "Stop after N levels".\n• Shows teammate perks in survivor matches (press Tab).\n• Shows a Decisive Strike timer for killers, if enabled.\n• Can start together with DBD and stay in the tray.'),
 'notice_needs_title':('Что нужно','Requirements'),
 'notice_needs':('• DBD в режиме «без рамки» или в окне на весь экран.\n• Проверено на 1920×1080; другие разрешения масштабируются, точность может быть ниже.\n• Права администратора: Windows спросит разрешение при запуске, нажмите «Да».\n• Не двигайте мышь во время прокачки паутины.\n• F9 — экстренная остановка.',
  '• DBD in borderless or windowed fullscreen.\n• Tested at 1920×1080; other resolutions are scaled and may be less accurate.\n• Administrator rights: Windows asks for permission at start, click Yes.\n• Do not move the mouse during Bloodweb automation.\n• F9 is the emergency stop.'),
 'notice_problems_title':('Известные проблемы','Known problems'),
 'notice_problems':('• ReShade, фильтры NVIDIA/AMD, HDR, цветовые моды и моды интерфейса или текстур могут нарушить распознавание паутины и перков — отключите их.\n• Оверлеи других программ поверх левой части HUD или паутины могут мешать.',
  '• ReShade, NVIDIA/AMD filters, HDR, colour mods and custom UI or texture mods can break recognition of the Bloodweb and perks; disable them.\n• Overlays from other apps over the left HUD or the Bloodweb can interfere.'),
 'notice_risk_title':('Риск и ответственность','Risk and disclaimer'),
 'notice_risk':('Неофициальная фанатская программа, не связана с Behaviour Interactive. Она только считывает пиксели экрана и имитирует щелчки мыши в меню; память и файлы игры не читает. Риск блокировки минимален, но возможен. Вы используете программу на свой риск, без каких-либо гарантий.',
  'Unofficial fan tool, not affiliated with Behaviour Interactive. It only reads screen pixels and simulates mouse clicks in menus; it never reads game memory or files. The risk of a ban is minimal but possible. Use at your own risk; no guarantees.'),
}
