"""Render the browsable HTML catalogue.

The page embeds the catalogue as JSON and renders client-side. That keeps the
file small regardless of library size, makes search and grouping instant, and
means every value is escaped by the DOM rather than by string concatenation.
"""
import json
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote

from .catalog import Catalog
from .config import Config
from .logging_setup import Colors, get_logger

logger = get_logger()

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<style>
:root {
  --bg: #14161a;
  --surface: #1d2026;
  --surface-2: #262a32;
  --border: #333944;
  --text: #e6e8ec;
  --muted: #9aa2b1;
  --accent: #5aa7f0;
  --accent-soft: #1e3a52;
  --radius: 10px;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f7f9;
    --surface: #ffffff;
    --surface-2: #f0f2f5;
    --border: #d9dee6;
    --text: #1a1d23;
    --muted: #626b7a;
    --accent: #1a6fc4;
    --accent-soft: #e3eefb;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 0 20px 60px;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1180px; margin: 0 auto; }
header { padding: 28px 0 18px; }
h1 { margin: 0 0 4px; font-size: 26px; letter-spacing: -0.01em; }
.stats { color: var(--muted); font-size: 13px; }
.controls {
  position: sticky; top: 0; z-index: 10;
  display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
  padding: 12px 0; margin-bottom: 18px;
  background: var(--bg); border-bottom: 1px solid var(--border);
}
#search {
  flex: 1 1 260px; min-width: 200px;
  padding: 9px 12px; font-size: 14px;
  background: var(--surface); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--radius);
}
#search:focus, select:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
select {
  padding: 9px 10px; font-size: 14px;
  background: var(--surface); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--radius); cursor: pointer;
}
.count { color: var(--muted); font-size: 13px; margin-left: auto; white-space: nowrap; }
.group-head {
  margin: 30px 0 14px; padding-bottom: 8px;
  font-size: 17px; font-weight: 600;
  border-bottom: 1px solid var(--border);
}
.group-head .n { color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 8px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; }
.card {
  display: flex; gap: 14px; padding: 14px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius);
}
.card:hover { border-color: var(--accent); }
.thumb { flex: 0 0 88px; }
.thumb img {
  width: 88px; height: 132px; object-fit: cover;
  border-radius: 6px; background: var(--surface-2); display: block;
}
.thumb .placeholder {
  width: 88px; height: 132px; border-radius: 6px;
  background: var(--surface-2); color: var(--muted);
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; text-align: center; padding: 6px;
}
.body { min-width: 0; flex: 1; }
.title { font-weight: 600; margin-bottom: 2px; line-height: 1.35; }
.title a { color: var(--text); text-decoration: none; }
.title a:hover { color: var(--accent); text-decoration: underline; }
.author { color: var(--muted); font-size: 13px; margin-bottom: 6px; }
.badge {
  display: inline-block; padding: 1px 8px; margin: 0 5px 5px 0;
  font-size: 11px; border-radius: 20px;
  background: var(--accent-soft); color: var(--accent);
}
.tag {
  display: inline-block; padding: 1px 8px; margin: 4px 5px 0 0;
  font-size: 11px; border-radius: 20px;
  background: var(--surface-2); color: var(--muted);
  border: 1px solid var(--border); cursor: pointer;
}
.tag:hover { color: var(--accent); border-color: var(--accent); }
.desc {
  font-size: 13px; color: var(--muted); margin-top: 6px;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical;
  overflow: hidden;
}
.card.open .desc { -webkit-line-clamp: unset; }
.meta { font-size: 12px; color: var(--muted); margin-top: 8px; }
.meta a { color: var(--accent); text-decoration: none; }
.meta a:hover { text-decoration: underline; }
.empty { padding: 60px 0; text-align: center; color: var(--muted); }
@media (max-width: 560px) {
  .grid { grid-template-columns: 1fr; }
  .count { margin-left: 0; width: 100%; }
}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>__TITLE__</h1>
  <div class="stats" id="stats"></div>
</header>

