/* Geofence review UI. */

const State = {
  queue: [],
  decisions: {},
  idx: 0,
  loading: true,
  autoAdvance: false,
  saveStatus: 'idle',
  pendingSaves: 0,
  filter: { cls: 'both', type: 'all', decision: 'undecided', nameQuery: '' },
  _filteredIndices: null,  // lazy cache; null means stale
};

function entryMatchesFilter(e) {
  const f = State.filter;
  const lin = e.lineage || {};
  const klass = (lin.class || '').toLowerCase();
  if (f.cls !== 'both' && klass !== f.cls) return false;
  if (f.type !== 'all' && e.kind !== f.type) return false;
  const decision = State.decisions[e.id];
  if (f.decision === 'undecided' && decision) return false;
  if (['accept','reject','custom'].includes(f.decision)
      && (!decision || decision.outcome !== f.decision)) return false;
  if (f.nameQuery) {
    const cn = (e.commonName || '').toLowerCase();
    if (!cn.includes(f.nameQuery)) return false;
  }
  return true;
}

function getFilteredIndices() {
  if (State._filteredIndices !== null) return State._filteredIndices;
  const out = [];
  for (let i = 0; i < State.queue.length; i++) {
    if (entryMatchesFilter(State.queue[i])) out.push(i);
  }
  State._filteredIndices = out;
  return out;
}

function invalidateFilter() { State._filteredIndices = null; }

const OUTCOMES = ['accept', 'reject', 'custom'];

// ---------------------------------------------------------------------------
// Country / state name helpers

