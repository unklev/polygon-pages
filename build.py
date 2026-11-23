#!/usr/bin/env python3
"""
Полигон — генератор страниц турниров.

Берёт лист «Турниры» (опубликованный CSV Google-таблицы) и собирает статический сайт:
  /t/<ID>/            — страница турнира (+ event.ics для календаря)
  /goroda/<город>/    — турниры города
  /uroven/<уровень>/  — турниры по уровню (новички, дети, мастера…)
  /vid/<вид спорта>/  — кроссфит, ФМ, ГТО, фитнес-гонки, тяжёлая атлетика
  /za-rubezhom/, /arhiv/, sitemap.xml, robots.txt, 404.html

Запуск:  python build.py                  — данные из таблицы
         python build.py sample_data.csv  — данные из локального файла
Только стандартная библиотека Python 3.9+.
"""
import csv
import datetime as dt
import html
import io
import json
import os
import re
import shutil
import sys
import urllib.request
from collections import defaultdict
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
    TODAY = dt.datetime.now(ZoneInfo('Europe/Moscow')).date()
except Exception:  # pragma: no cover
    TODAY = dt.date.today()

# ---------------- настройки ----------------
CSV_URLS = [
    os.environ.get('POLYGON_CSV') or 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQTaWxY5CS4Qy09NeHkrXgBYRvDGADHAxi8s20EQuMDsv34u_D8TbSDJ0PhzD8BTwYUbiD7E96LdgeY/pub?gid=769382050&single=true&output=csv',
    # запасной источник: та же таблица через Apps Script
    'https://script.google.com/macros/s/AKfycbyGgRhjg6cIgroXVK4XWciZ-jAIpkbPZv9iB4nMYm0eYRG1O1Jdfd63bM8dzRZuonLYrQ/exec?csv=1',
]
# Подтверждение прав в Яндекс Вебмастере / Google Search Console (метатеги на всех страницах)
VERIFY = {'yandex-verification': '39d51cf908f446ab'}
VERIFY_META = ''.join('<meta name="%s" content="%s">' % kv for kv in VERIFY.items())
SITE = (os.environ.get('POLYGON_PAGES_URL') or 'https://turniry.thepolygon.ru').rstrip('/')
MAIN = 'https://thepolygon.ru/'
TG = 'https://t.me/thepolygonnews'
OUT = os.environ.get('POLYGON_OUT') or 'public'
BRAND = 'Полигон'

MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
MONTHS_NOM = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']
WEEKDAYS = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']

SPORTS = {  # значение в таблице → (адрес, заголовок, в родительном падеже для «турниры по …»)
    'Кроссфит': ('krossfit', 'Кроссфит', 'кроссфиту'),
    'Функциональное многоборье': ('funkcionalnoe-mnogobore', 'Функциональное многоборье', 'функциональному многоборью'),
    'Многоборье ГТО': ('gto', 'Многоборье ГТО', 'многоборью ГТО'),
    'Фитнес-гонка': ('fitnes-gonki', 'Фитнес-гонки', 'фитнес-гонкам'),
    'Тяжёлая атлетика': ('tyazhelaya-atletika', 'Тяжёлая атлетика', 'тяжёлой атлетике'),
}
LEVELS = {
    'Новички': ('novichki', 'Турниры для новичков', 'Первые старты: базовые движения, умеренные веса, без сложной гимнастики.'),
    'Любители': ('lyubiteli', 'Турниры для любителей', 'Scaled и Intermediate: есть соревновательный опыт, подтягивания, двойные прыжки, штанга средних весов.'),
    'RX/Профи': ('rx', 'Турниры RX и для профи', 'Полный арсенал: выходы силой, ходьба на руках, тяжёлая штанга.'),
    'Элита': ('elita', 'Элитные турниры', 'Сильнейшие атлеты, часто с отбором и денежным призовым фондом.'),
    'Мастера': ('mastera', 'Турниры для мастеров 35+', 'Отдельный зачёт по возрасту: Masters 35+, 40+ и старше.'),
    'Дети и подростки': ('deti', 'Детские и юношеские соревнования', 'Возрастные группы по году рождения, обычно от 5 до 17 лет.'),
}
COLS = {'id': 'ID', 'title': 'Название', 'start': 'Дата начала', 'end': 'Дата окончания', 'qs': 'Онлайн-отбор с',
        'qe': 'Онлайн-отбор до', 'city': 'Город', 'place': 'Место (подробно)', 'online': 'Онлайн', 'sport': 'Вид спорта',
        'levels': 'Уровни', 'format': 'Формат', 'fee': 'Взнос', 'prize': 'Призы', 'deadline': 'Регистрация до',
        'link': 'Ссылка организатора', 'img': 'Картинка', 'descr': 'Описание', 'show': 'Показывать',
        'country': 'Страна', 'll': 'Координаты', 'reg': 'Ссылка на регистрацию'}

