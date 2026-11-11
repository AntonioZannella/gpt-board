import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pageLinkAt, safeWebLink, isLinkTap } from '../src/pdf-links.js';
const store = () => ({
  a: { id:'a', typeName:'shape', type:'frame', x:100, y:200, props:{w:1000,h:1200} },
  b: { id:'b', typeName:'shape', type:'frame', props:{w:1000,h:1200} },
  image: {id:'image', typeName:'shape', type:'image', parentId:'a', x:0,y:0,
    props:{w:1000,h:1200}, meta:{pdfLinks:[{rect:[.1,.1,.3,.05],frame:'b'}]}},
});
test('hit targets follow image coordinates and stable page IDs', () => {
  const s=store();
  assert.deepEqual(pageLinkAt(s,'a',{x:250,y:340}),{frame:'b'});
  s.b.meta={order:99};
  assert.deepEqual(pageLinkAt(s,'a',{x:250,y:340}),{frame:'b'});
  assert.equal(pageLinkAt(s,'a',{x:99,y:340}),null);
  s.b.meta.trashed=true;
  assert.equal(pageLinkAt(s,'a',{x:250,y:340}),null);
});
test('unsupported schemes, malformed bounds and missing destinations do not navigate',()=>{
  for(const u of ['javascript:alert(1)','file:///private/a','data:text/html,bad','/relative'])
    assert.equal(safeWebLink(u),null);
  assert.equal(safeWebLink('https://example.com'), 'https://example.com/');
  const s=store();s.image.meta.pdfLinks=[{rect:[.1,.1,NaN,.05],url:'https://example.com'}];
  assert.equal(pageLinkAt(s,'a',{x:250,y:340}),null);
});
test('drag out and back, long presses and canceled timings are not taps',()=>{
  const p={x:0,y:0};
  assert.equal(isLinkTap(p,{x:3,y:4},5,250),true);
  assert.equal(isLinkTap(p,p,30,250),false);
  assert.equal(isLinkTap(p,p,0,800),false);
  assert.equal(isLinkTap(p,p,0,-1),false);
});
test('image crop and flips preserve PDF coordinates',()=>{
  const s=store();s.image.props.crop={topLeft:{x:.1,y:.1},bottomRight:{x:.4,y:.15}};
  assert.deepEqual(pageLinkAt(s,'a',{x:600,y:800}),{frame:'b'});
  s.image.props.flipX=true;
  assert.deepEqual(pageLinkAt(s,'a',{x:600,y:800}),{frame:'b'});
});
