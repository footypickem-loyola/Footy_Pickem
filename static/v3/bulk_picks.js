/* Bulk choices stay in this dialog until the final, authoritative POST. */
function createBulkDraft(fixtures, preferences = []) {
  const byId = new Map(fixtures.map(fixture => [fixture.id, fixture]));
  const initial = [];
  const seen = new Set();
  preferences.forEach(preference => {
    const fixture = byId.get(preference.fixture_id);
    if (fixture && !fixture.picked && !seen.has(fixture.id) && [fixture.home, fixture.away].includes(preference.team)) {
      initial.push({fixture_id: fixture.id, team: preference.team});
      seen.add(fixture.id);
    }
  });
  return {
    fixtures, selected: null, filter: 'available',
    slots: Array.from({length: fixtures.filter(fixture => !fixture.picked).length}, (_, index) => initial[index] || null),
    position(id) { return this.slots.findIndex(slot => slot && slot.fixture_id === id); },
    visibleFixtures() {
      return this.fixtures.filter(fixture => this.filter === 'all' || (!fixture.picked && this.position(fixture.id) < 0));
    },
    select(id) { if (byId.has(id) && !byId.get(id).picked) this.selected = id; },
    place(index) {
      if (this.selected === null || index < 0 || index >= this.slots.length) return;
      const old = this.slots.findIndex(slot => slot && slot.fixture_id === this.selected);
      const moving = old >= 0 ? this.slots[old] : {fixture_id: this.selected, team: null};
      if (old >= 0 && old !== index) this.slots[old] = this.slots[index];
      this.slots[index] = moving;
      this.selected = null;
    },
    choose(index, team) {
      const slot = this.slots[index];
      const fixture = slot && byId.get(slot.fixture_id);
      if (fixture && [fixture.home, fixture.away].includes(team)) slot.team = team;
    },
    clear(index) { if (index >= 0 && index < this.slots.length) this.slots[index] = null; },
    move(index, delta) {
      const target = index + delta;
      if (index >= 0 && index < this.slots.length && target >= 0 && target < this.slots.length) {
        [this.slots[index], this.slots[target]] = [this.slots[target], this.slots[index]];
      }
    },
    complete() { return this.slots.length > 0 && this.slots.every(slot => slot && slot.team); },
    payload() {
      if (!this.complete()) throw new Error('Complete every priority before confirming.');
      return this.slots.map(slot => ({...slot}));
    },
    fixture(id) { return byId.get(id); },
  };
}

if (typeof module !== 'undefined' && module.exports) module.exports = {createBulkDraft};