TR = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
              ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p', 'r', 's', 't', 'u', 'f',
               'h', 'c', 'ch', 'sh', 'sch', '', 'y', '', 'e', 'yu', 'ya']))


# ---------------- данные ----------------
def slug(s):
    s = ''.join(TR.get(ch, ch) for ch in str(s).lower())
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-') or 'x'


def pdate(s):
    s = (s or '').strip()
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})', s) or None
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r'^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})', s)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def url_ok(u):
    u = (u or '').strip()
    return u if re.match(r'^https?://\S+$', u) else ''


def load_csv(argv):
    if len(argv) > 1 and os.path.exists(argv[1]):
        with open(argv[1], encoding='utf-8-sig') as f:
            return f.read()
    last = None
    for u in CSV_URLS:
        try:
            req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 polygon-pages'})
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode('utf-8-sig')
            if 'Название' in text[:400]:
                return text
        except Exception as e:  # пробуем следующий источник
            last = e
    sys.exit('Не удалось загрузить таблицу: %s' % last)


def parse(text):
    rows = list(csv.reader(io.StringIO(text)))
    head = [h.strip() for h in rows[0]]
    idx = {k: (head.index(v) if v in head else -1) for k, v in COLS.items()}
    events, seen = [], set()
    for r in rows[1:]:
        g = lambda k: (r[idx[k]].strip() if idx[k] >= 0 and idx[k] < len(r) else '')
        title, start = g('title'), pdate(g('start'))
        if not title or not start or g('show').lower() in ('нет', 'no', 'false', '0', 'скрыть'):
            continue
        end = pdate(g('end')) or start
        if end < start:
            end = start
        eid = re.sub(r'[^\w-]', '', g('id')) or slug(title + '-' + start.isoformat())
        if eid in seen:
            continue
        seen.add(eid)
        city_raw = g('city') or 'Город уточняется'
        online = g('online').lower() in ('да', 'yes', '1', 'true') or city_raw.lower() == 'онлайн'
        country = g('country')
        abroad = bool(country) and not re.match(r'^(росси|рф$)', country, re.I)
        city = 'Онлайн' if online else city_raw
        city_short = city.split(',')[0].strip()
        sport = g('sport') if g('sport') in SPORTS else ('Кроссфит' if not g('sport') else 'Другое')
        events.append({
            'id': eid, 'title': title, 'start': start, 'end': end, 'qs': pdate(g('qs')), 'qe': pdate(g('qe')),
            'city': city, 'city_short': city_short, 'place': g('place'), 'online': online, 'country': country, 'abroad': abroad,
            'sport': sport, 'levels': [x.strip() for x in g('levels').split(',') if x.strip()],
            'format': [x.strip() for x in g('format').split(',') if x.strip()],
            'fee': g('fee'), 'prize': g('prize'), 'deadline': pdate(g('deadline')),
            'link': url_ok(g('link')), 'reg': url_ok(g('reg')), 'img': url_ok(g('img')), 'descr': g('descr'),
            'past': end < TODAY,
        })
    events.sort(key=lambda e: (e['start'], e['title']))
    return events


# ---------------- форматирование ----------------
E = lambda s: html.escape(str(s or ''), quote=True)


def fdate(d, year=True):
    return '%d %s%s' % (d.day, MONTHS[d.month - 1], (' %d' % d.year) if year else '')


def frange(a, b):
    if a == b:
        return fdate(a)
    if a.year == b.year and a.month == b.month:
        return '%d–%d %s %d' % (a.day, b.day, MONTHS[a.month - 1], a.year)
    if a.year == b.year:
        return '%s — %s' % (fdate(a, False), fdate(b))
    return '%s — %s' % (fdate(a), fdate(b))


def plural(n, one, few, many):
    n10, n100 = n % 10, n % 100
    return one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many


