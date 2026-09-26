const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Exercise the existing shell script without a browser or additional dependencies.
function setup() {
  const listeners = {};
  const events = [];
  const document = {addEventListener: (name, handler) => { listeners[name] = handler; }};
  const button = () => ({isConnected: true, focus() { document.activeElement = this; }});
  const trigger = button(), back = button(), confirm = button();
  const classes = new Set();
  const modal = {
    classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x)},
    querySelectorAll: () => [back, confirm],
  };
  const elements = {
    'pick-confirm-modal': modal,
    'confirm-team': {}, 'confirm-fixture': {},
    'arsenal-banter-images': {textContent: '[]'},
    'arsenal-banter': {classList: {remove() {}}, setAttribute() {}},
  };
  document.getElementById = id => elements[id];
  document.querySelector = () => back;
  document.body = {addEventListener() {}, classList: {remove() {}}};
  trigger.focus();
  const context = vm.createContext({document, window: {clearTimeout() {}}, htmx: {trigger: (...args) => events.push(args)}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/v3/legacy.js'), 'utf8'), context);
  const form = {querySelector: () => null, elements: {team: {value: 'Everton'}}, dataset: {fixtureLabel: 'Everton vs Wolves'}};
  const key = (key, shiftKey = false) => {
    const event = {key, shiftKey, prevented: false, preventDefault() { this.prevented = true; }};
    listeners.keydown(event);
    return event;
  };
  return {context, document, trigger, back, confirm, form, key, modal, events};
}

test('confirmation focuses Go Back and wraps keyboard focus in both directions', () => {
  const s = setup();
  s.context.openPickConfirm(s.form);
  assert.equal(s.document.activeElement, s.back);
  assert.equal(s.key('Tab', true).prevented, true);
  assert.equal(s.document.activeElement, s.confirm);
  assert.equal(s.key('Tab').prevented, true);
  assert.equal(s.document.activeElement, s.back);
});

test('Escape cancels without submitting and restores the triggering pick control', () => {
  const s = setup();
  s.context.openPickConfirm(s.form);
  s.key('Escape');
  assert.equal(s.modal.classList.contains('open'), false);
  assert.equal(s.document.activeElement, s.trigger);
  s.context.submitConfirmedPick();
  assert.equal(s.events.length, 0);
  assert.equal(s.key('Tab').prevented, false);
});

test('confirmation submits once through the existing HTMX event', () => {
  const s = setup();
  s.context.openPickConfirm(s.form);
  s.context.submitConfirmedPick();
  s.context.submitConfirmedPick();
  assert.deepEqual(s.events, [[s.form, 'confirmedPick']]);
  assert.equal(s.document.activeElement, s.trigger);
});