<div class="controls">
  <input type="search" id="search" placeholder="Search title, author, series, subject…" autocomplete="off">
  <select id="group">
    <option value="author">Group by author</option>
    <option value="title">Sort by title</option>
    <option value="series">Group by series</option>
    <option value="category">Group by category</option>
    <option value="added">Most recently added</option>
  </select>
  <select id="filter"></select>
  <span class="count" id="count"></span>
</div>

<div id="results"></div>
</div>

<script id="catalog" type="application/json">__DATA__</script>
<script>
const BOOKS = JSON.parse(document.getElementById('catalog').textContent);

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

// ---- filter dropdown ------------------------------------------------------
const UNCATEGORISED = '\x01uncategorised';
const categories = [...new Set(BOOKS.flatMap(b => b.categories || []))].sort();
const filterSel = document.getElementById('filter');
filterSel.append(new Option('All categories', ''));
categories.forEach(c => filterSel.append(new Option(c, c)));
filterSel.append(new Option('— Uncategorised —', UNCATEGORISED));

// ---- stats ----------------------------------------------------------------
const authors = new Set(BOOKS.map(b => b.author).filter(Boolean));
const seriesNames = new Set(BOOKS.map(b => b.series).filter(Boolean));
const categorised = BOOKS.filter(b => (b.categories || []).length).length;
document.getElementById('stats').textContent =
  BOOKS.length + ' books · ' + authors.size + ' authors · ' + seriesNames.size +
  ' series · ' + categorised + ' categorised';

// ---- card -----------------------------------------------------------------
function card(book) {
  const c = el('div', 'card');

  const thumb = el('div', 'thumb');
  if (book.cover) {
    const img = el('img');
    img.src = book.cover;
    img.alt = 'Cover of ' + book.title;
    img.loading = 'lazy';
    img.onerror = () => { thumb.replaceChildren(el('div', 'placeholder', 'No cover')); };
    thumb.append(img);
  } else {
    thumb.append(el('div', 'placeholder', 'No cover'));
  }
  c.append(thumb);

  const body = el('div', 'body');

  const title = el('div', 'title');
  if (book.file) {
    const a = el('a', null, book.title);
    a.href = book.file;
    a.title = 'Open ' + book.title;
    title.append(a);
  } else {
    title.textContent = book.title;
  }
  body.append(title);
  body.append(el('div', 'author', book.author || 'Unknown author'));

  if (book.series) {
    const label = book.series + (book.series_index ? ' #' + book.series_index : '');
    body.append(el('span', 'badge', label));
  }

  (book.categories || []).forEach(cat => {
    const t = el('span', 'tag', cat);
    t.onclick = () => { filterSel.value = cat; render(); };
    body.append(t);
  });

  if (book.description) {
    const d = el('div', 'desc', book.description);
    d.onclick = () => c.classList.toggle('open');
    d.title = 'Click to expand';
    body.append(d);
  }

  const bits = [];
  if (book.published_date) bits.push(book.published_date.slice(0, 4));
  if (book.page_count) bits.push(book.page_count + ' pages');
  if (book.type) bits.push(book.type.toUpperCase());
  if (book.size) bits.push((book.size / 1048576).toFixed(1) + ' MB');
  if (bits.length) {
    const meta = el('div', 'meta', bits.join(' · '));
    if (book.file) {
      meta.append(document.createTextNode(' · '));
      const open = el('a', null, 'Open file');
      open.href = book.file;
      meta.append(open);
    }
    body.append(meta);
  }

  c.append(body);
  return c;
}

// ---- grouping -------------------------------------------------------------
const collator = new Intl.Collator(undefined, { sensitivity: 'base', numeric: true });

function groupKeys(book, mode) {
  if (mode === 'author') return [book.author || 'Unknown author'];
  if (mode === 'series') return [book.series || 'Standalone'];
  if (mode === 'category') {
    // A book sits under each of its categories, so sub-genres get their own
    // section rather than everything collapsing into Fiction / Non-Fiction.
    const cats = book.categories || [];
    return cats.length ? cats : ['Uncategorised'];
  }
  return [''];
}

