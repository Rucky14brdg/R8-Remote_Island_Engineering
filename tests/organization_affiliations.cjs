const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {innerHTML:'',hidden:false,value:'',open:false,classList:{add(){},remove(){},toggle(){}},setAttribute(){},querySelectorAll(){return [];},addEventListener(){},showModal(){this.open=true;},close(){this.open=false;}});
  return elements.get(id);
}
const listeners = new Map();
const document={getElementById:element,querySelectorAll(){return [];},querySelector:element,addEventListener(type,handler){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(handler);}};
const context=vm.createContext({document,window:{addEventListener(){},scrollTo(){}},location:{hash:''},localStorage:{getItem(){return null;}},setTimeout,clearTimeout,console,URLSearchParams});
vm.runInContext(fs.readFileSync('static/vendor/markdown-it/markdown-it.min.js','utf8'),context);
vm.runInContext(fs.readFileSync('static/markdown.js','utf8'),context);
const source=fs.readFileSync('static/portfolio.js','utf8');
vm.runInContext(source.slice(0,source.lastIndexOf('\npaintIcons();')), context);
vm.runInContext(`
  data={entities:[
    {id:'大学',types:['Laboratory'],outgoing:[],literals:{}},
    {id:'研究室',types:['Laboratory'],outgoing:[{predicate:'所属する',object_id:'大学'}],literals:{}},
    {id:'下位組織',types:['Laboratory'],outgoing:[{predicate:'所属する',object_id:'研究室'}],literals:{}},
    {id:'個人',types:['User'],outgoing:[{predicate:'所属する',object_id:'研究室'}],literals:{表示名:['表示名<&>']}}
  ]};
`,context);
const html=vm.runInContext("organizationAffiliationSection(entity('研究室'))",context);
assert.match(html, /所属先の組織/);
assert.match(html, /大学/);
assert.match(html, /所属する組織/);
assert.match(html, /下位組織/);
assert.match(html, /所属メンバー/);
assert.match(html, /表示名&lt;&amp;&gt;/);
assert.doesNotMatch(html, /edit-organization-affiliations/);
vm.runInContext("openOrganizationAffiliations('研究室');",context);
assert.equal(element('profile-dialog').open,true);
assert.match(element('profile-dialog-content').innerHTML,/研究室が所属する組織/);
assert.match(element('profile-dialog-content').innerHTML,/大学/);

