const test = require('node:test');
const assert = require('node:assert');
const J = require('./public/ue-jsx.js');

const HELPERS = `function Page(props) {
  return <main className="page" data-theme="grove" style={{display:"flex", ...(props.style || {})}}>{props.children}</main>;
}
function Button(props) {
  return <button onClick={props.onClick} style={{...(props.style || {})}}>{props.children}</button>;
}
function Card(props) {
  return <div className="ds-card" style={{...(props.style || {})}}>{props.children}</div>;
}
`;

const SCREEN = HELPERS + `function RoomList() {
  return (
    <Page data-theme="grove">
      <Sidebar>
        <Button variant="nav" onClick={() => navigate("RoomList")}>Rooms</Button>
      </Sidebar>
      <div>
        <h1>Rooms</h1>
        <p>Don't double-book the hall.</p>
        <Card>
          <h2>Main hall</h2>
          <Button onClick={() => navigate("NewBooking")}>Book</Button>
        </Card>
        <Field name="date" label="Date" onChange={e => setDate(e.target.value)} />
      </div>
    </Page>
  );
}
`;

function stamped() {
  return J.stamp(SCREEN);
}

function idOf(src, needle) {
  const at = src.indexOf(needle);
  const m = /data-ue="(ue\d+)"/.exec(src.slice(src.lastIndexOf('<', at), at + needle.length + 40));
  return m && m[1];
}

test('stamp skips host helper bodies and is idempotent', () => {
  const src = stamped();
  const helperPart = src.slice(0, src.indexOf('function RoomList'));
  assert.ok(!/data-ue=/.test(helperPart), 'helpers must stay unstamped');
  assert.ok(/<h1 data-ue="ue\d+">Rooms/.test(src));
  assert.strictEqual(J.stamp(src), src);
  assert.strictEqual(J.normalize(src), SCREEN);
});

test('openTagEnd is not fooled by arrow functions in attributes', () => {
  const src = stamped();
  const lt = src.indexOf('<Button', src.indexOf('<Card'));
  const end = J.openTagEnd(src, lt);
  assert.ok(src.slice(lt, end + 1).endsWith('navigate("NewBooking")}>'));
});

test('setStyle on a button with onClick keeps the arrow intact', () => {
  const src = stamped();
  const id = idOf(src, 'onClick={() => navigate("NewBooking")}');
  const out = J.setStyle(src, id, { position: 'absolute', left: '120px', top: '40px', zIndex: '5' });
  assert.ok(out.includes('onClick={() => navigate("NewBooking")} style={{position:"absolute", left:"120px", top:"40px", zIndex:5}}>Book'));
  assert.deepStrictEqual(J.readStyle(out, id), { position: 'absolute', left: '120px', top: '40px', zIndex: '5' });
  assert.ok(J.isFree(out, id));
  const back = J.clearPosition(out, id);
  assert.strictEqual(back, src);
});

test('style values with commas survive a round trip', () => {
  const src = stamped();
  const id = idOf(src, '>Rooms</h1>');
  const once = J.setStyle(src, id, { fontFamily: 'Georgia, serif', width: '200px' });
  const twice = J.setStyle(once, id, { width: '240px' });
  assert.deepStrictEqual(J.readStyle(twice, id), { fontFamily: 'Georgia, serif', width: '240px' });
});

test('a button dragged out of a card can be moved back into it', () => {
  const src = stamped();
  const button = idOf(src, 'onClick={() => navigate("NewBooking")}');
  const card = idOf(src, '<Card');
  const main = idOf(src, '<div data-ue');
  const h1 = idOf(src, '>Rooms</h1>');
  const out = J.moveElement(src, button, main, h1);
  assert.ok(out, 'moved out');
  const cardSpan = J.elementSpan(out, J.findTag(out, card));
  assert.ok(!out.slice(cardSpan.start, cardSpan.end).includes('>Book<'), 'button left the card');
  assert.ok(out.indexOf('>Book<') < out.indexOf('>Rooms</h1>'));
  const back = J.moveElement(out, button, card, '');
  const cardBack = J.elementSpan(back, J.findTag(back, card));
  assert.ok(back.slice(cardBack.start, cardBack.end).includes('>Book</Button>'), 'button is inside the card again');
  assert.strictEqual(J.normalize(back).replace(/\s+/g, ' '), SCREEN.replace(/\s+/g, ' '));
});

test('moving into itself or a non-container is refused', () => {
  const src = stamped();
  const card = idOf(src, '<Card');
  const h2 = idOf(src, '>Main hall');
  assert.strictEqual(J.moveElement(src, card, h2, ''), null, 'cannot drop a card into its own child');
  assert.strictEqual(J.moveElement(src, card, card, ''), null);
  const field = idOf(src, '<Field');
  assert.strictEqual(J.moveElement(src, card, field, ''), null, 'self-closing tag is not a container');
});

