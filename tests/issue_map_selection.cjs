const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const elements=new Map(),markers=[],maps=[];
let activePopup=null;
function el(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',hidden:false,classList:{toggle(){},remove(){}},setAttribute(){},querySelectorAll(){return [];},addEventListener(){}});return elements.get(id);}
const document={getElementById:el,querySelector:()=>el('generic'),querySelectorAll:()=>[],addEventListener(){}};
const L={
  map(){const map={events:{},setView(){return this;},fitBounds(){return this;},on(name,fn){this.events[name]=fn;return this;},closePopup(){activePopup=null;return this;},remove(){activePopup=null;}};maps.push(map);return map;},
  tileLayer(){return {addTo(){return this;},on(){return this;}};},
  divIcon(options){return options;},
  latLngBounds(){return {pad(){return this;}};},
  marker(point,options){const attributes={},classes={};const marker={point,options,events:{},attributes,classes,
    addTo(){return this;},bindPopup(content){this.popup=content;return this;},openPopup(){activePopup=this;return this;},bindTooltip(){throw new Error('Persistent place labels must not be bound');},
    getElement(){return {setAttribute(k,v){attributes[k]=v;},classList:{toggle(k,v){classes[k]=v;}}};},
    on(name,fn){this.events[name]=fn;return this;}};markers.push(marker);return marker;}
};
const context=vm.createContext({document,L,window:{L,addEventListener(){},scrollTo(){}},location:{hash:'#issues/map'},localStorage:{getItem(){return null;}},setTimeout,clearTimeout,console});
const portfolio=fs.readFileSync('static/portfolio.js','utf8');
vm.runInContext(portfolio.slice(0,portfolio.lastIndexOf('\npaintIcons();')),context);
vm.runInContext(fs.readFileSync('static/issues.js','utf8'),context);
const nursery={id:'nursery',title:'保育所',latitude:34.25,longitude:133.19};
const school={id:'school',title:'弓削商船高等専門学校',latitude:34.26,longitude:133.2};
// 同じ座標でも別の地点IDに紐づく課題を混ぜない。
const point={id:'target_abc',title:'',latitude:34.25,longitude:133.19};
const zero={id:'zero',title:'困りごとのない施設',latitude:34.27,longitude:133.21};
const issue=(id,title,targets)=>({id,title,targets,research:[],description:'説明',status:'未整理',category:'',reporter_role:''});
const items=[issue('a','駐車場に迷う',[nursery]),issue('b','送迎の時間',[nursery]),issue('c','学校の困りごと',[school]),issue('d','地点だけの困りごと',[point]),issue('e','両施設の困りごと',[nursery,school]),issue('f','場所未設定',[])];
context.fixture={items,places:[nursery,school,point,zero]};
vm.runInContext(`data.entities=fixture.places.map(p=>({id:p.id,types:['Target'],outgoing:[],literals:{タイトル:p.title?[p.title]:[],緯度:[String(p.latitude)],経度:[String(p.longitude)]}}));issueItems=fixture.items;issueView='map';drawIssueResults();`,context);
assert.equal(el('issue-map-selection').innerHTML,'');
assert.doesNotMatch(el('issue-results').innerHTML,/場所を選ぶ|選択した場所|issue-map-place/);
assert.equal(activePopup,null);
function clickPlace(id){const marker=[...markers].reverse().find(m=>m.options.title.startsWith(id+':')||m.options.title.startsWith(id+'：'));assert(marker,'marker '+id);marker.events.click();return marker;}
const selected=clickPlace('保育所');
assert.match(el('issue-map-selection').innerHTML,/駐車場に迷う/);
assert.match(el('issue-map-selection').innerHTML,/送迎の時間/);
assert.match(el('issue-map-selection').innerHTML,/両施設の困りごと/);
assert.doesNotMatch(el('issue-map-selection').innerHTML,/学校の困りごと|地点だけの困りごと|場所未設定/);
assert.equal((el('issue-map-selection').innerHTML.match(/<article /g)||[]).length,3);
assert.equal(selected.attributes['aria-pressed'],'true');
assert.equal(selected.options.bubblingMouseEvents,false);
assert.equal(activePopup,selected);
assert.match(selected.popup,/<a class="place-popup-link" href="#entity\/nursery">保育所<\/a>/);
assert.doesNotMatch(el('issue-map-selection').innerHTML,/選択した場所|場所の詳細/);
clickPlace('弓削商船高等専門学校');
assert.equal((el('issue-map-selection').innerHTML.match(/<article /g)||[]).length,2);
assert.doesNotMatch(el('issue-map-selection').innerHTML,/駐車場に迷う|送迎の時間/);
clickPlace('指定した地点');
assert.match(el('issue-map-selection').innerHTML,/地点だけの困りごと/);
assert.equal((el('issue-map-selection').innerHTML.match(/<article /g)||[]).length,1);
clickPlace('困りごとのない施設');
assert.equal(el('issue-map-selection').innerHTML,'');
assert.doesNotMatch(el('issue-map-selection').innerHTML,/community-note note-/);
vm.runInContext("selectIssueMapPlace('nursery');issueQuery='送迎';drawIssueResults();",context);
assert.equal(vm.runInContext('issueMapSelectedPlaceId',context),'nursery');
assert.match(el('issue-map-selection').innerHTML,/送迎の時間/);
assert.doesNotMatch(el('issue-map-selection').innerHTML,/駐車場に迷う|学校の困りごと/);
assert.equal((el('issue-map-selection').innerHTML.match(/<article /g)||[]).length,1);
vm.runInContext("issueQuery='該当しない語';drawIssueResults();",context);
assert.match(el('issue-results').innerHTML,/id="issues-map"/);
assert.equal(el('issue-map-selection').innerHTML,'');
vm.runInContext("issueQuery='';drawIssueResults();",context);
clickPlace('保育所');
assert.equal((el('issue-map-selection').innerHTML.match(/<article /g)||[]).length,3);
maps.at(-1).events.click();
assert.equal(el('issue-map-selection').innerHTML,'');
assert.equal(vm.runInContext('issueMapSelectedPlaceId',context),'');
assert.equal(activePopup,null);
for(const marker of vm.runInContext('[...issueMapMarkers.values()]',context))assert.equal(marker.attributes['aria-pressed'],'false');
clickPlace('指定した地点');
assert.equal((el('issue-map-selection').innerHTML.match(/<article /g)||[]).length,1);
assert.match(activePopup.popup,/>指定した地点<\/a>/);
vm.runInContext("selectIssueMapPlace('missing');",context);
assert.equal(el('issue-map-selection').innerHTML,'');
assert.equal(activePopup,null);
console.log('Map interactions: pins without persistent labels, linked place-name popup, place-specific notes, single-point note, filters, empty background reset and re-selection: OK');
