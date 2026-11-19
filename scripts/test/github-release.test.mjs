import {test} from 'node:test';
import assert from 'node:assert/strict';
import {assertEditableRelease, publicAssetNames} from '../github-release.mjs';

test('public releases contain only system installers; source archives belong to the GitHub tag',()=>{
  assert.deepEqual(publicAssetNames('0.10.0'), ['Mumu-0.10.0-windows-x64-setup.exe','Mumu-0.10.0-linux-x64.AppImage','Mumu-0.10.0-linux-amd64.deb']);
  assert.equal(publicAssetNames('0.10.0').some(name => /\.(json|txt|md)$/.test(name)),false);
});

test('release reruns may update drafts but cannot overwrite published releases',()=>{
  assert.doesNotThrow(()=>assertEditableRelease(null));
  assert.doesNotThrow(()=>assertEditableRelease({isDraft:true}));
  assert.throws(()=>assertEditableRelease({isDraft:false}),/immutable/);
  assert.throws(()=>assertEditableRelease({}),/immutable/);
});