function nameForCountry(entry, code) {
  return (entry.countryNames && entry.countryNames[code]) || code;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ---------------------------------------------------------------------------
// Bootstrap

async function bootstrap() {
  setSaveStatus('loading');
  const [qRes, dRes] = await Promise.all([
    fetch('/api/queue').then(r => r.json()),
    fetch('/api/decisions').then(r => r.json()),
  ]);
  State.queue     = qRes.entries;
  State.decisions = dRes.decisions || {};
  State.loading   = false;
  document.getElementById('progress-text').textContent = `${State.queue.length} items loaded`;
  if (!State.queue.length) {
    document.getElementById('card-empty').classList.remove('hidden');
    return;
  }
  State.idx = firstUndecidedIdx();
  setSaveStatus('idle');
  attachListeners();
  render();
}

function firstUndecidedIdx() {
  const idxs = getFilteredIndices();
  for (const i of idxs) {
    if (!State.decisions[State.queue[i].id]) return i;
  }
  return idxs.length ? idxs[0] : 0;
}

// ---------------------------------------------------------------------------
// Rendering

function render() {
  const e = currentEntry();
  if (!e) return;
  document.getElementById('card').classList.remove('hidden');

  const decidedCount = Object.keys(State.decisions).length;
  const idxs = getFilteredIndices();
  const pos  = idxs.indexOf(State.idx);    // -1 if current is filtered out
  if (pos >= 0) {
    document.getElementById('progress-text').textContent =
      `Item ${pos + 1} of ${idxs.length} filtered  |  ${decidedCount} decided`;
    document.getElementById('progress-bar').style.width =
      `${((pos + 1) / idxs.length) * 100}%`;
  } else {
    document.getElementById('progress-text').textContent =
      `(current item is filtered out)  |  ${decidedCount} decided`;
    document.getElementById('progress-bar').style.width = '0%';
  }
  document.getElementById('filter-info').textContent =
    `${idxs.length} of ${State.queue.length} items visible`;
  document.getElementById('jump-input').value = State.idx + 1;

  document.getElementById('common-name').textContent  = e.commonName || '(no common name)';
  document.getElementById('binomial').textContent     = e.binomial ? `(${e.binomial})` : '';
  document.getElementById('class-label').textContent  = e.classLabel || '';
  document.getElementById('source-label').textContent = sourceLabel(e);
  document.getElementById('region-label').textContent = e.regionDisplay || 'global';
  document.getElementById('binomial-fallback').classList.toggle('hidden', !e.usedBinomialFallback);

  renderLineage(e);
  renderGeofence(e);
  renderProposal(e);
  renderSearchLinks(e);
  renderDecision(e);
}

function sourceLabel(e) {
  switch (e.source) {
    case 'systematic':  return `Systematic #${e.rank}`;
    case 'canada':      return 'Canada (country pack)';
    case 'usa':         return 'USA (country pack)';
    case 'usa_state':   return 'USA state pack';
    default: return e.source;
  }
}

// ---------------------------------------------------------------------------
// Taxonomy + geofence

function renderLineage(e) {
  const el = document.getElementById('lineage');
  el.innerHTML = '';
  if (!e.lineage) { el.textContent = '(no taxonomy)'; return; }
  for (const [rank, name] of [['Class', e.lineage.class], ['Order', e.lineage.order],
                              ['Family', e.lineage.family], ['Genus', e.lineage.genus],
                              ['Species', e.lineage.species]]) {
    if (!name) continue;
    const span = document.createElement('div');
    span.innerHTML = `<span class="rank">${rank.padEnd(8, ' ')}</span> ${name}`;
    el.appendChild(span);
  }
}

function renderGeofence(e) {
  const el = document.getElementById('geofence-summary');
  el.innerHTML = '';
  const gf = e.geofence;
  if (!gf || !gf.hasEntry) {
    el.textContent = '(no entry in geofence)';
    return;
  }
  const lines = [];
  if (gf.allow) lines.push(geofenceListHtml(e, 'Allow', gf.allow));
  if (gf.block) lines.push(geofenceListHtml(e, 'Block', gf.block));
  if (!lines.length) lines.push('<div>(empty entry)</div>');
  el.innerHTML = lines.join('');
}

function geofenceListHtml(e, label, dict) {
  const codes = Object.keys(dict).sort((a, b) =>
    nameForCountry(e, a).localeCompare(nameForCountry(e, b)));
  const summary = `<div><span class="label">${label}:</span> ${codes.length} countries</div>`;
  const items = codes.map(cc => {
    const name = nameForCountry(e, cc);
    const sub = dict[cc];
    const stateSuffix = (Array.isArray(sub) && sub.length) ? ` [${sub.join(', ')}]` : '';
    return `<span class="country-line"><span class="cc">${cc}</span> ${escapeHtml(name)}${stateSuffix}</span>`;
  }).join(', ');
  return summary + `<details><summary>Show</summary><div class="country-list">${items}</div></details>`;
}

// ---------------------------------------------------------------------------
// Original proposal

function renderProposal(e) {
  // The descriptive paragraph
  const el = document.getElementById('proposal-text');
  el.innerHTML = '';
  if (e.kind === 'add') {
    el.appendChild(makeTag('ADD', 'tag-add'));
    el.appendChild(document.createTextNode(
      ` ${e.commonName} (${e.binomial}) is not currently in the ${e.regionDisplay} pack; the suggestion is to add it.`));
  } else if (e.kind === 'remove') {
    el.appendChild(makeTag('REMOVE', 'tag-rem'));
    el.appendChild(document.createTextNode(
      ` ${e.commonName} (${e.binomial}) is currently in the ${e.regionDisplay} pack; the suggestion is to remove it.`));
  } else if (e.kind === 'systematic') {
    el.appendChild(makeTag('SYSTEMATIC', 'tag-info'));
    const p = document.createElement('p');
    p.style.margin = '6px 0 0 0';
    p.textContent = e.proposalSummary || '(no proposal text)';
    el.appendChild(p);
    const counts = document.createElement('p');
    counts.style.margin = '6px 0 0 0';
    counts.style.fontSize = '12px';
    counts.style.color = '#555';
    counts.textContent =
      `Currently allowed in ${e.keepCountryCount + e.removeCountryCount} countries: ` +
      `${e.keepCountryCount} proposed kept, ${e.removeCountryCount} proposed removed.`;
    el.appendChild(counts);
  }
  if (e.footprintLabel) {
    const fp = document.createElement('div');
    fp.style.fontSize = '12px'; fp.style.color = '#666'; fp.style.marginTop = '6px';
    fp.textContent = `Footprint: ${e.footprintLabel}`;
    el.appendChild(fp);
  }

  // The proposed rules: no colors, no interactivity, only changes.
  const rulesEl = document.getElementById('proposal-rules');
  rulesEl.innerHTML = '';
  const pr = e.proposedRules || {allowRules: [], blockRules: []};
  if (pr.allowRules.length) {
    rulesEl.appendChild(renderRuleList(e, 'Proposed allow rule', pr.allowRules));
  }
  if (pr.blockRules.length) {
    rulesEl.appendChild(renderRuleList(e, 'Proposed block rule', pr.blockRules));
  }
}

function makeTag(label, cls) {
  const t = document.createElement('span');
  t.className = 'tag ' + cls;
  t.textContent = label;
  return t;
}

const US_STATE_NAMES = {
  AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California',
  CO: 'Colorado', CT: 'Connecticut', DC: 'District of Columbia', DE: 'Delaware',
  FL: 'Florida', GA: 'Georgia', HI: 'Hawaii', IA: 'Iowa', ID: 'Idaho',
  IL: 'Illinois', IN: 'Indiana', KS: 'Kansas', KY: 'Kentucky', LA: 'Louisiana',
  MA: 'Massachusetts', MD: 'Maryland', ME: 'Maine', MI: 'Michigan', MN: 'Minnesota',
  MO: 'Missouri', MS: 'Mississippi', MT: 'Montana', NC: 'North Carolina',
  ND: 'North Dakota', NE: 'Nebraska', NH: 'New Hampshire', NJ: 'New Jersey',
  NM: 'New Mexico', NV: 'Nevada', NY: 'New York', OH: 'Ohio', OK: 'Oklahoma',
  OR: 'Oregon', PA: 'Pennsylvania', RI: 'Rhode Island', SC: 'South Carolina',
  SD: 'South Dakota', TN: 'Tennessee', TX: 'Texas', UT: 'Utah', VA: 'Virginia',
  VT: 'Vermont', WA: 'Washington', WI: 'Wisconsin', WV: 'West Virginia', WY: 'Wyoming',
};

function ruleRegionLabel(entry, rule) {
  if (rule.state) {
    const name = US_STATE_NAMES[rule.state] || rule.state;
    return `${name} (USA-${rule.state})`;
  }
  if (rule.country) return `${nameForCountry(entry, rule.country)} (${rule.country})`;
  return '';
}

function ruleTaxonLabel(entry, rule) {
  const level = rule.taxonLevel || 'species';
  if (level === 'genus')  return `${rule.genus  || 'unknown'} (genus)`;
  if (level === 'family') return `${rule.family || 'unknown'} (family)`;
  if (level === 'order')  return `${rule.order  || 'unknown'} (order)`;
  return entry.commonName || rule.binomial || '';
}

function describeRule(entry, rule, opts) {
  if (rule.description) return rule.description;
  const omitTaxon = opts && opts.omitTaxon;
  const region = ruleRegionLabel(entry, rule);
  if (omitTaxon) return region || ruleTaxonLabel(entry, rule);
  const label = ruleTaxonLabel(entry, rule);
  return region ? `${label} in ${region}` : label;
}

function renderRuleList(entry, label, rules) {
  const wrap = document.createElement('div');
  wrap.className = 'rule-list';
  const head = document.createElement('div');
  head.className = 'rule-list-head';
  head.textContent = `${label}${rules.length === 1 ? '' : 's'} (${rules.length})`;
  wrap.appendChild(head);

  // Group by taxon label so genus/family rules don't get mixed in with
  // species rules in a single comma-separated blob.  Group order matches
  // the rules array.
  const groupOrder = [];
  const groups = new Map();
  for (const r of rules) {
    const k = ruleTaxonLabel(entry, r);
    if (!groups.has(k)) { groups.set(k, []); groupOrder.push(k); }
    groups.get(k).push(r);
  }

  for (const taxon of groupOrder) {
    const groupRules = groups.get(taxon);
    const liftTaxon  = (groupRules.length > 1) || groups.size > 1;
    if (liftTaxon) {
      const sub = document.createElement('div');
      sub.className = 'rule-list-species';
      sub.textContent = `For ${taxon}:`;
      wrap.appendChild(sub);
    }
    if (groupRules.length > 8) {
      const body = document.createElement('div');
      body.className = 'rule-list-body compact';
      body.textContent = groupRules.map(r => describeRule(entry, r, {omitTaxon: liftTaxon})).join(', ');
      wrap.appendChild(body);
    } else {
      const ul = document.createElement('ul');
      ul.className = 'rule-list-body';
      for (const r of groupRules) {
        const li = document.createElement('li');
        li.textContent = describeRule(entry, r, {omitTaxon: liftTaxon});
        ul.appendChild(li);
      }
      wrap.appendChild(ul);
    }
  }
  return wrap;
}

// ---------------------------------------------------------------------------
// Search links

function renderSearchLinks(e) {
  const el = document.getElementById('search-links');
  el.innerHTML = '';
  for (const link of e.searchLinks || []) {
    const a = document.createElement('a');
    a.href = link.url; a.target = '_blank'; a.rel = 'noopener';
    a.textContent = link.label;
    el.appendChild(a);
  }
}

// ---------------------------------------------------------------------------
// Decision

function renderDecision(e) {
  const decision = currentDecision();
  const outcome = decision ? decision.outcome : null;

  // Buttons
  const btns = document.getElementById('decision-buttons');
  btns.innerHTML = '';
  for (let i = 0; i < OUTCOMES.length; i++) {
    const o = OUTCOMES[i];
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'decision-btn ' + ('btn-' + o);
    if (outcome === o) b.classList.add('active');
    b.innerHTML = `${o.charAt(0).toUpperCase() + o.slice(1)} <span class="key">${i + 1}</span>`;
    b.addEventListener('click', () => choose(o));
    btns.appendChild(b);
  }

  // Custom display (only when outcome=custom AND custom content exists)
  const custom = document.getElementById('custom-display');
  custom.innerHTML = '';
  if (outcome === 'custom') {
    if (decision.custom && (decision.custom.allowRules?.length || decision.custom.blockRules?.length)) {
      const wrap = document.createElement('div');
      wrap.className = 'custom-rules';
      const head = document.createElement('div');
      head.className = 'custom-rules-head';
      head.textContent = decision.custom.description
        ? `Custom rules: ${decision.custom.description}`
        : 'Custom rules:';
      wrap.appendChild(head);
      if (decision.custom.allowRules?.length) {
        wrap.appendChild(renderRuleList(e, 'New allow rule', decision.custom.allowRules));
      }
      if (decision.custom.blockRules?.length) {
        wrap.appendChild(renderRuleList(e, 'New block rule', decision.custom.blockRules));
      }
      custom.appendChild(wrap);
    } else {
      const msg = document.createElement('div');
      msg.className = 'custom-rules empty';
      msg.textContent = 'Custom selected but no custom rules defined yet. Talk to Claude to set them.';
      custom.appendChild(msg);
    }
  }

  // Notes (editable)
  document.getElementById('notes').value = (decision && decision.notes) || '';

  // Meta footer
  const meta = document.getElementById('decision-meta');
  meta.innerHTML = '';
  if (!decision) {
    meta.textContent = 'No decision yet.';
  } else {
    const bits = [];
    if (decision.bulkRuleName) bits.push(`rule "${decision.bulkRuleName}"`);
    if (decision.updatedAt)    bits.push(`updated ${decision.updatedAt}`);
    if (decision.custom && outcome !== 'custom') {
      bits.push('custom data retained (inactive)');
    }
    meta.textContent = bits.join('  •  ');
  }
}

// ---------------------------------------------------------------------------
// State helpers

function currentEntry()    { return State.queue[State.idx] || null; }
function currentDecision() {
  const e = currentEntry();
  if (!e) return null;
  return State.decisions[e.id] || null;
}

// ---------------------------------------------------------------------------
// Decision actions

function choose(outcome) {
  const e = currentEntry();
  if (!e) return;
  const prior = State.decisions[e.id] || {};
  // Preserve custom content across outcome switches.
  const decision = { ...prior, outcome };
  saveDecision(e.id, decision);
  render();
  if (State.autoAdvance) goNext();
}

function clearDecision() {
  const e = currentEntry();
  if (!e) return;
  saveDecision(e.id, null);
  render();
}

function saveDecision(id, decision) {
  if (decision === null) {
    delete State.decisions[id];
  } else {
    State.decisions[id] = { ...(State.decisions[id] || {}), ...decision,
                            updatedAt: new Date().toISOString() };
  }
  invalidateFilter();
  State.pendingSaves++;
  setSaveStatus('pending');
  fetch('/api/decision', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ id, decision }),
  }).then(r => {
    State.pendingSaves--;
    if (!r.ok) throw new Error('save failed');
    if (State.pendingSaves === 0) setSaveStatus('ok');
  }).catch(err => {
    console.error(err);
    State.pendingSaves--;
    setSaveStatus('err');
  });
}

