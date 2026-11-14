STRINGS = {
    'ru': {
        'subtitle':'ВАШИ ИКОНКИ • ВАШИ ПРИОРИТЕТЫ', 'mode':'Проверка скриншотов',
        'safe_notice':'Безопасный режим · автоклик ещё не подключён',
        'survivor':'Выживший', 'killer':'Убийца', 'blight':'Мор', 'all':'Все',
        'items':'Предметы', 'addons':'Аддоны', 'offerings':'Подношения',
        'library':'БИБЛИОТЕКА ИКОНОК', 'library_count':'{count} иконок · нажмите для выбора',
        'selected':'ПРИОРИТЕТЫ', 'selected_empty':'Выберите нужные изображения в библиотеке',
        'clear':'Очистить', 'remove':'Нажмите, чтобы убрать из приоритетов',
        'controls':'УПРАВЛЕНИЕ', 'start':'Старт', 'pause':'Пауза', 'stop':'Стоп',
        'scan':'Сканировать окно игры', 'test':'Открыть скриншот…', 'sample':'Пример из проекта',
        'analyze':'Проверить', 'live_disabled':'Автоклик будет доступен после проверки следующего этапа.',
        'topmost':'Поверх окон', 'nodes':'Узлы', 'targets':'Цели', 'matched':'Иконки',
        'state':'Состояние', 'confidence':'Оценка состояния', 'level':'Уровень',
        'ready':'Готово к анализу', 'busy':'Анализ изображения…', 'analysis_done':'Анализ завершён',
        'error':'Ошибка', 'debug':'ДИАГНОСТИКА', 'preview':'Превью', 'details':'Узлы и уверенность',
        'empty_preview':'Результат анализа появится здесь', 'number':'№', 'content':'Содержимое',
        'icon_score':'Сходство', 'bloodweb':'Паутина', 'prestige':'Престиж',
        'reward':'Награда / OK', 'refresh':'Ожидание новой паутины', 'unknown':'Не определено',
        'available':'Доступен', 'purchased':'Куплен', 'absorbed':'Поглощён',
        'locked':'Заблокирован', 'center':'Центр', 'uncertain':'Не уверен', 'perk':'Перк',
        'item':'Предмет', 'addon':'Аддон', 'offering':'Подношение',
        'game_not_found':'Видимое окно Dead by Daylight не найдено.',
        'game_occluded':'Окно игры перекрыто. Разместите игру и приложение рядом.',
        'static_read_only':'Анализ без управления мышью.', 'level_ocr_unavailable':'Чтение номера уровня пока не подключено.',
        'center_not_found':'Центр паутины не найден. Изображение не подтверждено.',
        'reward_reference_matched':'Окно награды и надпись OK найдены по эталону.',
        'uncertain_icons':'Неопределённых иконок: {count}.',
        'plan':'Предпросмотр маршрута', 'priority':'К выбранной цели · узел {node}',
        'filler':'Заполнение паутины · узел {node}', 'no_confident_action':'Нет уверенного действия',
        'no_action':'Нет действия', 'debug_saved':'PNG и JSON: debug/results',
        'logs':'Журнал', 'last_action':'Последнее действие', 'select_file':'Выберите скриншот Bloodweb',
        'analysis_failed':'Не удалось прочитать или обработать изображение: {message}',
        'legend':'Золото — доступен · красный — куплен · лиловый — поглощён',
        'stopped':'Остановлено', 'save_failed':'Не удалось сохранить настройки: {message}',
        'error_details':'Подробности записаны в debug/app.log',
    },
    'en': {
        'subtitle':'YOUR ICONS • YOUR PRIORITIES', 'mode':'Screenshot testing',
        'safe_notice':'Safe mode · auto-clicking is not connected yet',
        'survivor':'Survivor', 'killer':'Killer', 'blight':'Blight', 'all':'All',
        'items':'Items', 'addons':'Add-ons', 'offerings':'Offerings',
        'library':'ICON LIBRARY', 'library_count':'{count} icons · click to select',
        'selected':'PRIORITIES', 'selected_empty':'Select the images you want in the library',
        'clear':'Clear', 'remove':'Click to remove from priorities',
        'controls':'CONTROLS', 'start':'Start', 'pause':'Pause', 'stop':'Stop',
        'scan':'Scan game window', 'test':'Open screenshot…', 'sample':'Project reference',
        'analyze':'Analyze', 'live_disabled':'Auto-clicking requires validation in the next stage.',
        'topmost':'Always on top', 'nodes':'Nodes', 'targets':'Targets', 'matched':'Icons',
        'state':'State', 'confidence':'State score', 'level':'Level',
        'ready':'Ready to analyze', 'busy':'Analyzing image…', 'analysis_done':'Analysis complete',
        'error':'Error', 'debug':'DIAGNOSTICS', 'preview':'Preview', 'details':'Nodes and confidence',
        'empty_preview':'Your analysis preview will appear here', 'number':'#', 'content':'Content',
        'icon_score':'Similarity', 'bloodweb':'Bloodweb', 'prestige':'Prestige',
        'reward':'Reward / OK', 'refresh':'Waiting for a new web', 'unknown':'Unknown',
        'available':'Available', 'purchased':'Purchased', 'absorbed':'Absorbed',
        'locked':'Locked', 'center':'Center', 'uncertain':'Uncertain', 'perk':'Perk',
        'item':'Item', 'addon':'Add-on', 'offering':'Offering',
        'game_not_found':'A visible Dead by Daylight window was not found.',
        'game_occluded':'The game window is covered. Place the game and this app side by side.',
        'static_read_only':'Analysis without mouse control.', 'level_ocr_unavailable':'Level number reading is not connected yet.',
        'center_not_found':'Bloodweb center not found. Image is unconfirmed.',
        'reward_reference_matched':'Reward dialog and OK text matched the reference.',
        'uncertain_icons':'Uncertain icons: {count}.',
        'plan':'Route preview', 'priority':'Selected target · node {node}',
        'filler':'Complete the web · node {node}', 'no_confident_action':'No confident action',
        'no_action':'No action', 'debug_saved':'PNG and JSON: debug/results',
        'logs':'Log', 'last_action':'Last action', 'select_file':'Choose a Bloodweb screenshot',
        'analysis_failed':'Could not read or analyze the image: {message}',
        'legend':'Gold — available · red — purchased · violet — absorbed',
        'stopped':'Stopped', 'save_failed':'Could not save preferences: {message}',
        'error_details':'Details were written to debug/app.log',
    },
}


