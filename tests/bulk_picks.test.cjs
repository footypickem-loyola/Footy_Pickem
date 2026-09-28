const test = require('node:test');
const assert = require('node:assert/strict');
const {createBulkDraft} = require('../static/v3/bulk_picks.js');
const fixtures = Array.from({length:10}, (_, i) => ({id:i + 1, home:`Home ${i}`, away:`Away ${i}`}));

test('available fixtures disappear on placement before team selection and return on clear', () => {
  const draft = createBulkDraft(fixtures);
  draft.select(1);
  assert.equal(draft.visibleFixtures().length, 10);
  draft.place(0);
  assert.equal(draft.slots[0].team, null);
  assert.equal(draft.visibleFixtures().some(f => f.id === 1), false);
  draft.filter = 'all';
  assert.equal(draft.visibleFixtures().length, 10);
  assert.equal(draft.position(1), 0);
  draft.clear(0);
  assert.equal(draft.position(1), -1);
  draft.filter = 'available';
  assert.equal(draft.visibleFixtures().length, 10);
});

test('replacing an assigned fixture restores the displaced fixture to available', () => {
  const draft = createBulkDraft(fixtures);
  draft.select(1); draft.place(0);
  draft.select(2); draft.place(0);
  assert.equal(draft.visibleFixtures().some(f => f.id === 1), true);
  assert.equal(draft.visibleFixtures().some(f => f.id === 2), false);
});

test('mid-draft list requires only undrafted fixtures and ignores drafted saved priorities', () => {
  const draft = createBulkDraft([{...fixtures[0], picked:true}, fixtures[1]], [
    {fixture_id:1, team:'Home 0'}]);
  assert.deepEqual(draft.slots, [null]);
  draft.select(1); draft.place(0);
  assert.deepEqual(draft.slots, [null]);
  draft.select(2); draft.place(0); draft.choose(0, 'Away 1');
  assert.equal(draft.complete(), true);
  assert.deepEqual(draft.payload(), [{fixture_id:2, team:'Away 1'}]);
  assert.equal(draft.visibleFixtures().length, 0);
  draft.filter = 'all';
  assert.equal(draft.visibleFixtures().length, 2);
});

test('placing a fixture still requires a deliberate team choice before review', () => {
  const draft = createBulkDraft(fixtures);
  draft.select(3);
  draft.place(0);
  assert.deepEqual(draft.slots[0], {fixture_id:3, team:null});
  draft.choose(0, 'Draw');
  assert.equal(draft.slots[0].team, null);
  assert.equal(draft.complete(), false);
  assert.throws(() => draft.payload());
});

test('moving an assigned fixture swaps slots without duplicates and retains teams', () => {
  const draft = createBulkDraft(fixtures);
  draft.select(1); draft.place(0); draft.choose(0, 'Home 0');
  draft.select(2); draft.place(1); draft.choose(1, 'Away 1');
  draft.select(1); draft.place(1);
  assert.deepEqual(draft.slots.slice(0,2), [{fixture_id:2,team:'Away 1'}, {fixture_id:1,team:'Home 0'}]);
  draft.move(1, -1);
  assert.equal(draft.slots[0].fixture_id, 1);
  draft.clear(0);
  assert.equal(draft.slots[0], null);
});

test('all ten choices produce an ordered independent confirmation payload', () => {
  const draft = createBulkDraft(fixtures);
  fixtures.forEach((fixture, i) => {
    draft.select(fixture.id); draft.place(9-i); draft.choose(9-i, fixture.home);
  });
  assert.equal(draft.complete(), true);
  const payload = draft.payload();
  assert.deepEqual(payload.map(p => p.fixture_id), [10,9,8,7,6,5,4,3,2,1]);
  payload[0].team = 'Tampered';
  assert.equal(draft.slots[0].team, 'Home 9');
});

test('existing priorities prefill only available valid fixtures without duplicates', () => {
  const draft = createBulkDraft(fixtures.slice(0,2), [
    {fixture_id:1,team:'Home 0'}, {fixture_id:1,team:'Home 0'},
    {fixture_id:3,team:'Home 2'}, {fixture_id:2,team:'Draw'}]);
  assert.deepEqual(draft.slots, [{fixture_id:1,team:'Home 0'}, null]);
});