function sortWithin(books, mode) {
  if (mode === 'series') {
    return books.sort((a, b) =>
      (a.series_index || 9999) - (b.series_index || 9999) ||
      collator.compare(a.title, b.title));
  }
  if (mode === 'added') {
    return books.sort((a, b) => (b.modified || '').localeCompare(a.modified || ''));
  }
  return books.sort((a, b) => collator.compare(a.title, b.title));
}

function render() {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const mode = document.getElementById('group').value;
  const filter = filterSel.value;

  let books = BOOKS;

  if (filter === UNCATEGORISED) {
    books = books.filter(b => !(b.categories || []).length);
  } else if (filter) {
    books = books.filter(b => (b.categories || []).includes(filter));
  }

  if (q) {
    books = books.filter(b => b.haystack.includes(q));
  }

  const results = document.getElementById('results');
  results.replaceChildren();

  document.getElementById('count').textContent =
    books.length === BOOKS.length ? '' : books.length + ' of ' + BOOKS.length + ' shown';

  if (!books.length) {
    results.append(el('div', 'empty', 'No books match that search.'));
    return;
  }

  if (mode === 'title' || mode === 'added') {
    const grid = el('div', 'grid');
    sortWithin([...books], mode).forEach(b => grid.append(card(b)));
    results.append(grid);
    return;
  }

  const groups = new Map();
  books.forEach(b => {
    groupKeys(b, mode).forEach(key => {
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(b);
    });
  });

  [...groups.keys()].sort(collator.compare).forEach(key => {
    const head = el('h2', 'group-head', key);
    head.append(el('span', 'n', groups.get(key).length + ' ' +
      (groups.get(key).length === 1 ? 'book' : 'books')));
    results.append(head);
    const grid = el('div', 'grid');
    sortWithin([...groups.get(key)], mode).forEach(b => grid.append(card(b)));
    results.append(grid);
  });
}

document.getElementById('search').addEventListener('input', render);
document.getElementById('group').addEventListener('change', render);
filterSel.addEventListener('change', render);
render();
</script>
</body>
</html>
"""


def _book_payload(book: Dict) -> Dict:
    """Reduce a catalogue entry to the fields the page actually renders."""
    title = book.get('title') or Path(book.get('path', '')).stem or 'Untitled'
    payload = {
        'title': title,
        'author': book.get('author', ''),
        'categories': book.get('categories') or [],
        'description': book.get('description', ''),
        'series': book.get('series', ''),
        'type': book.get('type', ''),
        'size': book.get('size', 0),
        'modified': book.get('modified', ''),
        'published_date': str(book.get('published_date', '')),
        'page_count': book.get('page_count', ''),
    }

    index = book.get('series_index')
    if index not in (None, ''):
        try:
            value = float(index)
            payload['series_index'] = int(value) if value == int(value) else value
        except (TypeError, ValueError):
            pass

    cover = book.get('cover_url', '')
    if cover:
        # Covers live beside the HTML file, so relative paths are used as-is.
        payload['cover'] = cover if cover.startswith('http') else quote(cover)

    path = book.get('path', '')
    if path:
        # The HTML sits in Library/, the books one level up.
        payload['file'] = '../' + quote(path)

    # Precomputed lowercase search index — one string beats five comparisons per keystroke.
    payload['haystack'] = ' '.join(filter(None, [
        payload['title'], payload['author'], payload['series'],
        ' '.join(payload['categories']), payload['description'],
    ])).lower()

    return payload


def generate_html(catalog: Catalog, config: Config, title: str = "Ebook Library") -> Path:
    """Write books.html and return its path."""
    books: List[Dict] = [_book_payload(b) for b in catalog.values()]
    books.sort(key=lambda b: (b['author'].lower(), b['title'].lower()))

    data = json.dumps(books, ensure_ascii=False)
    # Prevent the JSON from terminating the surrounding <script> element.
    data = data.replace('</', '<\\/')

    page = PAGE_TEMPLATE.replace('__TITLE__', title).replace('__DATA__', data)

    config.library_dir.mkdir(parents=True, exist_ok=True)
    config.html_file.write_text(page, encoding='utf-8')
    logger.info(f"{Colors.GREEN}HTML catalogue written: {config.html_file}{Colors.ENDC}")
    return config.html_file