function setSaveStatus(s) {
  State.saveStatus = s;
  const el = document.getElementById('save-status');
  el.classList.remove('ok', 'pending', 'err');
  if (s === 'ok')           { el.textContent = 'Saved';        el.classList.add('ok'); }
  else if (s === 'pending') { el.textContent = 'Saving...';    el.classList.add('pending'); }
  else if (s === 'err')     { el.textContent = 'Save error!';  el.classList.add('err'); }
  else if (s === 'idle')    { el.textContent = 'Idle'; }
  else                      { el.textContent = s; }
}

// ---------------------------------------------------------------------------
// Navigation

function goNext() {
  const idxs = getFilteredIndices();
  for (const i of idxs) {
    if (i > State.idx) { State.idx = i; render(); return; }
  }
  if (idxs.length) { State.idx = idxs[idxs.length - 1]; render(); }
}

function goPrev() {
  const idxs = getFilteredIndices();
  for (let j = idxs.length - 1; j >= 0; j--) {
    if (idxs[j] < State.idx) { State.idx = idxs[j]; render(); return; }
  }
  if (idxs.length) { State.idx = idxs[0]; render(); }
}

function goTo(idx) {
  if (idx < 0 || idx >= State.queue.length) return;
  State.idx = idx;
  render();
}

// ---------------------------------------------------------------------------
// Keyboard