vm.runInContext(`
  data.users=[];data.articles=[];
  aiEntryLink=()=>'';prose=text=>esc(text);
  entity('研究室').wiki_content='研究室の詳しい紹介';
  entity('研究室').literals.概要=['研究室の概要'];
  entity('研究室').outgoing.push({predicate:'外部リンク',object_id:'https://example.org/'});
  renderEntity('研究室');
`,context);
assert.match(element('main').innerHTML,/組織情報を編集/);
assert.match(element('main').innerHTML,/研究室の詳しい紹介/);
assert.match(element('main').innerHTML,/https:\/\/example.org\//);
assert.ok(element('main').innerHTML.indexOf('所属関係')<element('main').innerHTML.indexOf('参加している研究・活動'));
vm.runInContext("search='';renderOrganizations();",context);
assert.match(element('main').innerHTML,/組織を追加/);
assert.match(element('main').innerHTML,/#entity\/%E7%A0%94%E7%A9%B6%E5%AE%A4/);
vm.runInContext("search='大学';renderOrganizations();",context);
assert.doesNotMatch(element('main').innerHTML,/研究室の概要/);
assert.match(element('main').innerHTML,/>研究室<\/h3><p class="fine">大学<\/p>/);
assert.doesNotMatch(element('main').innerHTML,/>下位組織<\/h3>/);
vm.runInContext("openOrganizationEditor('研究室');",context);
assert.match(element('profile-dialog-content').innerHTML,/organization-form/);
assert.match(element('profile-dialog-content').innerHTML,/研究室の概要/);
assert.match(element('profile-dialog-content').innerHTML,/研究室の概要\n\n研究室の詳しい紹介/);
assert.doesNotMatch(element('profile-dialog-content').innerHTML,/organization-description/);
assert.match(element('profile-dialog-content').innerHTML,/大学/);
vm.runInContext("openOrganizationEditor();",context);
assert.equal(vm.runInContext('profileDraft.entity_id',context),'');
assert.match(element('profile-dialog-content').innerHTML,/組織を追加/);
vm.runInContext("openOrganizationAffiliations('研究室');",context);
console.log('Organization list, detail hierarchy, introduction, links, and create/edit form: OK');

vm.runInContext(`
  entity('研究室').literals.概要=['## 活動紹介\\n\\n**電子工作**に取り組みます。\\n\\n- 設計\\n- 制作\\n\\n[危険なリンク](javascript:alert(1))'];
  entity('研究室').wiki_content='';
  renderEntity('研究室');
`,context);
assert.match(element('main').innerHTML,/<h2>活動紹介<\/h2>/);
assert.match(element('main').innerHTML,/<strong>電子工作<\/strong>/);
assert.match(element('main').innerHTML,/<li>設計<\/li>/);
assert.doesNotMatch(element('main').innerHTML,/href="javascript:/);
vm.runInContext("openOrganizationEditor('研究室');",context);
assert.match(element('profile-dialog-content').innerHTML,/Markdown対応/);
assert.match(element('profile-dialog-content').innerHTML,/organization-summary-preview/);
const summaryInput={id:'organization-summary',value:'## 更新した概要',matches(){return false;},closest(){return null;}};
for(const handler of listeners.get('input')||[])handler({target:summaryInput});
assert.match(element('organization-summary-preview').innerHTML,/<h2>更新した概要<\/h2>/);
console.log('Organization summary Markdown display, safe links and live preview: OK');

// 自己所属・循環する下位組織を、候補・手入力・新規候補から除外する。
vm.runInContext(`
  openOrganizationEditor('研究室');
  entity('研究室').literals.タイトル=['表示用の研究室名'];
`,context);
assert.deepEqual(JSON.parse(vm.runInContext("JSON.stringify(chipCandidates('affiliations').map(e=>e.id))",context)),['大学']);
assert.equal(vm.runInContext("invalidOrganizationAffiliation('表示用の研究室名',profileDraft)",context),true);
assert.equal(vm.runInContext("invalidOrganizationAffiliation(':研究室',profileDraft)",context),true);
assert.equal(vm.runInContext("invalidOrganizationAffiliation('下位組織',profileDraft)",context),true);
assert.equal(vm.runInContext("invalidOrganizationAffiliation('大学',profileDraft)",context),false);
vm.runInContext("suggest('affiliations','表示用の研究室名');",context);
assert.doesNotMatch(element('suggest-affiliations').innerHTML,/suggestion-new/);
vm.runInContext(`
  var originalNotify=notify;notify=()=>{};
  addChips('affiliations','研究室');addChips('affiliations','下位組織');
  notify=originalNotify;
`,context);
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(profileDraft.affiliations)',context)),['大学']);
// 個人の所属候補には、組織を引き続き選べる。
assert.equal(vm.runInContext("chipCandidates('affiliations',{entity_id:'個人'}).length",context),3);
console.log('Self and descendant affiliation candidates and manual additions are blocked: OK');

// 日本語の変換確定Enterは追加に使わず、その次のEnterで一度だけ追加する。
vm.runInContext('var originalAddChips=addChips;var additions=[];addChips=(key,value)=>additions.push({key,value});',context);
const chipInput={value:'弓削商船',dataset:{chipInput:'affiliations'},matches:selector=>selector==='[data-chip-input]'};
let prevented=0;
function enter(properties={}){
  for(const handler of listeners.get('keydown')||[])handler({target:chipInput,key:'Enter',preventDefault(){prevented++;},...properties});
}
enter({isComposing:true,keyCode:13});
enter({isComposing:false,keyCode:229}); // Safari等の変換確定時にも対応。
assert.equal(vm.runInContext('additions.length',context),0);
assert.equal(prevented,0);
enter({isComposing:false,keyCode:13});
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(additions)',context)),[{key:'affiliations',value:'弓削商船'}]);
assert.equal(prevented,1);
vm.runInContext('addChips=originalAddChips;',context);
console.log('Japanese IME confirmation does not add chips; subsequent Enter adds once: OK');

(async()=>{
  vm.runInContext(`
    var captured;
    request=async(url,payload)=>{captured={url,payload};};
    load=async()=>{};route=()=>{};notify=()=>{};
    profileDraft.affiliations=['大学'];
  `,context);
  element('chip-affiliations').value='連携組織';
  await vm.runInContext('saveOrganizationAffiliations({preventDefault(){}})',context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(captured)',context)),{
    url:'/api/entity',payload:{entity_id:'研究室',entity_class:'Laboratory',relations:[{predicate:'所属する',object:'大学'},{predicate:'所属する',object:'連携組織'}],replace_predicates:['所属する']}
  });
  assert.equal(element('profile-dialog').open,false);
  vm.runInContext("openOrganizationAffiliations('研究室');profileDraft.affiliations=[];",context);
  element('chip-affiliations').value='';
  await vm.runInContext('saveOrganizationAffiliations({preventDefault(){}})',context);
  assert.equal(vm.runInContext('captured.payload.relations.length',context),0);
  vm.runInContext("openOrganizationAffiliations('研究室');request=async()=>{throw new Error('組織の所属が循環しています');};",context);
  await vm.runInContext('saveOrganizationAffiliations({preventDefault(){}})',context);
  assert.equal(element('profile-dialog').open,true);
  assert.match(element('organization-affiliations-error').textContent,/循環/);
  assert.equal(element('save-organization-affiliations').disabled,false);
  console.log('Organization affiliation display, editing, clearing, and error recovery: OK');
  vm.runInContext(`
    request=async(url,payload)=>{captured={url,payload};return {entity_id:'研究室'};};
    openOrganizationEditor('研究室');
  `,context);
  const organizationFields=[
    {dataset:{organizationKey:'name'},value:'変更した組織名'},
    {dataset:{organizationKey:'summary'},value:'変更した概要'},
    {dataset:{organizationKey:'external_links'},value:'https://example.org/one\nhttps://example.org/two'}
  ];
  document.querySelectorAll=selector=>selector==='#organization-form [data-organization-key]'?organizationFields:[];
  element('chip-affiliations').value='連携団体';
  await vm.runInContext('saveOrganization({preventDefault(){}})',context);
  const saved=JSON.parse(vm.runInContext('JSON.stringify(captured)',context));
  assert.equal(saved.url,'/api/portfolio/organization');
  assert.equal(saved.payload.entity_id,'研究室');
  assert.equal(saved.payload.name,'変更した組織名');
  assert.equal(saved.payload.summary,'変更した概要');
  assert.equal(saved.payload.description,'');
  assert.deepEqual(saved.payload.affiliations,['大学','連携団体']);
  assert.deepEqual(saved.payload.external_links,['https://example.org/one','https://example.org/two']);
  assert.equal(context.location.hash,'#entity/'+encodeURIComponent('研究室'));
  assert.equal(element('profile-dialog').open,false);
  vm.runInContext("openOrganizationEditor('研究室');request=async()=>{throw new Error('保存エラー');};",context);
  await vm.runInContext('saveOrganization({preventDefault(){}})',context);
  assert.equal(element('profile-dialog').open,true);
  assert.equal(element('organization-error').textContent,'保存エラー');
  assert.equal(element('save-organization').disabled,false);
  console.log('Organization create/edit payload, navigation, and failed-save recovery: OK');
  vm.runInContext("openOrganizationEditor('研究室');var requestCount=0;request=async()=>{requestCount++;return {entity_id:'研究室'};};",context);
  element('chip-affiliations').value='下位組織';
  await vm.runInContext('saveOrganization({preventDefault(){}})',context);
  assert.equal(vm.runInContext('requestCount',context),0);
  assert.match(element('organization-error').textContent,/自分自身や下位/);
  assert.equal(element('profile-dialog').open,true);
  console.log('Pending invalid affiliation is rejected before save: OK');


})().catch(error=>{console.error(error);process.exitCode=1;});
