const test = require('node:test');
const assert = require('node:assert/strict');
const {layoutForest} = require('../../app/static/js/neostaffing_org_layout.js');
const person = (id, children = [], height = 140) => ({id, width:220, height, children});

test('supervisor above horizontally aligned direct reports with canonical connectors', () => {
    const root = person('division', [person('manager-a'), person('manager-b')]);
    const result = layoutForest([root], 900);
    const [parent, left, right] = result.nodes;
    assert.equal(left.y, right.y);
    assert.ok(parent.y + parent.height < left.y);
    assert.ok(left.x + left.width < right.x);
    assert.equal(parent.x + parent.width/2, (left.x + right.x + right.width)/2);
    assert.equal(result.links[0].parent.source, root);
    assert.deepEqual(result.links[0].children.map(child => child.source.id), ['manager-a','manager-b']);
});

test('different card heights and uneven subtrees share the same reporting levels', () => {
    const root = person('root', [person('a',[person('c')],240), person('b',[person('d'),person('e')],100)]);
    const result = layoutForest([root]);
    for (let depth=0; depth<3; depth++) {
        const row = result.nodes.filter(node=>node.depth===depth);
        assert.equal(new Set(row.map(node=>node.y)).size, 1);
        for (const node of row) if (node.parent) assert.ok(node.y >= node.parent.y + node.parent.height + 48);
    }
});

test('multiple roots remain separate and small forests are centered', () => {
    const roots = [person('root-a',[person('a')]), person('root-b',[person('b')])];
    const result = layoutForest(roots, 1440);
    assert.equal(result.width, 1440);
    assert.equal(result.nodes.filter(node=>node.parent===null).length, 2);
    assert.deepEqual(result.links.map(link=>link.parent.source.id), ['root-a','root-b']);
    assert.equal(result.nodes[0].x, 1440 - (result.nodes[2].x + 220));
});

test('large forests retain readable cards without overlaps at desktop or mobile widths', () => {
    const roots = Array.from({length:4}, (_,r)=>person(`root-${r}`,
        Array.from({length:12},(_,m)=>person(`manager-${r}-${m}`,
            Array.from({length:20},(_,p)=>person(`report-${r}-${m}-${p}`))))));
    for (const viewport of [1440,390]) {
        const result = layoutForest(roots,viewport);
        assert.equal(result.nodes.length,1012);
        assert.ok(result.width>viewport);
        for (let depth=0;depth<3;depth++) {
            const row=result.nodes.filter(node=>node.depth===depth).sort((a,b)=>a.x-b.x);
            for (let i=1;i<row.length;i++) assert.ok(row[i].x>=row[i-1].x+row[i-1].width+24);
        }
        assert.ok(result.nodes.every(node=>node.width===220 && node.x>=24 && node.y>=24));
    }
});

test('layout does not mutate data or infer edges from operational assignments', () => {
    const roots=[person('a',[person('actual-report')]), person('same-work-area')];
    roots.forEach(root=>{root.assignment='same department';});
    const before=JSON.stringify(roots), result=layoutForest(roots);
    assert.equal(JSON.stringify(roots),before);
    assert.equal(result.links.length,1);
    assert.deepEqual(result.links[0].children.map(child=>child.source.id),['actual-report']);
});