test('moving next to its current place is a no-op', () => {
  const src = stamped();
  const h1 = idOf(src, '>Rooms</h1>');
  const p = idOf(src, "Don't");
  const main = idOf(src, '<div data-ue');
  assert.strictEqual(J.moveElement(src, h1, main, p), src);
});

test('empty studio wrappers are removed after a move', () => {
  const src = J.stamp(SCREEN.replace('<Field', '<div className="ue-place" style={{marginTop:"var(--space-md)"}}>\n          <Button>Extra</Button>\n        </div>\n        <Field'));
  const extra = idOf(src, '>Extra<');
  const card = idOf(src, '<Card');
  const out = J.moveElement(src, extra, card, '');
  assert.ok(out && !out.includes('ue-place'));
});

test('delete keeps the only h1 and removes other elements cleanly', () => {
  const src = stamped();
  const h1 = idOf(src, '>Rooms</h1>');
  assert.ok(J.removeElement(src, h1).refused);
  const p = idOf(src, "Don't");
  const out = J.removeElement(src, p);
  assert.ok(!out.refused && !out.src.includes("Don't"));
  assert.ok(!/\n\s*\n\s*<Card/.test(out.src), 'no blank line left behind');
});

test('duplicate copies without ids so they get fresh ones', () => {
  const src = stamped();
  const card = idOf(src, '<Card');
  const out = J.duplicateElement(src, card);
  assert.strictEqual((out.match(/<Card/g) || []).length, 2);
  const again = J.stamp(out);
  const ids = J.knownIds(again);
  assert.strictEqual(new Set(ids).size, ids.length, 'ids stay unique');
});

test('theme is read from the screen Page, not the helper', () => {
  const src = `function Page(props) {
  return <main className="page" data-theme="grove">{props.children}</main>;
}
function RoomList() {
  return <Page data-theme="blush"><h1>Rooms</h1></Page>;
}
`;
  assert.strictEqual(J.themeOf(src), 'blush');
  const vars = J.setPageVars(src, { '--color-accent': '#112233' });
  assert.ok(vars.includes('<Page data-theme="blush" style={{"--color-accent":"#112233"}}>'));
  assert.ok(!/<main className="page" data-theme="grove" style=/.test(vars));
});

test('an insert target with an arrow in its attributes still closes on the real tag', () => {
  const src = stamped().replace(
    '<div data-ue=',
    '<div onClick={() => navigate("RoomList")} data-ue='
  );
  const lt = src.indexOf('<div onClick');
  const span = J.elementSpan(src, lt);
  assert.ok(span && span.closeStart > lt);
  assert.ok(src.slice(span.closeStart, span.end).startsWith('</div>'));
  assert.ok(!src.slice(lt, span.openEnd).includes('</div>'));
});

test('motion and text edits stay on the selected element', () => {
  const src = stamped();
  const button = idOf(src, 'onClick={() => navigate("NewBooking")}');
  const moved = J.setMotion(src, button, 'motion-rise');
  assert.ok(moved.includes('motion="motion-rise"'));
  const card = idOf(src, '<Card');
  const titled = J.setElementText(src, card, 'ignored');
  assert.strictEqual(titled, null, 'a card with children is not a text node');
  const h1 = idOf(src, '>Rooms</h1>');
  assert.strictEqual(J.elementText(src, h1), 'Rooms');
  const renamed = J.setElementText(src, h1, 'Halls');
  assert.ok(renamed.includes('>Halls</h1>'));
  const field = idOf(src, '<Field');
  const labelled = J.setElementText(src, field, 'Starts');
  assert.ok(labelled.includes('label="Starts"'));
});

test('theme and page variables apply to a screen', () => {
  const themed = J.setTheme(SCREEN, 'blush');
  assert.strictEqual((themed.match(/data-theme="blush"/g) || []).length, 2);
  assert.strictEqual(J.themeOf(themed), 'blush');
  const vars = J.setPageVars(themed, { '--color-accent': '#ff3366', '--radius': '12px' });
  assert.ok(vars.includes('<Page data-theme="blush" style={{"--color-accent":"#ff3366", "--radius":"12px"}}>'));
  const changed = J.setPageVars(vars, { '--color-accent': '#00aa00' });
  assert.deepStrictEqual(J.pageVars(changed), { '--color-accent': '#00aa00', '--radius': '12px' });
  assert.strictEqual(J.setPageVars('function X(){return (<div/>);}', { '--radius': '1px' }), null);
});
