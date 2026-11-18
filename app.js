import { snapshot } from './data.js';
import { artwork } from './art.js';
import { Pit } from './physics.js';
import { makeSticker } from './stickers.js';

const byId = id => document.getElementById(id);
const colors = ['#bba2f4', '#f4b5a4', '#f9ce90', '#a9cdae', '#9ec9f3', '#e4a9d0'];
const countFormat = new Intl.NumberFormat();
const dateFormat = new Intl.DateTimeFormat('en-US', {
  month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
});
const arena = byId('arena');
const detail = byId('detail');
const rankings = byId('rankings');
const search = byId('search');
const drop = byId('drop');
const pause = byId('pause');
const motion = matchMedia('(prefers-reduced-motion: reduce)');
const pit = new Pit(arena, { select: id => select(id, true), reducedMotion: motion.matches });

let mode = 'album';
let records = [];
const selections = new Map();
let activeId;

byId('date-range').textContent = dateFormat.formatRange(
  new Date(snapshot.startDate + 'T00:00:00Z'),
  new Date(snapshot.endDate + 'T00:00:00Z'),
);
byId('coverage').textContent = 'Matched share of recorded listening time: ' +
  Object.entries(snapshot.coverage).map(([category, fraction]) =>
    Math.round(fraction * 100) + '% ' + category
  ).join(', ') + '. About ' + Math.round(snapshot.inferredAlbumShare * 100) +
  '% of total listening time was assigned to albums through unique artist and song matches in playlists.';

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function duration(seconds) {
  const minutes = Math.round(seconds / 60);
  if (minutes >= 60) {
    const hours = (seconds / 3600).toLocaleString(undefined, { maximumFractionDigits: 1 });
    return hours + ' hr';
  }
  return minutes + ' min';
}

function insertArtwork(parent, item) {
  const image = artwork[item.id]?.url;
  parent.append(makeSticker(item.id));
  if (!image) return;
  const img = element('img');
  img.alt = '';
  img.decoding = 'async';
  img.loading = parent.classList.contains('ball') ? 'lazy' : 'eager';
  img.draggable = false;
  img.referrerPolicy = 'no-referrer';
  img.addEventListener('error', () => img.remove(), { once: true });
  img.src = image;
  parent.append(img);
}

function color(item) {
  let index = 0;
  for (const character of item.id) index += character.charCodeAt(0);
  return colors[index % colors.length];
}

function makeBall(item, index) {
  const ball = element('button', 'ball');
  ball.type = 'button';
  ball.style.setProperty('--disc', color(item));
  ball.setAttribute('aria-label', [
    '#' + (index + 1), item.title, mode !== 'artist' && item.artist, duration(item.seconds),
  ].filter(Boolean).join(', '));
  ball.setAttribute('aria-pressed', 'false');
  insertArtwork(ball, item);

  ball.addEventListener('focus', () => {
    if (ball.matches(':focus-visible')) select(item.id);
  });
  return ball;
}

function renderDetail(item, rank) {
  const imageSource = artwork[item.id];
  const row = element('div', 'detail-entry');
  const art = element('div', 'detail-image');
  art.style.setProperty('--disc', color(item));
  insertArtwork(art, item);

  const copy = element('div');
  copy.append(element('p', 'detail-kicker', '#' + rank + ' ' + mode));
  copy.append(element('h2', '', item.title));
  if (mode !== 'artist' && item.artist) copy.append(element('p', 'detail-credit', item.artist));
  if (mode === 'song' && item.album) copy.append(element('p', 'detail-credit', item.album));
  if (item.topTrack) copy.append(element('p', 'detail-credit', 'Top song · ' + item.topTrack));
  row.append(art, copy);

  const stats = element('p', 'detail-meta');
  stats.append(element('strong', '', duration(item.seconds) + ' listened'));
  stats.append(element('span', '', countFormat.format(item.events) + ' playback events'));
  if (imageSource?.source && (mode === 'song' || imageSource.sourceName !== 'Amazon Music')) {
    const source = element('a', '', imageSource.sourceName === 'Amazon Music' ? 'Album art' : (imageSource.sourceName || 'Cover source'));
    source.href = imageSource.source;
    source.target = '_blank';
    source.rel = 'noopener noreferrer';
    stats.append(source);
  }
  const identifier = /^[A-Z0-9]{10}$/.test(item.catalogId);
  if (identifier || item.title) {
    const link = element('a', '', 'Open in Amazon Music');
    const path = mode === 'album' && identifier ? 'albums/' + item.catalogId :
      mode === 'artist' && identifier ? 'artists/' + item.catalogId :
      'search/' + encodeURIComponent([item.title, mode !== 'artist' && item.artist].filter(Boolean).join(' '));
    link.href = mode === 'artist' && imageSource?.source?.startsWith('https://music.amazon.com/artists/')
      ? imageSource.source : 'https://music.amazon.com/' + path;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    stats.append(link);
  }
  detail.replaceChildren(row, stats);
}

