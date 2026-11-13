import {test} from 'node:test';
import assert from 'node:assert/strict';
import {assertEditableRelease} from '../github-release.mjs';

test('release reruns may update drafts but cannot overwrite published releases',()=>{
  assert.doesNotThrow(()=>assertEditableRelease(null));
  assert.doesNotThrow(()=>assertEditableRelease({draft:true}));
  assert.throws(()=>assertEditableRelease({draft:false}),/immutable/);
  assert.throws(()=>assertEditableRelease({}),/immutable/);
});