function onKey(ev) {
  const tag = (document.activeElement && document.activeElement.tagName) || '';
  if (tag === 'INPUT' || tag === 'TEXTAREA') {
    if (ev.key === 'Escape') document.activeElement.blur();
    return;
  }
  switch (ev.key) {
    case 'j': case 'ArrowRight': case ' ':
      ev.preventDefault(); goNext(); break;
    case 'k': case 'ArrowLeft':
      ev.preventDefault(); goPrev(); break;
    case '1': ev.preventDefault(); choose('accept'); break;
    case '2': ev.preventDefault(); choose('reject'); break;
    case '3': ev.preventDefault(); choose('custom'); break;
    case 'Backspace': case 'u':
      ev.preventDefault(); clearDecision(); break;
    case 'g':
      ev.preventDefault(); document.getElementById('jump-input').focus(); break;
    case 'n':
      ev.preventDefault(); document.getElementById('notes').focus(); break;
  }
}

function attachListeners() {
  document.addEventListener('keydown', onKey);
  document.getElementById('btn-next').addEventListener('click', goNext);
  document.getElementById('btn-prev').addEventListener('click', goPrev);
  document.getElementById('btn-jump').addEventListener('click', () => {
    const v = parseInt(document.getElementById('jump-input').value, 10);
    if (!isNaN(v)) goTo(v - 1);
  });
  document.getElementById('jump-input').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      const v = parseInt(ev.target.value, 10);
      if (!isNaN(v)) { goTo(v - 1); ev.target.blur(); }
    }
  });
  document.getElementById('auto-advance').addEventListener('change', (ev) => {
    State.autoAdvance = ev.target.checked;
  });

  // Filter controls: ensure defaults match State, then react to changes.
  const fc = document.getElementById('filter-class');
  const ft = document.getElementById('filter-type');
  const fd = document.getElementById('filter-decision');
  const fn = document.getElementById('filter-name');
  fc.value = State.filter.cls;
  ft.value = State.filter.type;
  fd.value = State.filter.decision;
  fn.value = State.filter.nameQuery;
  function onFilterChange() {
    State.filter.cls       = fc.value;
    State.filter.type      = ft.value;
    State.filter.decision  = fd.value;
    State.filter.nameQuery = fn.value.trim().toLowerCase();
    invalidateFilter();
    const idxs = getFilteredIndices();
    if (idxs.length && idxs.indexOf(State.idx) < 0) {
      State.idx = idxs[0];     // jump to first matching if current is filtered out
    }
    render();
  }
  fc.addEventListener('change', onFilterChange);
  ft.addEventListener('change', onFilterChange);
  fd.addEventListener('change', onFilterChange);
  fn.addEventListener('input',  onFilterChange);
  document.getElementById('notes').addEventListener('blur', (ev) => {
    const e = currentEntry();
    if (!e) return;
    const decision = State.decisions[e.id] || {};
    const newNotes = ev.target.value;
    if ((decision.notes || '') === newNotes) return;
    const next = { ...decision, notes: newNotes };
    if (!next.outcome) next.outcome = 'reject';   // notes without decision -> reject
    saveDecision(e.id, next);
    render();
  });
}

bootstrap();
