import {test} from 'node:test';
import assert from 'node:assert/strict';
import {pageNumbers} from '../src/file-options.js';
test('export ranges preserve order, deduplicate, and reject invalid bounds',()=>{
 assert.deepEqual(pageNumbers('3-5, 1, 4',8),[2,3,4,0]);
 assert.equal(pageNumbers('all',185).length,185);
 for(const invalid of ['0','2-1','186','1,,2','1-500000000']) assert.throws(()=>pageNumbers(invalid,185));
});
