const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('matchup dialog preserves focus/scroll and pauses only background polling', () => {
  const listeners = {}, classes = new Set();
  let focused = false, restored = null;
  const dialog = {
    open: false,
    showModal() { this.open = true; },
    close() { this.open = false; listeners.close({target: this}); },
    matches: selector => selector === '.mw-detail-dialog',
  };
  const opener = {dataset: {matchupOpen: 'other-matchup-2'}, focus(options) { focused = options.preventScroll; }};
  const document = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    getElementById: id => id === 'other-matchup-2' ? dialog : null,
    querySelector: () => dialog.open ? dialog : null,
    documentElement: {classList: {add: x => classes.add(x), remove: x => classes.delete(x)}},
  };
  const window = {scrollY: 475, scrollTo: (x, y) => { restored = y; }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/v3/matchup_dialog.js'), 'utf8'), {document, window});
  listeners.click({target: {closest: selector => selector === '[data-matchup-open]' ? opener : null}});
  assert.equal(dialog.open, true);
  assert.ok(classes.has('mw-dialog-open'));
  const request = id => {
    const event = {detail: {elt: {id}}, prevented: false, preventDefault() { this.prevented = true; }};
    listeners['htmx:beforeRequest'](event);
    return event.prevented;
  };
  assert.equal(request('matchweek-view'), true);
  assert.equal(request('another-request'), false);
  const swap = {detail: {target: {id: 'matchweek-view'}}, prevented: false, preventDefault() { this.prevented = true; }};
  listeners['htmx:beforeSwap'](swap);
  assert.equal(swap.prevented, true); // Also guard a request already in flight when opened.
  listeners.click({target: {closest: selector => selector === '[data-matchup-close]' ? {} : selector === 'dialog' ? dialog : null}});
  assert.equal(dialog.open, false);
  assert.equal(focused, true);
  assert.equal(restored, 475);
  assert.equal(classes.size, 0);
  assert.equal(request('matchweek-view'), false);
});