if (typeof document !== 'undefined') (() => {
  const states = new WeakMap();
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const button = (text, attribute, value, label) => {
    const node = element('button', text);
    node.type = 'button';
    node.setAttribute(attribute, value);
    if (label) node.setAttribute('aria-label', label);
    return node;
  };
  const focus = (dialog, selector) => dialog.querySelector(selector)?.focus();

  function render(dialog) {
    const state = states.get(dialog);
    const fixtures = dialog.querySelector('[data-bulk-fixtures]');
    fixtures.replaceChildren();
    dialog.querySelectorAll('[data-bulk-filter]').forEach(button => {
      const active = button.dataset.bulkFilter === state.filter;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    state.visibleFixtures().forEach(fixture => {
      const position = state.position(fixture.id);
      const card = button('', 'data-bulk-fixture', fixture.id,
        `Choose fixture ${fixture.home} vs ${fixture.away}`);
      card.className = 'bulk-fixture';
      card.setAttribute('aria-pressed', String(state.selected === fixture.id));
      const name = element('span', `${fixture.home} (Home) vs ${fixture.away} (Away)`);
      const status = fixture.picked ? (fixture.picked_by_you ? `${fixture.picked_team} Picked` : 'Opponent Picked') : (position >= 0 ? `Priority ${position + 1} · Click to move` : 'Available');
      card.append(name, element('small', `${fixture.venue} · ${status}`));
      if (position >= 0) card.classList.add('is-assigned');
      if (fixture.picked) { card.classList.add('is-picked'); card.disabled = true; }
      fixtures.append(card);
    });
    if (!fixtures.children.length) fixtures.append(element('p', 'All remaining fixtures are in your priorities. Clear a slot to make its fixture available again.'));
    const slots = dialog.querySelector('[data-bulk-slots]');
    slots.replaceChildren();
    state.slots.forEach((slot, index) => {
      const row = element('li', undefined, 'bulk-slot');
      row.append(element('span', String(index + 1), 'bulk-number'));
      const content = element('div', undefined, 'bulk-slot-content');
      const fixture = slot && state.fixture(slot.fixture_id);
      const place = button(fixture ? `${fixture.home} (Home) vs ${fixture.away} (Away)` : 'Place fixture here',
        'data-bulk-slot', index, `Place selected fixture in priority ${index + 1}`);
      place.className = 'bulk-place';
      place.disabled = state.selected === null;
      content.append(place);
      if (fixture) {
        content.append(element('small', fixture.venue, 'bulk-slot-venue'));
        const teams = element('div', undefined, 'bulk-teams');
        teams.setAttribute('role', 'group');
        teams.setAttribute('aria-label', `Team for priority ${index + 1}`);
        [fixture.home, fixture.away].forEach((team, side) => {
          const choice = button(team, 'data-bulk-team', side, `Priority ${index + 1}: select ${team}`);
          choice.dataset.index = index;
          choice.setAttribute('aria-pressed', String(slot.team === team));
          teams.append(choice);
        });
        content.append(teams);
        const actions = element('div', undefined, 'bulk-slot-actions');
        const up = button('↑', 'data-bulk-up', index, `Move priority ${index + 1} up`);
        const down = button('↓', 'data-bulk-down', index, `Move priority ${index + 1} down`);
        up.disabled = index === 0;
        down.disabled = index === state.slots.length - 1;
        actions.append(up, down, button('Clear', 'data-bulk-clear', index, `Clear priority ${index + 1}`));
        content.append(actions);
      }
      row.append(content);
      slots.append(row);
    });
    const complete = state.slots.filter(slot => slot && slot.team).length;
    dialog.querySelector('[data-bulk-progress]').textContent = `${complete}/${state.slots.length} ready`;
    dialog.querySelector('[data-bulk-review-button]').disabled = !state.complete();
    const selected = state.fixture(state.selected);
    dialog.querySelector('[data-bulk-instruction]').textContent = selected
      ? `${selected.home} vs ${selected.away} selected. Choose its priority slot.`
      : state.complete() ? 'Your list is ready. Submit it to review before confirming.'
      : 'Choose a fixture, place it in a slot, then choose a team.';
  }

  function review(dialog, show) {
    const state = states.get(dialog);
    if (show && !state.complete()) return;
    dialog.querySelector('[data-bulk-editor]').hidden = show;
    dialog.querySelector('[data-bulk-review]').hidden = !show;
    dialog.querySelector('[data-bulk-review-button]').hidden = show;
    dialog.querySelector('[data-bulk-form]').hidden = !show;
    dialog.querySelector('[data-bulk-back]').hidden = !show;
    dialog.querySelector('[data-bulk-error]').hidden = true;
    if (show) {
      const list = dialog.querySelector('[data-bulk-review-list]');
      list.replaceChildren();
      state.slots.forEach((slot, index) => {
        const fixture = state.fixture(slot.fixture_id);
        const item = element('li');
        const copy = element('div');
        copy.append(element('strong', slot.team), element('small', `${fixture.home} (Home) vs ${fixture.away} (Away) · ${fixture.venue}`));
        item.append(element('span', String(index + 1), 'bulk-number'), copy);
        list.append(item);
      });
      dialog.querySelector('[name=preferences]').value = JSON.stringify(state.payload());
      focus(dialog, '[data-bulk-review-title]');
    } else focus(dialog, '[data-bulk-fixture]');
    dialog.querySelector('.bulk-content').scrollTop = 0;
  }

  document.addEventListener('click', event => {
    const target = event.target.closest('button');
    if (!target) return;
    if (target.hasAttribute('data-bulk-open')) {
      const dialog = document.getElementById(target.dataset.bulkOpen);
      const config = dialog.querySelector('.bulk-config');
      if (config) {
        const data = JSON.parse(config.textContent);
        states.set(dialog, createBulkDraft(data.fixtures, data.preferences));
        render(dialog);
        review(dialog, false);
        dialog.querySelectorAll('button').forEach(btn => { if (btn.hasAttribute('data-bulk-close')) btn.disabled = false; });
      }
      dialog.showModal();
      focus(dialog, '[data-bulk-close]');
      return;
    }
    const dialog = target.closest('.bulk-dialog');
    if (!dialog || dialog.dataset.submitting === 'true') return;
    if (target.hasAttribute('data-bulk-close')) { dialog.close(); return; }
    const state = states.get(dialog);
    if (!state) return;
    if (target.hasAttribute('data-bulk-filter')) {
      state.filter = target.dataset.bulkFilter;
      render(dialog);
      return;
    }
    if (target.hasAttribute('data-bulk-review-button')) { review(dialog, true); return; }
    if (target.hasAttribute('data-bulk-back')) { review(dialog, false); return; }
    let selector;
    if (target.hasAttribute('data-bulk-fixture')) {
      state.select(Number(target.dataset.bulkFixture));
      const empty = state.slots.findIndex(slot => !slot);
      selector = `[data-bulk-slot="${empty < 0 ? 0 : empty}"]`;
    } else if (target.hasAttribute('data-bulk-slot')) {
      const index = Number(target.dataset.bulkSlot);
      state.place(index);
      selector = `[data-index="${index}"][data-bulk-team="0"]`;
    } else if (target.hasAttribute('data-bulk-team')) {
      const index = Number(target.dataset.index);
      const fixture = state.fixture(state.slots[index].fixture_id);
      state.choose(index, Number(target.dataset.bulkTeam) === 0 ? fixture.home : fixture.away);
      selector = `[data-index="${index}"][data-bulk-team="${target.dataset.bulkTeam}"]`;
    } else if (target.hasAttribute('data-bulk-clear')) {
      state.clear(Number(target.dataset.bulkClear));
      selector = '[data-bulk-fixture]';
    } else if (target.hasAttribute('data-bulk-up') || target.hasAttribute('data-bulk-down')) {
      const up = target.hasAttribute('data-bulk-up');
      const index = Number(up ? target.dataset.bulkUp : target.dataset.bulkDown);
      state.move(index, up ? -1 : 1);
      selector = `[data-index="${index + (up ? -1 : 1)}"][data-bulk-team="0"]`;
    } else return;
    render(dialog);
    focus(dialog, selector);
  });

  document.addEventListener('submit', event => {
    const form = event.target.closest('[data-bulk-form]');
    if (!form) return;
    event.preventDefault();
    const dialog = form.closest('dialog');
    if (dialog.dataset.submitting === 'true') return;
    if (!states.get(dialog)?.complete() || form.hidden) return;
    dialog.dataset.submitting = 'true';
    dialog.querySelectorAll('button').forEach(btn => { btn.disabled = true; });
    dialog.querySelector('[data-bulk-confirm]').textContent = 'Confirming…';
    htmx.trigger(form, 'bulkConfirmed');
  });
  document.addEventListener('cancel', event => {
    if (event.target.matches('.bulk-dialog') && event.target.dataset.submitting === 'true') event.preventDefault();
  }, true);
  document.addEventListener('htmx:afterRequest', event => {
    const form = event.detail.elt;
    if (!form?.matches('[data-bulk-form]')) return;
    const dialog = form.closest('dialog');
    if (event.detail.successful) {
      if (dialog?.open) dialog.close();
      const title = document.querySelector('#matchweek-view h1');
      if (title) { title.tabIndex = -1; title.focus(); }
      return;
    }
    if (!dialog) return;
    dialog.dataset.submitting = 'false';
    dialog.querySelectorAll('button').forEach(btn => { btn.disabled = false; });
    dialog.querySelector('[data-bulk-confirm]').textContent = 'Confirm Bulk Picks';
    const error = dialog.querySelector('[data-bulk-error]');
    let message = 'Could not confirm your picks. Close this window and refresh to check the saved state before trying again.';
    try { message = JSON.parse(event.detail.xhr.responseText).error || message; } catch (_) { /* Network or non-JSON error. */ }
    error.textContent = message;
    error.hidden = false;
    error.tabIndex = -1;
    error.focus();
  });
})();