from src.core.v2_strings import PAIRS
for key,(ru,en) in PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en

from src.core.v3_strings import PAIRS as V3_PAIRS
for key,(ru,en) in V3_PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en

from src.core.v4_strings import PAIRS as V4_PAIRS
for key,(ru,en) in V4_PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en

from src.core.v42_strings import PAIRS as V42_PAIRS
for key,(ru,en) in V42_PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en


from src.core.v43_strings import PAIRS as V43_PAIRS
for key,(ru,en) in V43_PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en


from src.core.v44_strings import PAIRS as V44_PAIRS
for key,(ru,en) in V44_PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en


from src.core.v45_strings import PAIRS as V45_PAIRS
for key,(ru,en) in V45_PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en


from src.core.v46_strings import PAIRS as V46_PAIRS
for key,(ru,en) in V46_PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en

from src.core.v47_strings import PAIRS as V47_PAIRS
for key,(ru,en) in V47_PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en


from src.core.v48_strings import PAIRS as V48_PAIRS
for key,(ru,en) in V48_PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en


from src.core.v51_strings import PAIRS as V51_PAIRS
for key,(ru,en) in V51_PAIRS.items():
    STRINGS['ru'][key]=ru
    STRINGS['en'][key]=en


for key,ru,en in (
    ('bp_changed_unexpectedly','Резкое изменение BP не соответствует подтверждённой цене. Ввод остановлен.','The BP change exceeds the proven price. Input stopped.'),
    ('runtime_progress','Уровень {level} · Куплено {count} · {speed:.1f} уз./мин\nЦель: {target}','Level {level} · Purchased {count} · {speed:.1f} nodes/min\nTarget: {target}'),
    ('window_geometry_recovery','Обновляю привязку окна и проверяю паутину…','Rebinding the window and verifying the Bloodweb…'),
    ('probe_capture_recovery','Обновляю захват цены без повторной покупки…','Restarting price capture without retrying a purchase…'),
    ('prestige_bp_unreadable','Не удаётся прочитать счётчик BP. Проверьте, что его ничто не закрывает.','Cannot read the BP counter. Check that nothing covers it.'),
    ('match_overlay','Перки союзников в матче','Teammate perks in match'),
    ('start_with_dbd','Запускать вместе с DBD','Start with DBD'),
    ('start_with_dbd_hint','С DBD приложение само откроется в трее с включёнными перками союзников. Покупки начинаются только по Старт.','The app opens in the tray with DBD, teammate perks on. Purchases start only with Start.'),
    ('developer_mode','Режим разработчика','Developer mode'),
    ('level_limit_label','Остановиться через уровней:','Stop after levels:'),
    ('level_limit_none','без ограничения','no limit'),
    ('level_limit_hint','Сколько уровней паутины пройти до остановки. Престиж считается одним уровнем. 0 — без ограничения.','How many Bloodweb levels to complete before stopping. A prestige counts as one level. 0 means no limit.'),
    ('level_limit','Остановлено: пройдено уровней — {count}, как задано.','Stopped: {count} levels completed, as set.'),
    ('overlay_status_lobby','Оверлей: лобби — жду матч','Overlay: lobby — waiting for a match'),
    ('overlay_status_reading','Оверлей: матч — читаю перки (союзников: {count} из 3)','Overlay: match — reading perks ({count} of 3 teammates)'),
    ('overlay_status_locked','Оверлей: матч — перки закреплены','Overlay: match — perks locked'),
    ('overlay_lobby','Лобби: перки союзников убраны, жду следующий матч.','Lobby: teammate perks cleared; waiting for the next match.'),
    ('overlay_reading','Матч: нажмите Tab, чтобы прочитать перки союзников.','Match: press Tab to read teammate perks.'),
    ('overlay_locked','Перки союзников закреплены до лобби.','Teammate perks locked until the lobby.'),
    ('out_of_bloodpoints','Покупки не проходят: три узла подряд проверены как не купленные. Похоже, закончились кровавые очки. Остановлено.','Purchases are not going through: three nodes in a row were verified as not bought. You are probably out of bloodpoints. Stopped.'),
    ('mouse_being_moved','Курсор сдвигается перед нажатием (три раза подряд). Не двигайте мышь во время работы. ЛКМ не нажималась.','The cursor keeps moving before the click (three times in a row). Leave the mouse still while it runs. No click was sent.'),
    ('purchase_not_taken','Покупка не прошла (проверено дважды). Перечитываю паутину и выбираю другой узел.','The purchase did not go through (checked twice). Re-reading the Bloodweb and choosing another node.'),
    ('cursor_position_mismatch','Курсор сдвинулся перед нажатием. ЛКМ не нажата; перечитываю и пробую снова.','The cursor moved before the click. No click was sent; re-reading and trying again.'),
    ('cursor_outside_node','Курсор оказался вне узла перед нажатием. ЛКМ не нажата; перечитываю и пробую снова.','The cursor left the node before the click. No click was sent; re-reading and trying again.'),
    ('overlay_status_off','Оверлей: выключен','Overlay: off'),
    ('overlay_status_waiting','Оверлей: ждёт Tab в матче','Overlay: waiting for Tab in a match'),
    ('overlay_status_showing','Оверлей: показаны союзники — {count}','Overlay: showing {count} teammates'),
    ('overlay_status_error','Оверлей: ошибка, продолжаю попытки (подробности в журнале)','Overlay: error – retrying (details in the log)'),
    ('overlay_status_no_names','ники недоступны, только портреты','nicknames unavailable, portraits only'),
    ('overlay_error','Ошибка оверлея; он продолжает работать. Подробности в debug/overlay.','Overlay error; it keeps running. Details in debug/overlay.'),
    ('overlay_size','Размер перков союзников','Teammate perk size'),
    ('overlay_size_large','Крупный','Large'),
    ('overlay_size_normal','Обычный','Normal'),
    ('hotkey_hint','{toggle} — старт · {stop} — стоп','{toggle} Start · {stop} Stop'),
    ('start_with_dbd_on','Включено: при входе в Windows запустится наблюдатель; с DBD приложение откроется в трее. Покупки сами не начинаются.','On: a watcher starts at Windows logon and opens the app in the tray with DBD. Purchases never start by themselves.'),
    ('start_with_dbd_off','Запуск вместе с DBD выключен.','Start with DBD is off.'),
    ('start_with_dbd_failed','Не удалось изменить задачу автозапуска. Нужны права администратора.','Could not change the startup task. Administrator rights are needed.'),
    ('tray_open','Открыть окно','Open window'),
    ('tray_start','Старт Bloodweb','Start Bloodweb'),
    ('tray_stop','Стоп Bloodweb','Stop Bloodweb'),
    ('tray_exit','Выход','Exit'),
    ('match_overlay_hint','В матче откройте Tab: перки союзников появятся слева от их портретов. Перки закрепляются до лобби. Только захват экрана, без ввода в игру. Нужен оконный режим без рамки. На время покупок Bloodweb отключается.','In a match, open Tab: teammates\' perks appear left of their portraits. Perks stay locked until the lobby. Screen capture only, no input to the game. Needs borderless windowed mode. Off during Bloodweb purchases.'),
    ('overlay_ready','Оверлей готов: в матче откройте Tab.','Overlay ready: open Tab in a match.'),
    ('overlay_hotkeys_failed','Часть клавиш оверлея занята другой программой.','Some overlay shortcuts are taken by another program.'),
    ('overlay_blocked_automation','Оверлей выключен на время покупок Bloodweb.','The overlay is off during Bloodweb purchases.'),
    ('overlay_game_not_focused','Оверлей: окно DBD не в фокусе, кадр не снят.','Overlay: DBD is not in focus, no frame taken.'),
    ('overlay_exclusive_fullscreen','Оверлей не виден в исключительном полноэкранном режиме. Включите в DBD оконный режим без рамки.','The overlay cannot appear over exclusive fullscreen. Set DBD to borderless windowed.'),
    ('overlay_cleared','Матч закончился: перки союзников убраны.','Match over: teammate perks cleared.'),
    ('overlay_capture_failed','Оверлей: кадр не удалось снять или прочитать.','Overlay: the frame could not be captured or read.'),
    ('overlay_not_scoreboard','Оверлей: экран Tab не появился. Подержите Tab чуть дольше.','Overlay: the Tab screen did not appear. Hold Tab a little longer.'),
    ('overlay_key','Клавиша экрана матча…','Match details key…'),
    ('overlay_key_prompt','Клавиша, открывающая экран матча в DBD (по умолчанию TAB):','Key that opens DBD\'s match details (default TAB):'),
    ('overlay_key_invalid','Неизвестная клавиша. Примеры: TAB, CAPSLOCK, F2, Q.','Unknown key. Examples: TAB, CAPSLOCK, F2, Q.'),
    ('overlay_showing','Перки союзников показаны.','Teammate perks shown.'),
    ('supervisor_recovery','Восстановление ({reason}), попытка {attempt}/{limit}. Повторных покупок нет.','Recovering ({reason}), attempt {attempt}/{limit}. No purchase is retried.'),
    ('recovery_waiting_game','Жду окно DBD в фокусе ({reason}). Покупки приостановлены, F9 — стоп.','Waiting for the DBD window in focus ({reason}). Purchases are on hold, F9 stops.'),
    ('probe_visible_fallback','Проверяю цену по видимой области игры…','Checking the price in the visible game area…'),
    ('chain_budget_limit','Достигнут лимит BP этой серии с учётом резервов. Покупки остановлены.','This run’s BP limit, including reservations, has been reached. Purchases stopped.'),
    ('chain_unexpected_purchase','Результат цепочки не совпал с ожидаемыми узлами. Ввод остановлен для проверки.','The chain result does not match its expected nodes. Input stopped for verification.'),
    ('series_scope_invalid','Параметры разрешённой тестовой серии некорректны. Покупки не начаты.','The authorized test run has invalid settings. Purchases did not start.'),
    ('permission_scope_already_running','Эта тестовая серия уже открыта в другом окне AutoBloodWeb.','This test run is already open in another AutoBloodWeb window.'),
    ('repeat_clicks','Повтор 100 мс (×3)','Repeat 100 ms (×3)'),
    ('input_mode_calibration_required','Новый способ покупки ещё не проверен. Открыта проверка одной тестовой покупки.','The new purchase method is not verified yet. The one-purchase test is open.'),
    ('repeat_calibration_notice','Проверяется режим «Повтор 100 мс»: до трёх коротких кликов на одном выбранном узле. Открытие окна ничего не покупает. Для начала нужны свежий анализ, проверка F9 и ваше разрешение ниже.','Testing Repeat 100 ms: up to three short clicks on one selected node. Opening this window buys nothing. Starting requires a fresh scan, an F9 check and your approval below.'),
    ('prestige_animation','Ожидание анимации престижа…','Waiting for prestige animation…'),
    ('reward_dismissing','Закрытие окна наград…','Closing the rewards dialog…'),
    ('tutorial','Подсказка паутины','Bloodweb tutorial'),
    ('tutorial_dismissing','Продолжение по CONTINUE…','Continuing via CONTINUE…'),
    ('tutorial_not_dismissed','Подсказка не закрылась после одного клика. Нужна ручная проверка.','The tutorial stayed open after one click. Manual review is required.'),
    ('modal_attempt_limit','Окно наград не закрылось после пяти попыток.','Rewards dialog stayed open after five attempts.'),
    ('modal_recovery_timeout','Истекло время ожидания закрытия окна наград.','Timed out waiting for the rewards dialog to close.'),
    ('automatic_unlock_state_contradiction','Состояние подтверждённого узла противоречит новому кадру.','A confirmed node conflicts with the new observation.'),
    ('single_standard_price_bound_unproven','Цена выбранного узла не подтверждена для лимита BP этой серии.','The selected node price is unproven for this run’s BP cap.'),
):
    STRINGS['ru'][key]=ru;STRINGS['en'][key]=en

from src.core.locale_extra import EXTRA
STRINGS.update(EXTRA)


class Localization:
    def __init__(self,language:str='ru'):
        self.language=language if language in STRINGS else 'ru'

    def text(self,key:str,**values) -> str:
        return STRINGS[self.language].get(key,STRINGS['en'].get(key,key)).format(**values)

    def diagnostic(self,message:str) -> str:
        key,_,value=message.partition(':')
        return self.text(key,count=value) if key=='uncertain_icons' else self.text(key)

from src.core.notice_strings import PAIRS as NOTICE_PAIRS
for key,(ru,en) in NOTICE_PAIRS.items():
    STRINGS['ru'][key]=ru;STRINGS['en'][key]=en

from src.core.ui_strings import PAIRS as UI_PAIRS
for key,(ru,en) in UI_PAIRS.items():
    STRINGS['ru'][key]=ru;STRINGS['en'][key]=en

from src.core.meme_strings import LANGUAGES as MEME_LANGUAGES,TEXT as MEME_TEXT
for key,values in MEME_TEXT.items():
    for code,value in zip(MEME_LANGUAGES,values):STRINGS[code][key]=value