def status(e):
    if e['past']:
        return 'off', 'Турнир прошёл'
    days = (e['start'] - TODAY).days
    if e['deadline'] and e['deadline'] < TODAY:
        return 'off', 'Регистрация закрыта'
    if e['deadline'] and (e['deadline'] - TODAY).days <= 10:
        return 'hot', 'Регистрация до ' + fdate(e['deadline'], False)
    if days <= 0:
        return 'hot', 'Идёт сейчас'
    if days <= 14:
        return 'hot', 'Старт через %d %s' % (days, plural(days, 'день', 'дня', 'дней'))
    return 'ok', 'Старт ' + fdate(e['start'], False)


def linkify(text):
    out, pos = [], 0
    for m in re.finditer(r'https?://[^\s<>"«»]+', text):
        u = m.group(0).rstrip('.,;:!?)')
        out.append(E(text[pos:m.start()]))
        out.append('<a href="%s" rel="nofollow noopener" target="_blank">%s</a>' % (E(u), E(u if len(u) < 60 else u[:57] + '…')))
        pos = m.start() + len(u)
    out.append(E(text[pos:]))
    return ''.join(out)


def plain(text, n):
    t = re.sub(r'https?://\S+', '', text or '')
    t = re.sub(r'[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200D]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t if len(t) <= n else t[:n - 1].rsplit(' ', 1)[0] + '…'


def kind_word(e):
    return {'Кроссфит': 'турнир по кроссфиту', 'Функциональное многоборье': 'турнир по функциональному многоборью',
            'Многоборье ГТО': 'соревнования по многоборью ГТО', 'Фитнес-гонка': 'фитнес-гонка',
            'Тяжёлая атлетика': 'турнир по тяжёлой атлетике'}.get(e['sport'], 'соревнования')


def ev_url(e):
    return '/t/%s/' % e['id']


# ---------------- шаблон ----------------
CSS = r"""
:root{--red:#E20B2D;--ink:#131313;--paper:#fff;--gray:#F5F5F5;--rule:#D9D9D9;--muted:#666;
--display:'Oswald','Arial Narrow',sans-serif;--serif:'PT Serif',Georgia,serif;--sans:Arial,'Helvetica Neue',sans-serif}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
a{color:inherit}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px}
.top{border-bottom:3px solid var(--ink)}
.top .wrap{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:72px}
.logo{font-family:var(--display);font-weight:700;font-size:30px;letter-spacing:.02em;text-decoration:none;text-transform:uppercase}
.logo small{display:inline-block;margin-left:12px;padding-left:12px;border-left:2px solid var(--ink);font-size:12px;line-height:1.2;vertical-align:middle;letter-spacing:.08em;font-weight:500;max-width:150px;white-space:normal}
.top nav{display:flex;gap:8px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 16px;font-family:var(--display);font-size:15px;letter-spacing:.08em;text-transform:uppercase;text-decoration:none;border:1px solid var(--red);background:var(--red);color:#fff;cursor:pointer}
.btn.ghost{background:var(--paper);border-color:var(--ink);color:var(--ink)}
.btn.sm{min-height:36px;font-size:13px;padding:0 12px}
.crumbs{font-size:13px;color:var(--muted);margin:22px 0 0}
.crumbs a{color:var(--muted)}
.kicker{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--red);margin-top:18px}
h1{font-family:var(--display);font-weight:600;font-size:clamp(34px,5vw,60px);line-height:1.02;text-transform:uppercase;margin:8px 0 10px;overflow-wrap:anywhere}
h2{font-family:var(--display);font-weight:600;font-size:30px;text-transform:uppercase;margin:0;border-bottom:3px solid var(--ink);padding-bottom:6px}
.dateline{font-family:var(--serif);font-size:20px;color:#333;display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center}
.st{font-family:var(--display);font-size:13px;letter-spacing:.08em;text-transform:uppercase;padding:4px 10px}
.st.hot{background:var(--red);color:#fff}.st.ok{background:var(--ink);color:#fff}.st.off{border:1px solid var(--rule);color:var(--muted)}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:36px;margin:28px 0 48px;border-top:6px solid var(--red);padding-top:22px}
.text{font-family:var(--serif);font-size:18px;line-height:1.65;white-space:pre-line;overflow-wrap:anywhere}
.text a{color:var(--red)}
.poster{width:100%;display:block;margin-bottom:14px;background:var(--gray)}
dl.facts{margin:0;border-top:3px solid var(--ink)}
dl.facts div{padding:10px 0;border-bottom:1px solid var(--rule)}
dt{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
dd{margin:2px 0 0;overflow-wrap:anywhere}
.actions{display:flex;flex-direction:column;gap:8px;margin-top:16px}
.note{font-family:var(--serif);font-style:italic;font-size:14px;color:var(--muted)}
.list{margin:0 0 40px;padding:0;list-style:none}
.month{font-family:var(--display);font-weight:700;font-size:48px;line-height:1;text-transform:uppercase;color:var(--red);margin:36px 0 0;border-bottom:1px solid var(--ink);padding-bottom:6px}
.row{display:grid;grid-template-columns:96px minmax(0,1fr) auto;gap:8px 22px;align-items:center;padding:16px 0;border-bottom:1px solid var(--rule)}
.day{font-family:var(--display);font-weight:700;font-size:40px;line-height:1}
.day small{display:block;font-size:12px;letter-spacing:.12em;color:var(--muted);text-transform:uppercase;font-weight:500;margin-top:4px}
.row a.t{font-family:var(--display);font-weight:600;font-size:23px;line-height:1.1;text-transform:uppercase;text-decoration:none;overflow-wrap:anywhere}
.row a.t:hover{color:var(--red)}
.meta{font-family:var(--serif);color:#444;font-size:15px}
.lead{font-family:var(--serif);font-size:19px;color:#333;max-width:780px;margin:0 0 8px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 4px}
.chips a{font-family:var(--display);font-size:14px;letter-spacing:.06em;text-transform:uppercase;text-decoration:none;border:1px solid var(--ink);padding:6px 12px}
.chips a:hover{background:var(--ink);color:#fff}
.chips a span{color:var(--red);margin-left:6px}
section{margin-top:44px}
.foot{margin-top:60px;border-top:3px solid var(--ink);padding:26px 0 40px;font-size:14px;color:var(--muted)}
.foot a{color:var(--ink)}
.cta{background:var(--gray);padding:22px;margin:40px 0 0;display:flex;flex-wrap:wrap;gap:12px 24px;align-items:center;justify-content:space-between}
.cta b{font-family:var(--display);font-size:22px;text-transform:uppercase;font-weight:600}
@media (max-width:760px){.grid{grid-template-columns:1fr}.row{grid-template-columns:64px minmax(0,1fr)}.row .st{grid-column:2;justify-self:start}.day{font-size:30px}.top nav .btn.ghost{display:none}.logo small{display:none}}
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=PT+Serif:ital,wght@0,400;0,700;1,400&display=swap">')


def page(path, title, desc, body, image='', jsonld=None, noindex=False):
    canon = SITE + path
    ld = ''.join('<script type="application/ld+json">%s</script>' % json.dumps(x, ensure_ascii=False) for x in (jsonld or []))
    return ('<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            + VERIFY_META +
            '<title>%s</title><meta name="description" content="%s"><link rel="canonical" href="%s">%s'
            '<meta property="og:type" content="website"><meta property="og:site_name" content="%s"><meta property="og:title" content="%s">'
            '<meta property="og:description" content="%s"><meta property="og:url" content="%s">%s'
            '<link rel="icon" href="%sfavicon.ico">%s<link rel="stylesheet" href="/assets/style.css">%s</head><body>'
            '<header class="top"><div class="wrap"><a class="logo" href="/">Полигон<small>Встретимся на соревнованиях!</small></a>'
            '<nav><a class="btn ghost sm" href="%s">Календарь</a><a class="btn sm" href="%s#popup:myform">Разместить турнир</a></nav></div></header>'
            '<main class="wrap">%s</main>'
            '<footer class="wrap foot">«Полигон» — бесплатная площадка с анонсами соревнований по кроссфиту, функциональному многоборью и ГТО в России и СНГ. '
            '<a href="%s">Интерактивный календарь</a> · <a href="%s">Telegram-канал</a> · <a href="/arhiv/">Архив</a> · '
            '<a href="%s#popup:myform">Разместить турнир бесплатно</a></footer></body></html>') % (
        E(title), E(desc), E(canon), '<meta name="robots" content="noindex">' if noindex else '',
        BRAND, E(title), E(desc), E(canon), ('<meta property="og:image" content="%s">' % E(image)) if image else '',
        MAIN, FONTS, ld, MAIN, MAIN, body, MAIN, TG, MAIN)


BASE = urlparse(SITE).path.rstrip('/')  # для адреса вида user.github.io/polygon-pages


def write(path, content):
    if BASE and path.endswith(('/', '.html')):
        content = content.replace('href="/', 'href="%s/' % BASE).replace('src="/', 'src="%s/' % BASE)
    full = os.path.join(OUT, path.strip('/'), 'index.html') if path.endswith('/') else os.path.join(OUT, path.strip('/'))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8') as f:
        f.write(content)


# ---------------- страницы ----------------
def row_html(e):
    c, t = status(e)
    d = e['start']
    day = ('%d–%d' % (d.day, e['end'].day)) if e['end'] != d and e['end'].month == d.month else str(d.day)
    meta = ' · '.join(x for x in [e['city'], e['sport'], ', '.join(e['levels']), ', '.join(e['format'])] if x)
    return ('<li class="row"><div class="day">%s<small>%s</small></div><div><a class="t" href="%s">%s</a>'
            '<div class="meta">%s</div></div><span class="st %s">%s</span></li>') % (
        E(day), E(MONTHS[d.month - 1][:3] + ' ' + str(d.year)), ev_url(e), E(e['title']), E(meta), c, E(t))


def list_html(evs, by_month=True):
    if not evs:
        return '<p class="lead">Пока нет турниров. Загляните позже или подпишитесь на <a href="%s">Telegram-канал</a>.</p>' % TG
    if not by_month:
        return '<ul class="list">%s</ul>' % ''.join(row_html(e) for e in evs)
    out, cur = [], None
    for e in evs:
        k = (e['start'].year, e['start'].month)
        if k != cur:
            if cur:
                out.append('</ul>')
            out.append('<div class="month">%s %d</div><ul class="list">' % (MONTHS_NOM[k[1] - 1], k[0]))
            cur = k
        out.append(row_html(e))
    out.append('</ul>')
    return ''.join(out)


def cta():
    return ('<div class="cta"><b>Проводите турнир?</b><span class="note">Разместите его в календаре «Полигона» бесплатно — '
            'анонс увидят атлеты по всей России и в Telegram-канале.</span><a class="btn" href="%s#popup:myform">Разместить турнир</a></div>') % MAIN


def event_page(e, city_pages, all_ev):
    c, st = status(e)
    reg = e['reg'] or e['link']
    where = 'онлайн' if e['online'] else e['city']
    title = '%s — %s, %s, %s' % (e['title'], kind_word(e), where, frange(e['start'], e['end']))
    desc = plain('%s. %s. %s' % (frange(e['start'], e['end']), where, e['descr']), 160)
    facts = [('Когда', frange(e['start'], e['end']) + ', ' + WEEKDAYS[e['start'].weekday()])]
    place = e['place'] if (not e['place'] or e['city_short'].lower() in e['place'].lower()) else e['place'] + ', ' + e['city']
    facts.append(('Где', 'Онлайн' if e['online'] else (place or e['city'])))
    if e['qs'] or e['qe']:
        facts.append(('Онлайн-отбор', frange(e['qs'] or e['qe'], e['qe'] or e['qs'])))
    facts.append(('Вид спорта', e['sport']))
    if e['levels']:
        facts.append(('Уровни', ', '.join(e['levels'])))
    if e['format']:
        facts.append(('Формат', ', '.join(e['format'])))
    if e['deadline']:
        facts.append(('Регистрация', 'до ' + fdate(e['deadline'])))
    facts.append(('Взнос', e['fee'] or 'уточняется'))
    if e['prize']:
        facts.append(('Призы', e['prize']))
    dl = ''.join('<div><dt>%s</dt><dd>%s</dd></div>' % (E(a), E(b)) for a, b in facts)
    acts = []
    if not e['past'] and reg:
        acts.append('<a class="btn" href="%s" target="_blank" rel="nofollow noopener">Регистрация</a>' % E(reg))
    if e['link'] and (e['past'] or e['link'] != reg):
        acts.append('<a class="btn ghost" href="%s" target="_blank" rel="nofollow noopener">%s</a>' % (E(e['link']), 'Страница организатора' if e['past'] else 'Анонс организатора'))
    if not e['past']:
        acts.append('<a class="btn ghost" href="%sevent.ics" download>Добавить в календарь</a>' % ev_url(e))
    acts.append('<a class="btn ghost" href="%s#event-%s">Открыть в календаре «Полигона»</a>' % (MAIN, E(e['id'])))
    city_link = ('<a href="/goroda/%s/">%s</a> → ' % (slug(e['city_short']), E(e['city_short']))) if slug(e['city_short']) in city_pages else ''
    if e['abroad']:
        city_link = '<a href="/za-rubezhom/">За рубежом</a> → ' + city_link
    more = [x for x in all_ev if not x['past'] and x['id'] != e['id'] and x['city_short'] == e['city_short']][:5]
    if len(more) < 3:
        more += [x for x in all_ev if not x['past'] and x['id'] != e['id'] and x not in more and x['abroad'] == e['abroad']][:5 - len(more)]
    body = ('<div class="crumbs"><a href="/">Все турниры</a> → %s%s</div>'
            '<div class="kicker">%s · %s</div><h1>%s</h1>'
            '<div class="dateline"><span>%s, %s</span><span class="st %s">%s</span></div>'
            '<div class="grid"><div class="text">%s</div><aside>%s<dl class="facts">%s</dl><div class="actions">%s'
            '<p class="note">Условия могут меняться — уточняйте у организатора.</p></div></aside></div>'
            '<section><h2>%s</h2>%s</section>%s') % (
        city_link, E(e['title']), E(e['sport']), E(where), E(e['title']),
        E(frange(e['start'], e['end'])), E(where), c, E(st),
        linkify(e['descr']) if e['descr'] else '<p class="note">Подробное описание — на странице организатора.</p>',
        ('<img class="poster" src="%s" alt="Афиша: %s" loading="lazy">' % (E(e['img']), E(e['title']))) if e['img'] else '',
        dl, ''.join(acts), 'Ещё турниры' if more else '', list_html(more, False) if more else '', cta())
    ld = {'@context': 'https://schema.org', '@type': 'SportsEvent', 'name': e['title'], 'sport': e['sport'],
          'startDate': e['start'].isoformat(), 'endDate': e['end'].isoformat(), 'url': SITE + ev_url(e),
          'eventStatus': 'https://schema.org/EventScheduled',
          'eventAttendanceMode': 'https://schema.org/OnlineEventAttendanceMode' if e['online'] else 'https://schema.org/OfflineEventAttendanceMode',
          'description': plain(e['descr'], 500) or title}
    if e['online']:
        ld['location'] = {'@type': 'VirtualLocation', 'url': reg or SITE + ev_url(e)}
    else:
        ld['location'] = {'@type': 'Place', 'name': e['place'] or e['city'],
                          'address': {'@type': 'PostalAddress', 'addressLocality': e['city_short'],
                                      'streetAddress': e['place'] or e['city'], 'addressCountry': e['country'] or 'RU'}}
    if e['img']:
        ld['image'] = [e['img']]
    if reg:
        ld['offers'] = {'@type': 'Offer', 'url': reg, 'availability': 'https://schema.org/SoldOut' if (e['deadline'] and e['deadline'] < TODAY) else 'https://schema.org/InStock'}
    ld['organizer'] = {'@type': 'Organization', 'name': BRAND if not e['link'] else urlparse(e['link']).netloc, 'url': e['link'] or MAIN}
    crumbs = {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': 1, 'name': 'Все турниры', 'item': SITE + '/'},
        {'@type': 'ListItem', 'position': 2, 'name': e['title'], 'item': SITE + ev_url(e)}]}
    write(ev_url(e), page(ev_url(e), title + ' | ' + BRAND, desc, body, e['img'], [ld, crumbs]))
    write(ev_url(e) + 'event.ics', ics(e))


def ics(e):
    def esc(s):
        return str(s or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//thepolygon.ru//pages//RU', 'CALSCALE:GREGORIAN', 'BEGIN:VEVENT',
             'UID:%s@thepolygon.ru' % e['id'], 'DTSTAMP:%s' % dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
             'DTSTART;VALUE=DATE:%s' % e['start'].strftime('%Y%m%d'),
             'DTEND;VALUE=DATE:%s' % (e['end'] + dt.timedelta(days=1)).strftime('%Y%m%d'),
             'SUMMARY:%s' % esc(e['title']), 'LOCATION:%s' % esc('Онлайн' if e['online'] else ', '.join(x for x in [e['place'], e['city']] if x)),
             'URL:%s' % (SITE + ev_url(e)), 'DESCRIPTION:%s' % esc(plain(e['descr'], 600) + '\n' + SITE + ev_url(e)), 'END:VEVENT', 'END:VCALENDAR']
    return '\r\n'.join(lines) + '\r\n'


def listing(path, h1, lead, evs, title, desc, extra=''):
    up = [e for e in evs if not e['past']]
    past = sorted([e for e in evs if e['past']], key=lambda e: e['start'], reverse=True)
    body = '<div class="crumbs"><a href="/">Все турниры</a></div><h1>%s</h1><p class="lead">%s</p>%s' % (E(h1), E(lead), extra)
    body += '<section><h2>Предстоящие · %d</h2>%s</section>' % (len(up), list_html(up))
    if past:
        body += '<section><h2>Прошедшие · %d</h2>%s</section>' % (len(past), list_html(past[:60], False))
    body += cta()
    items = {'@context': 'https://schema.org', '@type': 'ItemList', 'itemListElement': [
        {'@type': 'ListItem', 'position': i + 1, 'url': SITE + ev_url(e), 'name': e['title']} for i, e in enumerate(up[:50])]}
    write(path, page(path, title, desc, body, '', [items], noindex=not evs))


def build(events):
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, 'assets'))
    with open(os.path.join(OUT, 'assets', 'style.css'), 'w', encoding='utf-8') as f:
        f.write(CSS.strip())
    urls = []

    ru = [e for e in events if not e['abroad']]
    by_city = defaultdict(list)
    for e in ru:
        if not e['online'] and e['city'] != 'Город уточняется':
            by_city[e['city_short']].append(e)
    city_pages = {slug(c): c for c, l in by_city.items() if len(l) >= 2 or any(not x['past'] for x in l)}

    for e in events:
        event_page(e, city_pages, events)
        urls.append((ev_url(e), 'weekly' if not e['past'] else 'yearly', '0.8' if not e['past'] else '0.4'))

    for s, c in city_pages.items():
        l = by_city[c]
        n = sum(1 for e in l if not e['past'])
        listing('/goroda/%s/' % s, 'Соревнования: %s' % c,
                'Турниры по кроссфиту, функциональному многоборью и ГТО в городе %s — даты, уровни, взносы и регистрация. Сейчас предстоящих: %d.' % (c, n), l,
                'Соревнования по кроссфиту и функциональному многоборью — %s, %d | %s' % (c, TODAY.year, BRAND),
                'Календарь турниров по кроссфиту, функциональному многоборью и ГТО: %s. Даты, уровни, взносы, ссылки на регистрацию.' % c)
        urls.append(('/goroda/%s/' % s, 'daily', '0.7'))

    for lv, (s, h1, lead) in LEVELS.items():
        l = [e for e in ru if lv in e['levels']]
        listing('/uroven/%s/' % s, h1, lead + ' Предстоящие турниры в России — со ссылками на регистрацию.', l,
                '%s по кроссфиту и функциональному многоборью, %d | %s' % (h1, TODAY.year, BRAND),
                '%s: кроссфит, функциональное многоборье, ГТО. Даты, города, взносы и регистрация.' % h1)
        urls.append(('/uroven/%s/' % s, 'daily', '0.7'))

    for sp, (s, h1, gen) in SPORTS.items():
        l = [e for e in ru if e['sport'] == sp]
        listing('/vid/%s/' % s, 'Соревнования по %s' % gen, 'Календарь турниров по %s в России: даты, города, уровни, взносы и регистрация.' % gen, l,
                'Соревнования по %s %d — календарь турниров | %s' % (gen, TODAY.year, BRAND),
                'Все предстоящие турниры по %s в России и СНГ: даты, города, уровни и ссылки на регистрацию.' % gen)
        urls.append(('/vid/%s/' % s, 'daily', '0.7'))

    ab = [e for e in events if e['abroad']]
    listing('/za-rubezhom/', 'Турниры за рубежом', 'Соревнования по кроссфиту и фитнес-гонкам в странах, куда просто доехать: ОАЭ, Турция, Израиль, Египет, Казахстан, Таиланд и другие.', ab,
            'Турниры по кроссфиту за рубежом — %d | %s' % (TODAY.year, BRAND), 'Зарубежные турниры по кроссфиту и фитнес-гонкам: даты, города, взносы, регистрация.')
    urls.append(('/za-rubezhom/', 'daily', '0.6'))

    past = sorted([e for e in events if e['past']], key=lambda e: e['start'], reverse=True)
    body = '<div class="crumbs"><a href="/">Все турниры</a></div><h1>Архив турниров</h1><p class="lead">Все прошедшие соревнования из календаря «Полигона» — %d %s.</p>%s' % (
        len(past), plural(len(past), 'турнир', 'турнира', 'турниров'), list_html(past, False))
    write('/arhiv/', page('/arhiv/', 'Архив турниров по кроссфиту и функциональному многоборью | ' + BRAND, 'Прошедшие соревнования по кроссфиту, ФМ и ГТО из календаря «Полигона».', body))
    urls.append(('/arhiv/', 'weekly', '0.3'))

    # главная
    up = [e for e in events if not e['past'] and not e['abroad']]
    cities = sorted(((c, sum(1 for e in by_city[c] if not e['past'])) for c in city_pages.values()), key=lambda x: (-x[1], x[0]))
    chips = lambda items: '<div class="chips">%s</div>' % ''.join('<a href="%s">%s<span>%s</span></a>' % (h, E(t), n) for h, t, n in items)
    extra = ('<h2 style="margin-top:28px">Города</h2>' + chips([('/goroda/%s/' % slug(c), c, n) for c, n in cities if n] or [('/arhiv/', 'Архив', len(past))]) +
             '<h2 style="margin-top:28px">Уровень</h2>' + chips([('/uroven/%s/' % s, {'Дети и подростки': 'Дети'}.get(lv, lv), sum(1 for e in up if lv in e['levels'])) for lv, (s, h, _) in LEVELS.items()]) +
             '<h2 style="margin-top:28px">Вид спорта</h2>' + chips([('/vid/%s/' % s, h, sum(1 for e in up if e['sport'] == sp)) for sp, (s, h, _) in SPORTS.items()] + [('/za-rubezhom/', 'За рубежом', sum(1 for e in ab if not e['past']))]))
    body = ('<h1>Календарь соревнований по кроссфиту, функциональному многоборью и ГТО</h1>'
            '<p class="lead">%d %s впереди в %d %s России и СНГ. Новички, любители, RX, мастера и дети — выбирайте старт по уровню и городу. '
            'Есть и <a href="%s">интерактивный календарь с фильтрами и картой</a>.</p>%s<section><h2>Ближайшие турниры</h2>%s</section>%s') % (
        len(up), plural(len(up), 'турнир', 'турнира', 'турниров'), len([c for c, n in cities if n]), plural(len([c for c, n in cities if n]), 'городе', 'городах', 'городах'),
        MAIN, extra, list_html(up), cta())
    site_ld = {'@context': 'https://schema.org', '@type': 'WebSite', 'name': BRAND, 'url': SITE + '/', 'inLanguage': 'ru'}
    write('/', page('/', 'Календарь соревнований по кроссфиту, функциональному многоборью и ГТО %d | %s' % (TODAY.year, BRAND),
                    'Все предстоящие турниры по кроссфиту, функциональному многоборью и ГТО в России и СНГ: даты, города, уровни и регистрация.', body, '', [site_ld]))
    urls.insert(0, ('/', 'daily', '1.0'))

    write('/404.html', page('/404.html', 'Страница не найдена | ' + BRAND, 'Страница не найдена',
                            '<h1>Страница не найдена</h1><p class="lead">Возможно, турнир удалили или переименовали. <a href="/">Все турниры</a> · <a href="%s">Календарь</a></p>' % MAIN, noindex=True))
    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sm += ['<url><loc>%s</loc><lastmod>%s</lastmod><changefreq>%s</changefreq><priority>%s</priority></url>' % (E(SITE + u), TODAY.isoformat(), f, p) for u, f, p in urls]
    sm.append('</urlset>')
    write('/sitemap.xml', '\n'.join(sm))
    write('/robots.txt', 'User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n' % SITE)
    host = urlparse(SITE).netloc
    if host and not host.endswith('github.io'):
        write('/CNAME', host + '\n')
    return len(urls)


if __name__ == '__main__':
    evs = parse(load_csv(sys.argv))
    n = build(evs)
    print('Турниров: %d (предстоящих %d), страниц: %d → %s/' % (len(evs), sum(1 for e in evs if not e['past']), n, OUT))