function select(id, scrollDetails = false) {
  const index = records.findIndex(item => item.id === id);
  if (index < 0) return;
  if (activeId !== id) {
    activeId = id;
    selections.set(mode, id);
    renderDetail(records[index], index + 1);
    pit.balls.forEach(({ item, node }) => node.setAttribute('aria-pressed', String(item.id === id)));
    rankings.querySelectorAll('button').forEach(node => node.setAttribute('aria-pressed', String(node.dataset.id === id)));
  }
  if (scrollDetails && matchMedia('(max-width: 770px)').matches) {
    detail.scrollIntoView({ behavior: pit.reducedMotion ? 'instant' : 'smooth', block: 'nearest' });
  }
}

function renderRankings() {
  const query = search.value.toLocaleLowerCase().trim();
  const nodes = records.flatMap((item, index) => {
    const text = [item.title, item.artist, item.album].filter(Boolean).join(' ').toLocaleLowerCase();
    if (query && !text.includes(query)) return [];

    const row = element('li');
    const button = element('button');
    button.type = 'button';
    button.dataset.id = item.id;
    button.setAttribute('aria-pressed', String(item.id === activeId));

    const name = element('span', 'rank-name');
    name.append(element('b', '', item.title));
    if (mode !== 'artist' && item.artist) name.append(element('span', '', item.artist));
    button.append(
      element('span', 'rank-number', String(index + 1).padStart(2, '0')),
      name,
      element('span', 'rank-time', duration(item.seconds)),
    );
    button.addEventListener('click', () => select(item.id, true));
    row.append(button);
    return [row];
  });
  rankings.replaceChildren(...nodes);
  rankings.scrollTop = 0;
  byId('no-results').hidden = nodes.length > 0;
}

function switchMode(nextMode) {
  mode = nextMode;
  records = snapshot.views[mode];
  activeId = null;
  document.querySelectorAll('[data-mode]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  });
  byId('item-count').textContent = records.length;
  const nodes = records.map(makeBall);
  arena.replaceChildren(...nodes);
  pit.setBalls(records, nodes);
  search.value = '';
  renderRankings();
  select(selections.get(mode) ?? records[0]?.id);
}

function renderMotionControls() {
  pause.textContent = pit.paused ? 'Resume' : 'Pause';
  pause.setAttribute('aria-pressed', String(pit.paused));
  drop.firstChild.textContent = pit.reducedMotion ? 'Rearrange ' : 'Drop again ';
}

document.querySelectorAll('[data-mode]').forEach(button => {
  button.addEventListener('click', () => {
    if (mode !== button.dataset.mode) switchMode(button.dataset.mode);
  });
});
search.addEventListener('input', renderRankings);
drop.addEventListener('click', () => {
  pit.drop();
  renderMotionControls();
});
pause.addEventListener('click', () => {
  pit.setPaused(!pit.paused);
  renderMotionControls();
});
motion.addEventListener('change', event => {
  pit.reducedMotion = event.matches;
  if (event.matches) {
    pit.setPaused(true);
    pit.layout(false);
  }
  renderMotionControls();
});

switchMode('album');
renderMotionControls();
